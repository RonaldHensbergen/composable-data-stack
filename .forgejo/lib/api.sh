# Forgejo REST helpers for Codeberg CI (Forgejo Actions + Woodpecker).
# Source this in a step after the setup-fj action (or the Woodpecker fj
# setup), which exports:
#   FORGEJO_API_URL      e.g. https://codeberg.org/api/v1
#   FORGEJO_REPOSITORY   owner/repo
#   FORGEJO_TOKEN        access token
#   FJ_FALLBACK_HOST     e.g. https://codeberg.org (so fj needs no -H)
#
# Division of labor (see docs/ci-codeberg.md): the `fj` CLI covers the
# human-shaped commands (issue/pr/release create/edit/comment/close/merge);
# this library covers what fj cannot do — JSON queries, label management,
# commit-status checks, and the label-backed roadmap project.

# Defaults shared by Forgejo Actions (setup-fj action) and Woodpecker
# (.forgejo/lib/setup-fj.sh); both store fj's keys.json under .ci-fj/ in the
# workspace and set XDG_DATA_HOME accordingly.
export FORGEJO_API_URL="${FORGEJO_API_URL:-https://codeberg.org/api/v1}"
export FJ_FALLBACK_HOST="${FJ_FALLBACK_HOST:-https://codeberg.org}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$PWD/.ci-fj}"

fj_api() { # METHOD PATH [JSON_DATA] -> body on stdout; fails on HTTP error
  # POSIX sh compatible (sourced by Woodpecker's /bin/sh): no arrays, so the
  # curl arguments are accumulated in the function's positional parameters.
  method="$1" path="$2" data="${3:-}"
  set -- -fsS -X "$method" -H "Authorization: token ${FORGEJO_TOKEN:?FORGEJO_TOKEN not set}" -H "Accept: application/json"
  if [ -n "$data" ]; then
    set -- "$@" -H "Content-Type: application/json" -d "$data"
  fi
  curl "$@" "${FORGEJO_API_URL:?}/${path}"
}

fj_get()   { fj_api GET "$1"; }
fj_post()  { fj_api POST "$1" "$2"; }
fj_patch() { fj_api PATCH "$1" "$2"; }

# ensure_label NAME [COLOR] [DESCRIPTION] — create when missing, patch when given
ensure_label() {
  local name="$1" color="${2:-}" desc="${3:-}" id
  id="$(fj_get "repos/$FORGEJO_REPOSITORY/labels?limit=100" \
    | jq -r --arg n "$name" '.[] | select(.name == $n) | .id // empty')"
  if [ -n "$id" ]; then
    if [ -n "$color" ] || [ -n "$desc" ]; then
      fj_patch "repos/$FORGEJO_REPOSITORY/labels/$id" \
        "$(jq -n --arg c "${color:-ededed}" --arg d "$desc" '{color:$c, description:$d}')" >/dev/null
    fi
  else
    fj_post "repos/$FORGEJO_REPOSITORY/labels" \
      "$(jq -n --arg n "$name" --arg c "${color:-ededed}" --arg d "$desc" '{name:$n, color:$c, description:$d}')" >/dev/null
  fi
}

# find_open_issue LABEL TITLE -> issue number (empty when absent)
find_open_issue() {
  fj_get "repos/$FORGEJO_REPOSITORY/issues?state=open&labels=$1&limit=50" \
    | jq -r --arg t "$2" '.[] | select(.title == $t) | .number' | head -n 1
}

# search_open_issues QUERY -> numbers of open issues matching the query
search_open_issues() {
  fj_get "repos/$FORGEJO_REPOSITORY/issues?state=open&type=issues&q=$1&limit=50" \
    | jq -r '.[].number'
}

# create_issue TITLE BODY [LABEL] -> new issue number
# (REST, not `fj issue create`: fj has no --label flag and no JSON output to
# learn the new number from. Forgejo accepts label names in the payload.)
create_issue() {
  local payload
  payload="$(jq -n --arg t "$1" --arg b "$2" '{title:$t, body:$b}')"
  if [ -n "${3:-}" ]; then
    payload="$(printf '%s' "$payload" | jq --arg l "$3" '. + {labels: [$l]}')"
  fi
  fj_post "repos/$FORGEJO_REPOSITORY/issues" "$payload" | jq -r '.number'
}

# head_status SHA -> combined commit status state (empty when none reported)
head_status() {
  # $'\n' is not POSIX; build the newline with printf
  nl="$(printf '\n')"
  out="$(curl -sS -w "$nl%{http_code}" \
    -H "Authorization: token ${FORGEJO_TOKEN:?}" \
    "${FORGEJO_API_URL:?}/repos/$FORGEJO_REPOSITORY/commits/$1/status")"
  code="${out##*"$nl"}"
  [ "$code" = "200" ] || return 0
  printf '%s' "${out%"$nl"*}" | jq -r '.state // .status // empty'
}

# open_labeled_prs LABEL [HEAD_SHA] -> numbers of open, non-fork PRs with LABEL
# (optionally at HEAD_SHA); non-fork means head repo == base repo.
open_labeled_prs() {
  fj_get "repos/$FORGEJO_REPOSITORY/pulls?state=open&labels=$1&limit=100" \
    | jq -r --arg h "${2:-}" --arg repo "$FORGEJO_REPOSITORY" \
      '.[] | select((.head.repo.full_name // $repo) == $repo)
            | select($h == "" or .head.sha == $h) | .number'
}

# merge_pr_if_green PR_NUMBER — squash-merge with branch deletion, but only
# when the head SHA's combined commit status is `success` (Forgejo has no
# auto-merge queue; this is the trust gate that replaces `gh pr merge --auto`).
merge_pr_if_green() {
  local n="$1" head state
  head="$(fj_get "repos/$FORGEJO_REPOSITORY/pulls/$n" | jq -r '.head.sha')"
  state="$(head_status "$head")"
  if [ "$state" != "success" ]; then
    echo "PR #$n: head ${head} status '${state:-none}'; not merging yet." >&2
    return 1
  fi
  fj pr -R origin merge "$n" -M squash -d
}

# release_is_draft TAG -> "true"/"false"; empty when no release exists for the tag
release_is_draft() {
  nl="$(printf '\n')"
  out="$(curl -sS -w "$nl%{http_code}" \
    -H "Authorization: token ${FORGEJO_TOKEN:?}" \
    "${FORGEJO_API_URL:?}/repos/$FORGEJO_REPOSITORY/releases/tags/$1")"
  code="${out##*"$nl"}"
  [ "$code" = "200" ] || return 0
  printf '%s' "${out%"$nl"*}" | jq -r '.draft | tostring'
}

# --- label-backed roadmap project ------------------------------------------
# Forgejo has no Projects v2 / GraphQL. The roadmap project is represented by
# issue labels: `roadmap-project` (membership), `order:<n>` (Order field),
# `status:<value>` (Status field). project_items_json emits the same JSON
# shape that `gh project item-list --format json` produced on GitHub, so
# scripts/assign_project_order.py keeps working unmodified.
project_items_json() {
  local items
  items="$(fj_get "repos/$FORGEJO_REPOSITORY/issues?state=all&type=issues&limit=200" \
    | jq -c '[.[] | select([.labels[].name] | index("roadmap-project"))]')"
  jq -n --argjson items "$items" '{
    items: [$items[] | {
      id: ("label-proj-\(.number)"),
      content: {type: "Issue", number: .number, url: .html_url},
      order: ([.labels[].name | select(startswith("order:")) | sub("order:";"") | tonumber] | first // null)
    }]
  }'
}

project_add() { # ISSUE_NUMBER — add the issue to the roadmap project
  ensure_label "roadmap-project"
  fj_post "repos/$FORGEJO_REPOSITORY/issues/$1/labels" '["roadmap-project"]' >/dev/null
}

project_set_field() { # ISSUE_NUMBER FIELD VALUE — FIELD is Order or Status
  local n="$1" prefix val newlabel id
  prefix="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
  val="$3"
  newlabel="${prefix}:${val}"
  ensure_label "$newlabel"
  fj_get "repos/$FORGEJO_REPOSITORY/issues/$n/labels" \
    | jq -r --arg p "$prefix" '.[] | select(.name | startswith($p + ":")) | .id' \
    | while read -r id; do
        [ -n "$id" ] && curl -fsS -X DELETE \
          -H "Authorization: token ${FORGEJO_TOKEN:?}" \
          "${FORGEJO_API_URL:?}/repos/$FORGEJO_REPOSITORY/issues/$n/labels/$id" >/dev/null
      done
  fj_post "repos/$FORGEJO_REPOSITORY/issues/$n/labels" "$(jq -n --arg l "$newlabel" '[$l]')" >/dev/null
}
