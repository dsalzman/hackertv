#!/usr/bin/env bash
# Write a fresh GitHub Actions OIDC token for Anthropic Workload Identity Federation to
# $ANTHROPIC_IDENTITY_TOKEN_FILE. GitHub tokens live ~5 minutes and each may be exchanged
# only once, so long jobs run this with --loop in the background (and once before each
# Claude step) so the SDK always finds an unused token when it refreshes.
set -euo pipefail
: "${ANTHROPIC_IDENTITY_TOKEN_FILE:?}" "${ACTIONS_ID_TOKEN_REQUEST_URL:?needs permissions: id-token: write}"

fetch() {
  local tmp="${ANTHROPIC_IDENTITY_TOKEN_FILE}.tmp"
  curl -sSf -H "Authorization: Bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=https://api.anthropic.com" | jq -r .value > "$tmp"
  mv "$tmp" "$ANTHROPIC_IDENTITY_TOKEN_FILE"   # atomic: the SDK never reads a half-written file
}

if [[ "${1:-}" == "--loop" ]]; then
  while true; do fetch || echo "oidc token refresh failed; retrying" >&2; sleep 90; done
else
  fetch
fi
