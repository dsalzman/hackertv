"""Sanity checks on the newest edition before CI publishes it. Exits non-zero to block the push.

    python3 scripts/check_build.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "public", "data")
MAX_DROP = 0.05           # newest edition may lose at most 5% of the previous one's videos
MIN_PER_CHANNEL = 20

lineup = json.load(open(os.path.join(DATA, "lineup.json")))
eds = sorted(lineup["editions"], key=lambda e: e["from"])
new, prev = eds[-1], (eds[-2] if len(eds) > 1 else None)
problems, warnings = [], []


def vids(ed):
    out = set()
    for c in ed["channels"]:
        path = os.path.join(DATA, ed["path"], "ch", f"{c['id']}.json")
        if not os.path.exists(path):
            problems.append(f"missing {path}")
            continue
        ch = json.load(open(path))
        if ch["total"] <= 0 or not ch["videos"]:
            problems.append(f"{ed['path']}: channel {c['id']} is empty")
        out |= {v["v"] for v in ch["videos"]}
    return out


if new.get("estimated"):
    problems.append("newest edition uses estimated durations (YouTube metadata missing)")
for c in new["channels"]:
    if c["id"] != "new" and c["count"] < MIN_PER_CHANNEL:
        problems.append(f"channel {c['num']} {c['name']} has only {c['count']} videos")
if not any(c["id"] == "new" for c in new["channels"]):
    warnings.append("no NEW channel this week")

new_v = vids(new)
if prev:
    prev_v = vids(prev)
    lost = len(prev_v - new_v)
    print(f"newest edition: {len(new_v)} videos ({len(new_v - prev_v)} added, {lost} gone) vs previous {len(prev_v)}")
    if prev_v and lost / len(prev_v) > MAX_DROP:
        problems.append(f"{lost} of {len(prev_v)} videos disappeared (>{MAX_DROP:.0%})")
else:
    print(f"newest edition: {len(new_v)} videos")

for w in warnings:
    print("warning:", w)
if problems:
    print("\n".join("FAIL: " + p for p in problems))
    sys.exit(1)
print("checks passed")
