"""Shared helpers for the HN -> HackerTV data pipeline."""
import datetime as dt
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")              # HackerTube seed export (read-only)
HN = os.path.join(ROOT, "data", "hn")                # our incremental data (committed)
P = {
    "seed_videos": os.path.join(RAW, "videos.jsonl"),
    "seed_mentions": os.path.join(RAW, "mentions.jsonl"),
    "taxonomy": os.path.join(RAW, "taxonomy.json"),
    "mentions": os.path.join(HN, "mentions.jsonl"),      # labeled mentions newer than the seed
    "cards": os.path.join(HN, "cards.jsonl"),            # video cards we wrote for non-seed videos
    "cursor": os.path.join(HN, "cursor.json"),
    "backfilled": os.path.join(HN, "backfilled.json"),   # vids whose older HN history we already pulled
    "pending": os.path.join(HN, "pending.jsonl"),        # collected, not yet labeled (gitignored)
    "videos": os.path.join(ROOT, "data", "videos.jsonl"),  # merged output used by build_channels
    "fresh": os.path.join(ROOT, "data", "fresh.jsonl"),    # NEW channel: recently recommended
    "yt_meta": os.path.join(ROOT, "data", "youtube_meta.jsonl"),  # gitignored, refetched weekly
}

MIN_OWN_TEXT = 100        # a mention counts only with >=100 chars of the commenter's own words
TEXT_CAP = 1500

_VID = r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])"
YT_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:[^\s\"'<>]*?&)?v=|embed/|live/|shorts/|v/)"
    r"|youtu\.be/)" + _VID)
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def append_jsonl(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")


def extract_vids(comment_html):
    """Unique YouTube ids linked in an HN comment (Algolia's HTML), in order."""
    t = html.unescape(comment_html or "")
    seen = []
    for v in YT_RE.findall(t):
        if v not in seen:
            seen.append(v)
    return seen


def clean_comment(comment_html, vid):
    """HackerTube's text format: HTML stripped, links -> [VIDEO] (this video) / [LINK], capped.

    Returns (text, own_text_length)."""
    t = html.unescape(comment_html or "")
    t = re.sub(r"<p>", "\n\n", t)

    def link(m):
        return "[VIDEO]" if vid in m.group(0) else "[LINK]"

    t = re.sub(r"<a\s[^>]*>.*?</a>", link, t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = URL_RE.sub(link, t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    own = len(re.sub(r"\[(?:VIDEO|LINK)\]", "", t).strip())
    return t[:TEXT_CAP], own


def month_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m")


def mention_key(m):
    return (int(m["hn_comment_id"]), m["vid"])
