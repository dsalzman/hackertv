"""Fetch durations, embeddability, titles and descriptions from the YouTube Data API.

Usage:  YOUTUBE_API_KEY=... python3 scripts/fetch_youtube_meta.py [--refresh]

Covers every video the pipeline knows: the HackerTube seed, our cards, labeled
mentions, and pending mentions. Output goes to data/youtube_meta.jsonl, which is
gitignored: YouTube's terms forbid redistributing it, and expect it to be kept
fresh, so CI refetches everything weekly with --refresh (~150 quota units).
Without --refresh, already-fetched ids are skipped.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hnlib import P, read_jsonl  # noqa: E402

key = os.environ.get("YOUTUBE_API_KEY")
if not key:
    sys.exit("Set YOUTUBE_API_KEY first (see README.md).")

vids = list(dict.fromkeys(
    [v["vid"] for v in read_jsonl(P["seed_videos"])]
    + [c["vid"] for c in read_jsonl(P["cards"])]
    + [m["vid"] for m in read_jsonl(P["mentions"])]
    + [m["vid"] for m in read_jsonl(P["pending"])]))

if "--refresh" in sys.argv and os.path.exists(P["yt_meta"]):
    os.remove(P["yt_meta"])
done = {r["vid"] for r in read_jsonl(P["yt_meta"])}
todo = [v for v in vids if v not in done]
print(f"{len(vids)} videos, {len(done)} cached, {len(todo)} to fetch")

FIELDS = ("items(id,contentDetails(duration),status(embeddable,privacyStatus),"
          "snippet(title,channelTitle,description,liveBroadcastContent))")

with open(P["yt_meta"], "a") as f:
    for i in range(0, len(todo), 50):
        ids = todo[i:i + 50]
        q = urllib.parse.urlencode({"part": "contentDetails,status,snippet", "fields": FIELDS,
                                    "id": ",".join(ids), "key": key})
        for attempt in range(4):
            try:
                resp = json.load(urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{q}", timeout=30))
                break
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 3:
                    sys.exit(f"API error {e.code}: {e.read().decode()[:500]}")
                time.sleep(2 ** attempt)
        got = {}
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            sn["description"] = (sn.get("description") or "")[:1500]
            got[it["id"]] = it
        for v in ids:  # missing from the response = deleted or private
            f.write(json.dumps({"vid": v, "item": got.get(v)}, ensure_ascii=False) + "\n")
        f.flush()
        if (i // 50) % 20 == 0:
            print(f"  {min(i + 50, len(todo))}/{len(todo)}")
        time.sleep(0.1)
print("done ->", P["yt_meta"])
