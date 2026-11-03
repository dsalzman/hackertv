"""Build a schedule *edition*: the channel lineup the browser schedules from.

    python3 scripts/build_channels.py [--from next-monday|epoch|now|<unix ts>]

Reads data/videos.jsonl (falls back to the HackerTube seed) + data/fresh.jsonl
+ data/youtube_meta.jsonl, and writes public/data/e/<from>/ch/<id>.json plus
public/data/lineup.json.

Editions: changing a channel's contents reshuffles its whole schedule, so new
data never goes live mid-week. Each build becomes an edition that starts at the
next Monday 00:00 UTC; the browser switches every viewer over at that instant.
lineup.json keeps the edition on air now plus any upcoming ones.

Without youtube_meta.jsonl, durations are *estimated* from content_type (preview only).
"""
import json
import os
import re
import shutil
import sys
import time
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hnlib import P, read_jsonl  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "data")
EPOCH = 1767225600        # 2026-01-01 00:00 UTC, the first edition

MAX_DUR = 3 * 3600        # skip 3h+ livestream archives on normal channels
MIN_DUR = 30              # skip shorts/glitches
LATE_MIN = 50 * 60        # late night = long-form only
PRIME_SIZE = 400

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
    rows = read_jsonl(P["yt_meta"])
    if not rows:
        return None
    meta = {}
    for r in rows:
        it = r["item"]
        ok = bool(it) and (it.get("status", {}).get("embeddable", False)
                           and it.get("status", {}).get("privacyStatus") == "public"
                           and it.get("snippet", {}).get("liveBroadcastContent", "none") == "none")
        meta[r["vid"]] = iso_dur(it.get("contentDetails", {}).get("duration")) if ok else None
    return meta


def edition_start(arg, now):
    if arg == "epoch":
        return EPOCH
    if arg == "now":
        return int(now)
    if arg == "next-monday":
        d = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
        monday = d + dt.timedelta(days=(7 - d.weekday()) % 7 or 7)
        return int(dt.datetime.combine(monday, dt.time(), dt.timezone.utc).timestamp())
    return int(arg)


def to_item(v, dur, quote=None):
    q = quote if quote is not None else (v.get("quote") or {})
    return {"v": v["vid"], "d": dur, "t": v["title"], "c": v["channel"],
            "q": q.get("text"), "y": q.get("year"), "h": q.get("hn_comment_id"),
            "_type": v["content_type"], "_area": v["categories"][0]["area"] if v["categories"] else None,
            "_score": v["score"]}


def build_channels(meta):
    estimated = meta is None
    src = P["videos"] if os.path.exists(P["videos"]) else P["seed_videos"]
    videos = read_jsonl(src)

    def duration(v):
        if estimated:
            return EST.get(v["content_type"], 900)
        d = meta.get(v["vid"])
        return d if d and d >= MIN_DUR else None

    pool, dropped = [], 0
    for v in videos:
        if v["shelf_life"] == "news":
            continue
        d = duration(v)
        if not d:
            dropped += 1
            continue
        pool.append(to_item(v, d))

    channels = []

    def add(num, cid, name, tagline, items, **extra):
        items = [i for i in items if i["d"] <= MAX_DUR or cid == "latenight"]
        channels.append({"num": num, "id": cid, "name": name, "tagline": tagline, "items": items, **extra})

    add(2, "prime", "HN PRIME", "The all-time most recommended videos on Hacker News",
        sorted(pool, key=lambda x: -x["_score"])[:PRIME_SIZE])
    for area, (num, name, tag) in AREAS.items():
        add(num, area, name, tag, [p for p in pool if p["_area"] == area])

    fresh = []
    for v in read_jsonl(P["fresh"]):
        d = duration(v)
        if d:
            fresh.append(to_item(v, d))
    if fresh:
        add(19, "new", "HN NEW", "What Hacker News started recommending this month", fresh)

    add(24, "music", "HN MUSIC", "Music videos and performances HN keeps sharing",
        [p for p in pool if p["_type"] == "music"])
    add(99, "latenight", "LATE NIGHT", "Long lectures & talks. On air 11pm-5am your time",
        [p for p in pool if p["_type"] in ("lecture", "talk", "interview", "podcast", "documentary", "course")
         and p["d"] >= LATE_MIN],
        hours=[23, 5])
    return channels, estimated, dropped


def load_editions():
    path = os.path.join(OUT, "lineup.json")
    if not os.path.exists(path):
        return []
    lineup = json.load(open(path))
    if "editions" in lineup:
        return lineup["editions"]
    # v1 layout (single schedule in public/data/ch) -> make it the first edition
    old_dir = os.path.join(OUT, "ch")
    new_dir = os.path.join(OUT, "e", str(EPOCH), "ch")
    if os.path.isdir(old_dir):
        os.makedirs(os.path.dirname(new_dir), exist_ok=True)
        shutil.move(old_dir, new_dir)
    return [{"from": EPOCH, "path": f"e/{EPOCH}", "estimated": lineup.get("estimated", False),
             "channels": lineup["channels"]}]


def main():
    arg = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "next-monday"
    now = time.time()
    start = edition_start(arg, now)
    meta = load_meta()
    channels, estimated, dropped = build_channels(meta)

    editions = [e for e in load_editions() if e["from"] != start]
    rel = f"e/{start}"
    edir = os.path.join(OUT, rel, "ch")
    if os.path.exists(edir):
        shutil.rmtree(edir)
    os.makedirs(edir)
    lineup_channels = []
    for ch in channels:
        items = [{k: val for k, val in i.items() if not k.startswith("_") and val is not None}
                 for i in ch.pop("items")]
        total = sum(i["d"] for i in items)
        with open(os.path.join(edir, f"{ch['id']}.json"), "w") as f:
            json.dump({"id": ch["id"], "total": total, "videos": items}, f, ensure_ascii=False, separators=(",", ":"))
        lineup_channels.append({**ch, "count": len(items), "total": total})
        print(f"  {ch['num']:>3} {ch['name']:<10} {len(items):>5} videos  {total / 3600:7.1f} h loop")
    editions.append({"from": start, "path": rel, "built": int(now), "estimated": estimated,
                     "channels": lineup_channels})
    editions.sort(key=lambda e: e["from"])

    # keep the edition on air now (latest with from <= now) and every upcoming one
    live = [e for e in editions if e["from"] <= now]
    keep = live[-1:] + [e for e in editions if e["from"] > now]
    keep_paths = {e["path"] for e in keep}
    for e in editions:
        if e["path"] not in keep_paths:
            shutil.rmtree(os.path.join(OUT, e["path"]), ignore_errors=True)

    with open(os.path.join(OUT, "lineup.json"), "w") as f:
        json.dump({"version": 2, "editions": keep}, f, indent=1)
    when = dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat()
    print(("ESTIMATED durations" if estimated else f"real durations; dropped {dropped} unavailable")
          + f"; edition starts {when}; {len(keep)} edition(s) in lineup")


if __name__ == "__main__":
    main()
