"""Pull new HN comments that link YouTube videos, filter them in code, label survivors with Claude.

    python3 scripts/hn_update.py collect     # Algolia -> data/hn/pending.jsonl (no LLM)
    python3 scripts/hn_update.py label       # pending -> Claude -> data/hn/mentions.jsonl

Run fetch_youtube_meta.py between the two so labels and cards can see video titles.
The cursor only advances in `label`, so a failed run is simply retried next time.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

from hnlib import (MIN_OWN_TEXT, P, append_jsonl, clean_comment, extract_vids, mention_key,
                   month_of, read_json, read_jsonl, write_json, write_jsonl)

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
SEED_END = 1787443200            # 2026-08-23: the HackerTube seed's source archive ends here
SETTLE = 86400                   # skip the newest day: comments are still being edited/flagged
QUERIES = ["youtube", "youtu.be"]
BACKFILL_MAX = 300               # older-history lookups per run
MAX_MENTIONS_PER_VIDEO = 20      # same cap as HackerTube


def algolia(params):
    url = ALGOLIA + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - network flake: back off and retry
            if attempt == 4:
                raise
            print(f"  algolia retry ({e})", file=sys.stderr)
            time.sleep(2 ** attempt)


def fetch_window(query, a, b, out):
    """All comments matching `query` with a < created_at_i <= b. Algolia caps a query at 1000
    hits, so busy windows are split in half until they fit."""
    params = {"query": query, "tags": "comment", "hitsPerPage": 1000,
              "numericFilters": f"created_at_i>{a},created_at_i<={b}"}
    res = algolia(params)
    if res.get("nbHits", 0) > 1000 and b - a > 3600:
        mid = (a + b) // 2
        fetch_window(query, a, mid, out)
        fetch_window(query, mid, b, out)
        return
    for h in res.get("hits", []):
        out[h["objectID"]] = h
    time.sleep(0.3)


def to_mentions(hit, only_vid=None):
    """Code-only filter: YouTube link present, >=100 chars of own text, has an author."""
    if not hit.get("author") or not hit.get("comment_text"):
        return []
    out = []
    for vid in extract_vids(hit["comment_text"]):
        if only_vid and vid != only_vid:
            continue
        text, own = clean_comment(hit["comment_text"], vid)
        if own < MIN_OWN_TEXT:
            continue
        cid = int(hit["objectID"])
        out.append({
            "hn_comment_id": cid, "hn_url": f"https://news.ycombinator.com/item?id={cid}",
            "vid": vid, "author": hit["author"], "month": month_of(hit["created_at_i"]),
            "time": hit["created_at_i"], "story_title": hit.get("story_title") or "", "text": text,
        })
    return out


def collect():
    cursor = read_json(P["cursor"], {"until": SEED_END})
    a, b = cursor["until"], int(time.time()) - SETTLE
    print(f"collect: comments from {month_of(a)} ({a}) to {b}")
    hits = {}
    for q in QUERIES:
        fetch_window(q, a, b, hits)
    print(f"  {len(hits)} comments mention youtube")

    known = {mention_key(m) for m in read_jsonl(P["seed_mentions"])}
    known |= {mention_key(m) for m in read_jsonl(P["mentions"])}
    fresh = [m for h in hits.values() for m in to_mentions(h) if mention_key(m) not in known]
    print(f"  {len(fresh)} new mentions survive the code filters")

    # Older HN history for videos HackerTube never saw, so they can reach the 3-people/3-months bar.
    seed_vids = {v["vid"] for v in read_jsonl(P["seed_videos"])}
    backfilled = set(read_json(P["backfilled"], []))
    todo = [v for v in dict.fromkeys(m["vid"] for m in fresh) if v not in seed_vids and v not in backfilled]
    older = []
    for vid in todo[:BACKFILL_MAX]:
        res = algolia({"query": vid, "tags": "comment", "hitsPerPage": 100})
        for h in res.get("hits", []):
            if h.get("created_at_i", 0) <= a:
                older += [m for m in to_mentions(h, only_vid=vid) if mention_key(m) not in known]
        backfilled.add(vid)
        time.sleep(0.2)
    print(f"  backfilled {min(len(todo), BACKFILL_MAX)} videos: {len(older)} older mentions")

    # Dedupe, then keep at most 20 mentions per video, favouring different commenters.
    rows = {mention_key(m): m for m in fresh + older}
    per_vid = defaultdict(list)
    for m in sorted(rows.values(), key=lambda m: -m["time"]):
        per_vid[m["vid"]].append(m)
    pending = []
    for vid, ms in per_vid.items():
        authors, picked = set(), []
        for m in ms:
            if m["author"] not in authors:
                authors.add(m["author"]); picked.append(m)
        pending += picked[:MAX_MENTIONS_PER_VIDEO]

    write_jsonl(P["pending"], pending)
    write_json(P["pending"] + ".meta", {"until": b, "backfilled": sorted(backfilled)})
    print(f"  {len(pending)} mentions pending labeling across {len(per_vid)} videos")


def label():
    import label as llm

    pending = read_jsonl(P["pending"])
    meta = read_json(P["pending"] + ".meta", None)
    if meta is None:
        sys.exit("nothing collected; run `collect` first")
    titles = {}
    for r in read_jsonl(P["yt_meta"]):
        if r.get("item"):
            titles[r["vid"]] = r["item"].get("snippet", {}).get("title", "")
    # Don't spend tokens on videos YouTube says are gone.
    gone = {r["vid"] for r in read_jsonl(P["yt_meta"]) if not r.get("item")}
    todo = [m for m in pending if m["vid"] not in gone]
    print(f"label: {len(todo)} mentions ({len(pending) - len(todo)} skipped: video unavailable)")

    labels = llm.label_mentions(todo, titles) if todo else {}
    rows = []
    for m in todo:
        lab = labels.get(mention_key(m))
        if lab:
            rows.append({**{k: m[k] for k in ("hn_comment_id", "hn_url", "vid", "author", "month",
                                              "time", "story_title")},
                         **lab, "text": m["text"]})
    missing = len(todo) - len(rows)
    if todo and missing > 0.2 * len(todo):
        sys.exit(f"too many unlabeled mentions ({missing}/{len(todo)}); not advancing the cursor")
    append_jsonl(P["mentions"], rows)
    write_json(P["backfilled"], meta["backfilled"])
    write_json(P["cursor"], {"until": meta["until"]})
    print(f"  labeled {len(rows)} mentions; cursor -> {meta['until']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"collect": collect, "label": label}.get(cmd, lambda: sys.exit(__doc__))()
