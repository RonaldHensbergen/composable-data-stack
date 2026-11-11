#!/usr/bin/env bash
# pr_thread_reply.sh [--yes] <pr> <thread-id> <reply-body> — reply to a review thread and resolve it, one call.
set -euo pipefail
assume_yes=0
if [[ "${1:-}" == "--yes" ]]; then
  assume_yes=1
  shift
fi
pr=${1:?usage: pr_thread_reply.sh [--yes] <pr> <thread-id> <reply-body>}
tid=${2:?missing thread-id}
body=${3:?missing reply-body}

# Preflight verification: ensure the provided thread belongs to the provided PR.
thread_pr_number=$(
  gh api graphql \
    -f query='query($tid:ID!){ node(id:$tid){ ... on PullRequestReviewThread { isResolved pullRequest { number } } } }' \
    -f tid="$tid" \
    --jq '.data.node.pullRequest.number'
)

if [[ -z "${thread_pr_number}" || "${thread_pr_number}" == "null" ]]; then
  echo "error: thread-id '$tid' was not found or is not a pull request review thread" >&2
  exit 1
fi

if [[ "${thread_pr_number}" != "${pr}" ]]; then
  echo "error: thread-id '$tid' belongs to PR #${thread_pr_number}, not PR #${pr}; aborting" >&2
  exit 1
fi

if [[ "${assume_yes}" -ne 1 ]]; then
  echo "About to post a reply and resolve thread '$tid' on PR #$pr." >&2
  read -r -p "Proceed? [y/N] " confirm
  if [[ ! "${confirm}" =~ ^([yY]|[yY][eE][sS])$ ]]; then
    echo "aborted" >&2
    exit 1
  fi
fi

gh api graphql -f query='mutation($tid:ID!,$body:String!){ addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$tid, body:$body}){ clientMutationId } }' -f tid="$tid" -f body="$body"
gh api graphql -f query='mutation($tid:ID!){ resolveReviewThread(input:{threadId:$tid}){ thread{ isResolved } } }' -f tid="$tid"
