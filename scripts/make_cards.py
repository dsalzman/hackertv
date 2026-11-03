"""Merge the HackerTube seed with our newer labeled mentions.

    python3 scripts/make_cards.py [--no-llm]

- Seed videos get their HN stats and score bumped by mentions newer than the seed.
- Non-seed videos that reach HackerTube's bar (3+ people, 3+ different months) get a card
  written by Claude and join the catalog.
- Videos recommended on HN in the last ~30 days get a card too and go to the NEW channel.

Writes data/videos.jsonl (catalog) and data/fresh.jsonl (NEW channel), both read by
build_channels.py. Needs data/youtube_meta.jsonl (titles, descriptions, availability).
"""
import sys
import time
from collections import defaultdict

from hnlib import P, append_jsonl, read_json, read_jsonl, write_jsonl

REC = ("rec", "strong_rec")
FRESH_WINDOWS = (30, 60, 90)     # days; widen until the NEW channel has enough videos
FRESH_MIN = 40


def group(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["vid"]].append(r)
    return g


def best_quote(ms):
    for want in ("strong_rec", "rec"):
        for m in sorted(ms, key=lambda m: -len(m.get("quote") or "")):
            if m["stance"] == want and m.get("quote") and not m.get("self_promo"):
                return {"text": m["quote"], "hn_comment_id": m["hn_comment_id"], "year": m["month"][:4]}
    return None


def stats(ms):
    honest = [m for m in ms if not m.get("self_promo")]
    recs = [m for m in honest if m["stance"] in REC]
    stances = defaultdict(int)
    for m in ms:
        stances[m["stance"]] += 1
    years = [int(m["month"][:4]) for m in ms]
    return {
        "mentions": len(ms),
        "recommenders": len({m["author"] for m in recs}),
        "rec_months": len({m["month"] for m in recs}),
        "strong_recs": sum(m["stance"] == "strong_rec" for m in honest),
        "stances": dict(stances),
        "first_year": min(years), "last_year": max(years),
    }


def main():
    no_llm = "--no-llm" in sys.argv
    taxonomy = read_json(P["taxonomy"], None)
    cat_info = {c["id"]: {"id": c["id"], "name": c["name"], "area": a["id"], "area_name": a["name"]}
                for a in taxonomy["areas"] for c in a["categories"]}
    seed = {v["vid"]: v for v in read_jsonl(P["seed_videos"])}
    seed_m = group(read_jsonl(P["seed_mentions"]))
    new_m = group(read_jsonl(P["mentions"]))
    yt = {r["vid"]: r["item"] for r in read_jsonl(P["yt_meta"])}
    cards = {c["vid"]: c for c in read_jsonl(P["cards"])}

    # ---- seed videos: add what HN said since the seed
    catalog = {}
    for vid, v in seed.items():
        v = {**v, "hn": {**v["hn"], "stances": dict(v["hn"]["stances"])}}
        ms = [m for m in new_m.get(vid, []) if not m.get("self_promo")]
        if ms:
            old = seed_m.get(vid, [])
            old_rec_authors = {m["author"] for m in old if m["stance"] in REC}
            old_rec_months = {m["month"] for m in old if m["stance"] in REC}
            recs = [m for m in ms if m["stance"] in REC]
            d_months = len({m["month"] for m in recs} - old_rec_months)
            d_strong = sum(m["stance"] == "strong_rec" for m in ms)
            h = v["hn"]
            h["mentions"] += len(ms)
            h["recommenders"] += len({m["author"] for m in recs} - old_rec_authors)
            h["rec_months"] += d_months
            h["strong_recs"] += d_strong
            for m in ms:
                h["stances"][m["stance"]] = h["stances"].get(m["stance"], 0) + 1
            h["last_year"] = max(h["last_year"], max(int(m["month"][:4]) for m in ms))
            v["score"] = v["score"] + d_months + 0.5 * d_strong
            v["quote"] = v.get("quote") or best_quote(ms)
        catalog[vid] = v

    # ---- which non-seed videos need a card
    qualifying = []
    for vid, ms in new_m.items():
        if vid in seed:
            continue
        honest = [m for m in ms if not m.get("self_promo")]
        if len({m["author"] for m in honest}) >= 3 and len({m["month"] for m in honest}) >= 3:
            qualifying.append(vid)

    now = time.time()
    fresh_vids, window = [], FRESH_WINDOWS[-1]
    for days in FRESH_WINDOWS:
        since = now - days * 86400
        fresh_vids = sorted({m["vid"] for ms in new_m.values() for m in ms
                             if m["stance"] in REC and not m.get("self_promo") and m.get("time", 0) >= since})
        window = days
        if len(fresh_vids) >= FRESH_MIN:
            break

    need = [v for v in dict.fromkeys(qualifying + fresh_vids)
            if v not in seed and v not in cards and yt.get(v)]
    print(f"{len(qualifying)} non-seed videos qualify; NEW window {window}d has {len(fresh_vids)} videos; "
          f"{len(need)} need cards")
    if need and not no_llm:
        import label as llm
        inputs = []
        for vid in need:
            sn = yt[vid].get("snippet", {})
            ms = sorted(new_m[vid], key=lambda m: m["stance"] not in REC)[:10]
            inputs.append({"vid": vid, "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                           "description": sn.get("description", ""),
                           "hn_comments": [{"stance": m["stance"], "why": m["why"], "quote": m["quote"]} for m in ms]})
        new_cards = llm.make_cards(inputs, taxonomy)
        append_jsonl(P["cards"], new_cards)
        cards.update({c["vid"]: c for c in new_cards})
        print(f"  wrote {len(new_cards)} cards")

    def record(vid):
        c, sn = cards[vid], (yt.get(vid) or {}).get("snippet", {})
        ms = new_m[vid]
        h = stats(ms)
        return {
            "vid": vid, "url": f"https://www.youtube.com/watch?v={vid}",
            "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
            "summary": c["summary"], "why_hn": c["why_hn"], "content_type": c["content_type"],
            "level": c["level"], "shelf_life": c["shelf_life"], "primary_topic": c["primary_topic"],
            "tags": c["tags"], "categories": [cat_info[i] for i in c["categories"] if i in cat_info],
            "quote": best_quote(ms), "hn": h, "score": h["rec_months"] + 0.5 * h["strong_recs"],
        }

    for vid in qualifying:
        if vid in cards and yt.get(vid):
            catalog[vid] = record(vid)

    fresh = []
    for vid in fresh_vids:
        v = catalog.get(vid) or (record(vid) if vid in cards and yt.get(vid) else None)
        if not v:
            continue
        recent = [m for m in new_m[vid] if m["stance"] in REC and not m.get("self_promo")]
        latest = max(recent, key=lambda m: m.get("time", 0))
        fresh.append({**v, "quote": best_quote(recent) or v.get("quote"), "fresh_time": latest["time"]})

    write_jsonl(P["videos"], sorted(catalog.values(), key=lambda v: -v["score"]))
    write_jsonl(P["fresh"], sorted(fresh, key=lambda v: -v["fresh_time"]))
    print(f"catalog: {len(catalog)} videos ({len(catalog) - len(seed)} beyond the seed); NEW: {len(fresh)}")


if __name__ == "__main__":
    main()
