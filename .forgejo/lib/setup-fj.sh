#!/usr/bin/env bash
# Install fj (forgejo-cli) for a Woodpecker step and write its keys.json into
# the shared workspace (.ci-fj/, picked up via XDG_DATA_HOME by
# .forgejo/lib/api.sh). Run once per pipeline, before any step that sources
# .forgejo/lib/api.sh or calls fj:
#
#   steps:
#     - name: setup-fj
#       image: alpine:3.21
#       environment:
#         FORGEJO_TOKEN: { from_secret: codeberg_token }
#       commands:
#         - bash .forgejo/lib/setup-fj.sh
#
# Required env: FORGEJO_TOKEN (Woodpecker secret `codeberg_token`).
# Optional: FORGEJO_HOST (default https://codeberg.org), FJ_VERSION
# (default v0.7.0), FORGEJO_REPOSITORY (defaults to $CI_REPO_OWNER/$CI_REPO_NAME).
set -euo pipefail

FJ_VERSION="${FJ_VERSION:-v0.7.0}"
FORGEJO_HOST="${FORGEJO_HOST:-https://codeberg.org}"
: "${FORGEJO_TOKEN:?FORGEJO_TOKEN (Woodpecker secret codeberg_token) is required}"
export FORGEJO_API_URL="${FORGEJO_API_URL:-${FORGEJO_HOST}/api/v1}"
export FORGEJO_REPOSITORY="${FORGEJO_REPOSITORY:-${CI_REPO_OWNER:?}/${CI_REPO_NAME:?}}"
export FJ_FALLBACK_HOST="$FORGEJO_HOST"

arch="$(uname -m)"
case "$arch" in
  x86_64)  fj_arch="x86_64" ;;
  aarch64) fj_arch="aarch64" ;;
  *) echo "setup-fj: unsupported arch $arch" >&2; exit 1 ;;
esac
curl -fsSL "https://codeberg.org/forgejo-contrib/forgejo-cli/releases/download/${FJ_VERSION}/forgejo-cli-${fj_arch}-linux.tar.gz" -o /tmp/fj.tar.gz
tar -xzf /tmp/fj.tar.gz -C /tmp
install -m 0755 /tmp/fj /usr/local/bin/fj

if ! command -v jq >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq jq
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache jq
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q jq
  else
    echo "setup-fj: jq is required but no apt/apk/dnf available" >&2; exit 1
  fi
fi

host="${FORGEJO_HOST#https://}"; host="${FORGEJO_HOST#http://}"; host="${host%%/*}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$PWD/.ci-fj}"
mkdir -p "$XDG_DATA_HOME/forgejo-cli"
jq -n --arg h "$host" --arg u "${FORGEJO_USER:-$CI_REPO_OWNER}" --arg t "$FORGEJO_TOKEN" \
  '{hosts: {($h): {type: "Application", name: $u, token: $t}}, aliases: {}, default_ssh: []}' \
  > "$XDG_DATA_HOME/forgejo-cli/keys.json"
chmod 600 "$XDG_DATA_HOME/forgejo-cli/keys.json"

fj whoami || echo "setup-fj: warning: fj whoami failed (token may lack the user scope); API calls still work" >&2
