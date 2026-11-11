#!/usr/bin/env bash
# pr_thread_reply.sh <pr> <thread-id> <reply-body> — reply to a review thread and resolve it, one call.
set -euo pipefail
if [[ "${1:-}" == "--yes" ]]; then
  echo "error: --yes is disabled; interactive confirmation is required." >&2
  exit 1
fi

pr=${1:?usage: pr_thread_reply.sh <pr> <thread-id> <reply-body>}
tid=${2:?missing thread-id}
body=${3:?missing reply-body}

# Preflight verification: ensure the provided thread belongs to the provided PR and expected repository.
expected_owner="RonaldHensbergen"
expected_repo="composable-data-stack"
thread_pr_number=$(
  gh api graphql \
    -f query='query($tid:ID!){ node(id:$tid){ ... on PullRequestReviewThread { isResolved pullRequest { number repository { owner { login } name } } } } }' \
    -f tid="$tid" \
    --jq '.data.node.pullRequest.number'
)
thread_repo_owner=$(
  gh api graphql \
    -f query='query($tid:ID!){ node(id:$tid){ ... on PullRequestReviewThread { pullRequest { repository { owner { login } } } } } }' \
    -f tid="$tid" \
    --jq '.data.node.pullRequest.repository.owner.login'
)
thread_repo_name=$(
  gh api graphql \
    -f query='query($tid:ID!){ node(id:$tid){ ... on PullRequestReviewThread { pullRequest { repository { name } } } } }' \
    -f tid="$tid" \
    --jq '.data.node.pullRequest.repository.name'
)

if [[ -z "${thread_pr_number}" || "${thread_pr_number}" == "null" ]]; then
  echo "error: thread-id '$tid' was not found or is not a pull request review thread" >&2
  exit 1
fi

if [[ -z "${thread_repo_owner}" || "${thread_repo_owner}" == "null" || -z "${thread_repo_name}" || "${thread_repo_name}" == "null" ]]; then
  echo "error: could not determine repository for thread-id '$tid'; aborting" >&2
  exit 1
fi

if [[ "${thread_repo_owner}" != "${expected_owner}" || "${thread_repo_name}" != "${expected_repo}" ]]; then
  echo "error: thread-id '$tid' belongs to ${thread_repo_owner}/${thread_repo_name}, expected ${expected_owner}/${expected_repo}; aborting" >&2
  exit 1
fi

if [[ "${thread_pr_number}" != "${pr}" ]]; then
  echo "error: thread-id '$tid' belongs to PR #${thread_pr_number}, not PR #${pr}; aborting" >&2
  exit 1
fi

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "error: interactive TTY confirmation is required; aborting in non-interactive context." >&2
  exit 1
fi

echo "About to post a reply and resolve thread '$tid' on PR #$pr." >&2
read -r -p "Proceed? [y/N] " confirm
if [[ ! "${confirm}" =~ ^([yY]|[yY][eE][sS])$ ]]; then
  echo "aborted" >&2
  exit 1
fi

if [[ -z "${PR_THREAD_REPLY_APPROVAL_TOKEN:-}" ]]; then
  echo "error: PR_THREAD_REPLY_APPROVAL_TOKEN must be set for out-of-band approval." >&2
  exit 1
fi

read -r -s -p "Enter approval token: " approval_token_input
echo >&2
if [[ "${approval_token_input}" != "${PR_THREAD_REPLY_APPROVAL_TOKEN}" ]]; then
  echo "error: approval token mismatch; aborting" >&2
  exit 1
fi
unset approval_token_input

gh api graphql -f query='mutation($tid:ID!,$body:String!){ addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$tid, body:$body}){ clientMutationId } }' -f tid="$tid" -f body="$body"
gh api graphql -f query='mutation($tid:ID!){ resolveReviewThread(input:{threadId:$tid}){ thread{ isResolved } } }' -f tid="$tid"
