#!/usr/bin/env bash
# pr_thread_reply.sh <pr> <thread-id> <reply-body> — reply to a review thread and resolve it, one call.
set -euo pipefail
pr=${1:?usage: pr_thread_reply.sh <pr> <thread-id> <reply-body>}
tid=${2:?missing thread-id}
body=${3:?missing reply-body}
gh api graphql -f query='mutation($tid:ID!,$body:String!){ addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$tid, body:$body}){ clientMutationId } }' -f tid="$tid" -f body="$body"
gh api graphql -f query='mutation($tid:ID!){ resolveReviewThread(input:{threadId:$tid}){ thread{ isResolved } } }' -f tid="$tid"
