# Security

Found a problem with hackertv.org or this repo? Please report it privately via
**Security → Report a vulnerability** on GitHub rather than opening a public issue.

What runs where:

- **hackertv.org** is static files plus one tiny API (`worker/index.js`, `POST /api/presence`)
  on Cloudflare Workers. No accounts, cookies or stored personal data.
- **Weekly update** (`.github/workflows/weekly-update.yml`) reads public HN comments, which are
  untrusted input: Claude only returns schema-constrained labels, quotes are checked verbatim
  against the source, and the site renders all text with `textContent`, never as HTML.
- **Credentials:** Claude uses Workload Identity Federation (no API key); the YouTube key is a
  GitHub secret, restricted to the YouTube Data API.

Automated upkeep: Dependabot, CodeQL, secret scanning with push protection, a weekly health
check (`scripts/health_check.py`), and a weekly Claude review that only opens pull requests.
