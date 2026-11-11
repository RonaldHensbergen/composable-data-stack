#!/usr/bin/env bash
# pr_thread_reply.sh [--yes] <pr> <thread-id> <reply-body> — reply to a review thread and resolve it, one call.
# --yes requires PR_THREAD_REPLY_CONFIRM='pr:<pr>|tid:<thread-id>|body:<reply-body>' to bypass interactive confirmation.
set -euo pipefail
assume_yes=0
if [[ "${1:-}" == "--yes" ]]; then
  assume_yes=1
  shift
fi

pr=${1:?usage: PR_THREAD_REPLY_CONFIRM='pr:<pr>|tid:<thread-id>|body:<reply-body>' pr_thread_reply.sh [--yes] <pr> <thread-id> <reply-body>}
tid=${2:?missing thread-id}
body=${3:?missing reply-body}

if [[ "${assume_yes}" -eq 1 ]]; then
  expected_confirm="pr:${pr}|tid:${tid}|body:${body}"
  if [[ "${PR_THREAD_REPLY_CONFIRM:-}" != "${expected_confirm}" ]]; then
    echo "error: --yes requires action-bound confirmation." >&2
    echo "set PR_THREAD_REPLY_CONFIRM='${expected_confirm}'" >&2
    exit 1
  fi
fi

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
