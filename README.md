# HackerTV

The videos Hacker News keeps recommending, broadcast 24/7 on channels, shown on an old TV.
Everyone sees the same program on the same channel at the same moment, with no server state.
The schedule is computed in the browser from the clock.

## Run locally

```sh
cd public && python3 -m http.server 8642
# open http://localhost:8642
```

Add `?alwayson` to the URL to watch Late Night (ch 99) outside 11pm–5am.

## How it works

- `data/raw/`: the [HackerTube](https://hackertube.org) export (6,454 videos, 16 topic areas).
- `scripts/fetch_youtube_meta.py`: gets durations and embeddability from the YouTube Data API
  into `data/youtube_meta.jsonl` (gitignored; YouTube's terms forbid redistributing it).
- `scripts/build_channels.py`: writes `public/data/lineup.json` and `public/data/ch/*.json`.
  Without the YouTube metadata it falls back to estimated durations.
- `public/schedule.js`: the deterministic scheduler. Each channel loops through its videos. Every
  loop ("cycle") is shuffled by a PRNG seeded with `(channel id, cycle number)`. The program airing
  at time `t` comes from `(t - EPOCH) mod loop length`. **Changing `EPOCH`, the shuffle, or the
  channel contents changes everyone's schedule.**
- `public/app.js`: the TV. It runs the YouTube IFrame player, static between channels, drift
  correction, the guide, filters and the remote.

### Channels

| # | Name | What |
|---|---|---|
| 2 | HN PRIME | top 400 videos by HN score |
| 3–18 | DEVTV … IDEAS | one per HackerTube topic area |
| 24 | HN MUSIC | `content_type == music` |
| 99 | LATE NIGHT | lectures, talks, interviews and documentaries over 50 min, on air 11pm–5am local time |

## Refreshing the data

```sh
export YOUTUBE_API_KEY=...            # never commit it
rm data/youtube_meta.jsonl            # to refetch everything (~130 quota units)
python3 scripts/fetch_youtube_meta.py
python3 scripts/build_channels.py
```

YouTube's developer policies expect stored API data to be refreshed now and then. Re-running every
few weeks also drops videos that have been deleted since.

## Deploy

`public/` is a static site. Point Cloudflare Pages, GitHub Pages or Netlify at it (no build command).

## Credits

Data: [HackerTube](https://hackertube.org) (ODC-By) and
[open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news).
Video via YouTube. Not affiliated with YouTube, Google, Hacker News or Y Combinator.
