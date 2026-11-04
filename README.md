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
| 19 | HN NEW | recommended on HN in the last ~30 days (updated weekly) |
| 24 | HN MUSIC | `content_type == music` |
| 99 | LATE NIGHT | lectures, talks, interviews and documentaries over 50 min, on air 11pm–5am local time |

## Weekly updates (GitHub Actions)

`.github/workflows/weekly-update.yml` runs every Saturday 12:00 UTC (or by hand from the Actions tab):

1. `scripts/hn_update.py collect`: new HN comments linking YouTube, from HN's Algolia search API since
   `data/hn/cursor.json`. Code-only filters: a YouTube id, at least 100 characters of the commenter's own
   text, one mention per person per video, at most 20 per video. Videos HackerTube never saw get their older
   HN history backfilled so they can reach the bar.
2. `scripts/fetch_youtube_meta.py --refresh`: durations, availability, titles for every video (not committed).
3. `scripts/hn_update.py label`: Claude labels each surviving comment (stance, why, verbatim quote,
   self-promotion) into `data/hn/mentions.jsonl`, in the same format as HackerTube's data.
4. `scripts/make_cards.py`: updates HN stats and scores. Videos newly reaching 3+ people in 3+ months get a
   Claude-written card and join the catalog (`data/videos.jsonl`). Recently recommended ones feed
   channel 19 HN NEW (`data/fresh.jsonl`).
5. `scripts/build_channels.py --from next-monday`: builds next week's **edition**.
6. `scripts/check_build.py`: refuses to publish if channels empty out or more than 5% of videos vanish.
7. Commit and push, and Cloudflare redeploys.

**Editions:** a channel's schedule depends on its contents, so new data never goes live mid-week. Each build
starts Monday 00:00 UTC. `public/data/lineup.json` keeps the edition on air plus upcoming ones, and every
open tab switches at the same instant ("NEW WEEK ON HNTV").

**Cost:** Claude (`claude-opus-5-5`, low effort) costs about $2-3 a week, and the first run, which fills the
gap since the seed's August 2026 cutoff, costs about $10-15. YouTube uses about 200 of the 10,000 free daily
quota units.

### One-time setup

**YouTube key:** add a repository secret `YOUTUBE_API_KEY` (Settings → Secrets and variables → Actions). If the
key has an *application* restriction by IP, remove it, because GitHub's runners use changing IPs. Keep the
*API* restriction to YouTube Data API v3.

**Claude via Workload Identity Federation (no API key stored anywhere):**

1. Claude Console → [Workload identity](https://platform.claude.com/settings/workload-identity-federation) →
   **Connect workload** → **GitHub Actions**.
2. Issuer: `https://token.actions.githubusercontent.com` with discovery (prefilled).
3. Rule match, as narrow as possible:
   - subject prefix `repo:dsalzman/hackertv:ref:refs/heads/main`
   - audience `https://api.anthropic.com`
   - claims `repository_owner = dsalzman`, `ref = refs/heads/main`
4. Scope `workspace:developer`, token lifetime `600`. Pick the workspace to bill.
5. Add four repository **variables** (not secrets; they're identifiers) under Settings → Secrets and
   variables → Actions → Variables: `ANTHROPIC_FEDERATION_RULE_ID` (`fdrl_...`), `ANTHROPIC_SERVICE_ACCOUNT_ID`
   (`svac_...`), `ANTHROPIC_ORGANIZATION_ID` (Console → Organization), `ANTHROPIC_WORKSPACE_ID` (`wrkspc_...`).
6. Run the workflow from the Actions tab within the wizard's 15-minute test window to confirm it.

GitHub's identity tokens last about 5 minutes and can be exchanged only once, so the workflow keeps a fresh one
in the token file (`scripts/gha_oidc_token.sh`). The Python SDK picks the federation settings up from the
environment with a plain `anthropic.Anthropic()`. Never also set `ANTHROPIC_API_KEY`, because it would take
priority over federation.

### Running it locally

```sh
python3 scripts/hn_update.py collect
YOUTUBE_API_KEY=... python3 scripts/fetch_youtube_meta.py --refresh
ANTHROPIC_API_KEY=... python3 scripts/hn_update.py label   # or `ant auth login`
ANTHROPIC_API_KEY=... python3 scripts/make_cards.py
python3 scripts/build_channels.py --from next-monday && python3 scripts/check_build.py
```

## Viewer count

`worker/index.js` adds `POST /api/presence` to the Cloudflare Worker (config: `wrangler.jsonc`). Each switched-on TV
sends an anonymous random session id about once a minute, and a single Durable Object counts the ids seen in the
last 2.5 minutes, in total and per channel. Nothing is stored and no IPs are kept. Only `/api/*` runs Worker code;
the site itself is still served as static assets. Test it locally with `npx wrangler dev`.

## Upkeep and security

- **Health check** (`.github/workflows/health-check.yml`, Monday 01:00 UTC): `scripts/health_check.py` checks the
  live site's security headers, that the deployed lineup matches `main`, every channel file of the edition on
  air, a random sample of videos still playable (YouTube oEmbed), the presence API's input validation, and that
  Saturday's weekly update succeeded. A failure opens a `health-check` issue; the next pass closes it.
  Run it yourself with `python3 scripts/health_check.py`.
- **Security headers** come from `public/_headers` (strict CSP: our code, YouTube, Google Fonts, Cloudflare Web Analytics).
- **Dependabot** (`.github/dependabot.yml`), **CodeQL**, and **secret scanning with push protection** are on.
  Actions are pinned to commit SHAs; Dependabot bumps them.
- **Weekly Claude review** (a Claude Code scheduled routine): reads the health check, alerts and the week's
  commits, and opens a pull request or issue if something needs fixing. It never pushes to `main`.
- Reporting a vulnerability: see `SECURITY.md`.

## Deploy

Cloudflare Workers Builds deploys on every push to `main` with `npx wrangler deploy` (`wrangler.jsonc`): static assets from `public/` plus the presence API.

## Credits

Data: [HackerTube](https://hackertube.org) (ODC-By) and
[open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news).
Video via YouTube. Not affiliated with YouTube, Google, Hacker News or Y Combinator.
