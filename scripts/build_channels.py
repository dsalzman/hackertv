"""Build the static channel lineup the browser schedules from.

Usage:  python3 scripts/build_channels.py

Reads data/raw/videos.jsonl (+ data/youtube_meta.jsonl if present) and writes
public/data/lineup.json and public/data/ch/<id>.json.

Without youtube_meta.jsonl, durations are *estimated* from content_type so you
can preview the site; the player then shows "please stand by" when a video ends
before its slot does. Run fetch_youtube_meta.py for a real schedule.

Videos are stored in a canonical order; the browser re-shuffles each loop of a
channel with a seeded PRNG, so every viewer computes the same schedule.
"""
import json
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
META = os.path.join(ROOT, "data", "youtube_meta.jsonl")
OUT = os.path.join(ROOT, "public", "data")

MAX_DUR = 3 * 3600        # skip 3h+ livestream archives on normal channels
MIN_DUR = 30              # skip shorts/glitches
LATE_MIN = 50 * 60        # late night = long-form only
PRIME_SIZE = 400

# Rough guesses used only when real durations are missing.
EST = {"music": 240, "comedy": 480, "demo": 600, "news": 600, "explainer": 900,
       "other": 900, "commentary": 1200, "tutorial": 1500, "course": 1200,
       "talk": 2700, "documentary": 3000, "lecture": 3600, "interview": 3600,
       "podcast": 4800}

# area id -> (channel number, call sign, tagline)
AREAS = {
    "software":                  (3,  "DEVTV",   "Software engineering & languages"),
    "systems-security":          (4,  "SYSNET",  "Systems, networks & security"),
    "ai-data":                   (5,  "NEURAL",  "AI, machine learning & data"),
    "math-theory":               (6,  "PROOF",   "Mathematics & theoretical CS"),
    "hardware":                  (7,  "SOLDER",  "Hardware & electronics"),
    "computing-history-culture": (8,  "RETRO",   "Computing history & culture"),
    "design-graphics-media":     (9,  "PIXEL",   "Design, graphics & visual media"),
    "business-economics":        (10, "YC-SPAN", "Business, startups & economics"),
    "politics-history-society":  (11, "HISTORY", "Politics, history & society"),
    "physics-space":             (12, "ORBIT",   "Physics, space & chemistry"),
    "life-sciences-health":      (13, "BIO",     "Life sciences, mind & health"),
    "earth-energy-environment":  (14, "TERRA",   "Earth, energy & environment"),
    "engineering-making":        (15, "MAKER",   "Engineering, transport & making"),
    "games":                     (16, "PLAYER1", "Games & game development"),
    "arts-entertainment":        (17, "MARQUEE", "Arts, comedy, music & film"),
    "ideas-education":           (18, "IDEAS",   "Ideas, learning & language"),
}


def iso_dur(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def load_meta():
    if not os.path.exists(META):
        return None
    meta = {}
    for line in open(META):
        r = json.loads(line)
        it = r["item"]
        if not it:
            meta[r["vid"]] = None
            continue
        ok = (it.get("status", {}).get("embeddable", False)
              and it.get("status", {}).get("privacyStatus") == "public"
              and it.get("snippet", {}).get("liveBroadcastContent", "none") == "none")
        meta[r["vid"]] = iso_dur(it.get("contentDetails", {}).get("duration")) if ok else None
    return meta


def main():
    videos = [json.loads(l) for l in open(os.path.join(RAW, "videos.jsonl"))]
    meta = load_meta()
    estimated = meta is None

    pool, dropped = [], 0
    for v in videos:
        if v["shelf_life"] == "news":
            continue
        if estimated:
            dur = EST.get(v["content_type"], 900)
        else:
            dur = meta.get(v["vid"])
            if not dur or dur < MIN_DUR:
                dropped += 1
                continue
        q = v.get("quote") or {}
        pool.append({
            "v": v["vid"], "d": dur, "t": v["title"], "c": v["channel"],
            "q": q.get("text"), "y": q.get("year"), "h": q.get("hn_comment_id"),
            "_type": v["content_type"], "_area": v["categories"][0]["area"] if v["categories"] else None,
            "_score": v["score"],
        })

    channels = []

    def add(num, cid, name, tagline, items, **extra):
        items = [i for i in items if i["d"] <= MAX_DUR or cid == "latenight"]
        channels.append({"num": num, "id": cid, "name": name, "tagline": tagline,
                         "items": items, **extra})

    add(2, "prime", "HN PRIME", "The all-time most recommended videos on Hacker News",
        sorted(pool, key=lambda x: -x["_score"])[:PRIME_SIZE])
    for area, (num, name, tag) in AREAS.items():
        add(num, area, name, tag, [p for p in pool if p["_area"] == area])
    add(24, "music", "HN MUSIC", "Music videos and performances HN keeps sharing",
        [p for p in pool if p["_type"] == "music"])
    add(99, "latenight", "LATE NIGHT", "Long lectures & talks. On air 11pm-5am your time",
        [p for p in pool if p["_type"] in ("lecture", "talk", "interview", "podcast", "documentary", "course")
         and p["d"] >= LATE_MIN],
        hours=[23, 5])

    if os.path.exists(os.path.join(OUT, "ch")):
        shutil.rmtree(os.path.join(OUT, "ch"))
    os.makedirs(os.path.join(OUT, "ch"))
    lineup = []
    for ch in channels:
        items = [{k: val for k, val in i.items() if not k.startswith("_") and val is not None}
                 for i in ch.pop("items")]
        total = sum(i["d"] for i in items)
        with open(os.path.join(OUT, "ch", f"{ch['id']}.json"), "w") as f:
            json.dump({"id": ch["id"], "total": total, "videos": items}, f,
                      ensure_ascii=False, separators=(",", ":"))
        lineup.append({**ch, "count": len(items), "total": total})
        print(f"  {ch['num']:>3} {ch['name']:<10} {len(items):>5} videos  {total / 3600:7.1f} h loop")

    with open(os.path.join(OUT, "lineup.json"), "w") as f:
        json.dump({"estimated": estimated, "channels": lineup}, f, indent=1)
    print(("ESTIMATED durations (no youtube_meta.jsonl)" if estimated
           else f"real durations; dropped {dropped} deleted/unembeddable") + f" -> {OUT}")


if __name__ == "__main__":
    main()
