#!/usr/bin/env python3
"""Weekly check that hackertv.org is up, current and locked down. Standard library only.

    python3 scripts/health_check.py [--site https://hackertv.org] [--sample 60]

Prints a report (also to $GITHUB_STEP_SUMMARY when set) and exits 1 if anything failed.
Warnings are reported but don't fail the run.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "hackertv-health-check (+https://github.com/dsalzman/hackertv)"
REQUIRED_HEADERS = ["content-security-policy", "x-content-type-options", "x-frame-options",
                    "referrer-policy", "strict-transport-security"]
MAX_DEAD = 0.10          # fail if more than 10% of sampled videos are unplayable
MAX_EDITION_AGE = 9      # days since the newest edition was built (weekly job + slack)

results = []  # (level, name, detail)


def ok(name, detail=""): results.append(("PASS", name, detail))
def warn(name, detail): results.append(("WARN", name, detail))
def fail(name, detail): results.append(("FAIL", name, detail))


def http(url, method="GET", body=None, headers=None, timeout=20):
    """Returns (status, headers, bytes). Never raises for HTTP errors."""
    h = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            return 0, {}, str(e).encode()


def check_home(site):
    st, hd, body = http(site + "/")
    if st != 200 or b"HackerTV" not in body:
        fail("home page", f"status {st}"); return
    ok("home page", "200")
    missing = [h for h in REQUIRED_HEADERS if h not in hd]
    if missing:
        fail("security headers", "missing: " + ", ".join(missing))
    else:
        csp = hd["content-security-policy"]
        bad = [d for d in ("'unsafe-eval'", "*") if d in csp.split()]
        (fail if bad else ok)("security headers", ("CSP allows " + ", ".join(bad)) if bad else "all present")
    for path in ("/.git/config", "/data/youtube_meta.jsonl", "/README.md", "/_headers"):
        s, _, b = http(site + path)
        if s == 200 and b and not b.lstrip().startswith(b"<!doctype"):
            fail("no stray files", f"{path} is served")
            break
    else:
        ok("no stray files")


def check_lineup(site):
    st, _, body = http(site + "/data/lineup.json")
    if st != 200:
        fail("lineup.json", f"status {st}"); return None
    live = json.loads(body)
    repo = json.loads((ROOT / "public/data/lineup.json").read_text())
    if [e["from"] for e in live["editions"]] != [e["from"] for e in repo["editions"]]:
        fail("deployed = repo", "live lineup.json differs from main; did the Cloudflare deploy fail?")
    else:
        ok("deployed = repo", f"{len(live['editions'])} edition(s)")

    now = time.time()
    current = [e for e in live["editions"] if e["from"] <= now]
    if not current:
        fail("edition on air", "no edition has started"); return None
    ed = max(current, key=lambda e: e["from"])
    ok("edition on air", time.strftime("%Y-%m-%d", time.gmtime(ed["from"])))
    if ed.get("estimated"):
        fail("real durations", "edition on air uses estimated durations")

    age = (now - max(e.get("built", 0) for e in live["editions"])) / 86400
    (fail if age > MAX_EDITION_AGE else ok)("weekly data fresh", f"newest edition built {age:.1f} days ago")

    vids, bad = [], []
    for ch in ed["channels"]:
        s, _, b = http(f"{site}/data/{ed['path']}/ch/{ch['id']}.json")
        try:
            data = json.loads(b) if s == 200 else None
        except ValueError:
            data = None
        if not data or not data.get("videos"):
            bad.append(f"{ch['num']} {ch['id']} ({s})")
            continue
        if data.get("total", 0) < 3600 * 2:
            bad.append(f"{ch['num']} {ch['id']} (loop under 2 hours)")
        vids += [v["v"] for v in data["videos"]]
    if bad:
        fail("channels", "broken: " + "; ".join(bad))
    else:
        ok("channels", f"{len(ed['channels'])} channels, {len(set(vids))} videos")
    return sorted(set(vids))


def check_videos(vids, n):
    if not vids:
        return
    sample = random.Random().sample(vids, min(n, len(vids)))
    dead = []
    for v in sample:
        u = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(f"https://www.youtube.com/watch?v={v}")
        s, _, _ = http(u, timeout=10)
        if s in (401, 403, 404):  # 401/403: embedding disabled or private; 404: removed
            dead.append(f"{v}({s})")
        time.sleep(0.2)
    rate = len(dead) / len(sample)
    detail = f"{len(dead)}/{len(sample)} unplayable" + (": " + " ".join(dead[:10]) if dead else "")
    (fail if rate > MAX_DEAD else warn if dead else ok)("videos playable", detail)


def check_presence(site):
    url = site + "/api/presence"
    sid = "health-" + uuid.uuid4().hex[:16]
    hdr = {"content-type": "application/json"}
    s, _, b = http(url, "POST", json.dumps({"id": sid, "ch": "prime"}).encode(), hdr)
    try:
        good = s == 200 and isinstance(json.loads(b).get("total"), int)
    except ValueError:
        good = False
    http(url, "POST", json.dumps({"id": sid, "leave": True}).encode(), hdr)  # don't inflate the count
    if not good:
        fail("presence API", f"heartbeat returned {s}"); return
    probes = {
        "GET": http(url)[0] == 405,
        "bad id": http(url, "POST", b'{"id":"<script>","ch":"prime"}', hdr)[0] == 400,
        "bad json": http(url, "POST", b"{nope", hdr)[0] == 400,
        "oversized": http(url, "POST", json.dumps({"id": sid, "ch": "x" * 4000}).encode(), hdr)[0] == 413,
        "unknown route": http(site + "/api/nope")[0] == 404,
    }
    failed = [k for k, v in probes.items() if not v]
    (fail if failed else ok)("presence API", ("accepts " + ", ".join(failed)) if failed else "heartbeat + input validation")


def report():
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    lines = ["| | Check | Detail |", "|---|---|---|"]
    lines += [f"| {icon[l]} | {n} | {d} |" for l, n, d in results]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## HackerTV health check\n\n" + text + "\n")
    if os.environ.get("HEALTH_REPORT"):
        Path(os.environ["HEALTH_REPORT"]).write_text(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="https://hackertv.org")
    ap.add_argument("--sample", type=int, default=60)
    a = ap.parse_args()
    site = a.site.rstrip("/")
    check_home(site)
    vids = check_lineup(site)
    check_videos(vids, a.sample)
    check_presence(site)
    report()
    sys.exit(1 if any(l == "FAIL" for l, _, _ in results) else 0)


if __name__ == "__main__":
    main()
