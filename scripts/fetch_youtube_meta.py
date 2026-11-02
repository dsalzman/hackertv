"""Fetch durations + embeddability for every video from the YouTube Data API.

Usage:  YOUTUBE_API_KEY=... python3 scripts/fetch_youtube_meta.py

Resumable: results are appended to data/youtube_meta.jsonl and already-fetched
ids are skipped. ~130 requests (1 quota unit each) for the whole dataset.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "raw", "videos.jsonl")
OUT = os.path.join(ROOT, "data", "youtube_meta.jsonl")

key = os.environ.get("YOUTUBE_API_KEY")
if not key:
    sys.exit("Set YOUTUBE_API_KEY first (see README.md).")

vids = [json.loads(line)["vid"] for line in open(SRC)]
done = set()
if os.path.exists(OUT):
    done = {json.loads(line)["vid"] for line in open(OUT)}
todo = [v for v in vids if v not in done]
print(f"{len(vids)} videos, {len(done)} cached, {len(todo)} to fetch")

with open(OUT, "a") as f:
    for i in range(0, len(todo), 50):
        ids = todo[i:i + 50]
        q = urllib.parse.urlencode({
            "part": "contentDetails,status,snippet",
            "fields": "items(id,contentDetails(duration),status(embeddable,privacyStatus),snippet(liveBroadcastContent))",
            "id": ",".join(ids),
            "key": key,
        })
        try:
            resp = json.load(urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/videos?{q}"))
        except urllib.error.HTTPError as e:
            sys.exit(f"API error {e.code}: {e.read().decode()[:500]}")
        got = {it["id"]: it for it in resp.get("items", [])}
        for v in ids:  # missing from the response = deleted or private
            f.write(json.dumps({"vid": v, "item": got.get(v)}) + "\n")
        f.flush()
        print(f"  {min(i + 50, len(todo))}/{len(todo)}")
        time.sleep(0.2)
print("done ->", OUT)
