#!/usr/bin/env bash
# Exercise TenderNed ingestion and visualization against this worktree's real k3s stack.
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s/k3d-env.sh
source "${SCRIPT_DIR}/../k8s/k3d-env.sh"

FEATURE_ROOT="${CDS_TENDER_PROOF_REPO_ROOT:-$CDS_REPO_ROOT}"
SNAPSHOT="${CDS_TENDER_SNAPSHOT:-${CDS_REPO_ROOT}/data/tenderned/tenderned-tenders.csv.gz}"
LOAD_LOG="/tmp/${CDS_CLUSTER}-tender-load-e2e.log"
BROWSER_LOG="/tmp/${CDS_CLUSTER}-tender-browser-e2e.log"
PASS_COUNT=0
FAIL_COUNT=0
BROWSER_SESSION="tender-e2e-${CDS_SLUG}-$$"

cleanup() {
  playwright-cli -s="$BROWSER_SESSION" close >/dev/null 2>&1 || true
}
trap cleanup EXIT

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL %s\n' "$1"
}

check_cluster() {
  local readiness
  readiness="$(kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" get pods -o json | jq -r '
    [.items[] | select(.metadata.deletionTimestamp == null)] as $pods
    | (($pods | length) >= 6 and all($pods[]; all(.status.containerStatuses[]?; .ready == true)))
  ')"
  [[ "$readiness" == "true" ]]
}

snapshot_rows() {
  python3 -c '
import csv
import gzip
import sys

with gzip.open(sys.argv[1], "rt", encoding="utf-8", newline="") as handle:
    print(sum(1 for _ in csv.reader(handle)) - 1)
' "$SNAPSHOT"
}

warehouse_state() {
  kubectl --context "$CDS_CONTEXT" --namespace "$CDS_NAMESPACE" \
    exec "${CDS_RELEASE}-postgres-0" -- sh -ceu '
      export PGPASSWORD="$ANALYTICS_DB_PASSWORD"
      psql -X -At -F "|" -v ON_ERROR_STOP=1 \
        -U "$ANALYTICS_DB_USER" -d "$ANALYTICS_DB_NAME" \
        -c "SELECT COUNT(*), COUNT(DISTINCT publicatie_id),
                   COUNT(*) FILTER (WHERE has_pdf), MAX(snapshot_loaded_at)
            FROM tender_analytics.tenders"
    '
}

check_materialization() {
  local expected actual rows distinct pdf
  [[ -x "${FEATURE_ROOT}/scripts/tender/load-snapshot.sh" ]] || return 1
  [[ -f "$SNAPSHOT" ]] || return 1
  expected="$(snapshot_rows)" || return 1
  CDS_TENDER_LOAD_TIMEOUT=120 gtimeout 180s \
    "${FEATURE_ROOT}/scripts/tender/load-snapshot.sh" "$SNAPSHOT" \
    >"$LOAD_LOG" 2>&1 || return 1
  actual="$(warehouse_state)" || return 1
  IFS='|' read -r rows distinct pdf _ <<<"$actual"
  [[ "$rows" == "$expected" && "$distinct" == "$expected" && "$pdf" -gt 0 ]]
}

check_noop_load() {
  local before after
  [[ -x "${FEATURE_ROOT}/scripts/tender/load-snapshot.sh" ]] || return 1
  before="$(warehouse_state | awk -F '|' '{print $4}')" || return 1
  CDS_TENDER_LOAD_TIMEOUT=120 gtimeout 180s \
    "${FEATURE_ROOT}/scripts/tender/load-snapshot.sh" "$SNAPSHOT" \
    >"$LOAD_LOG" 2>&1 || return 1
  after="$(warehouse_state | awk -F '|' '{print $4}')" || return 1
  [[ -n "$before" && "$before" == "$after" ]]
}

check_dashboard_provisioning() {
  local first second
  [[ -x "${FEATURE_ROOT}/scripts/tender/provision-dashboard.sh" ]] || return 1
  first="$(gtimeout 60s "${FEATURE_ROOT}/scripts/tender/provision-dashboard.sh")" || return 1
  second="$(gtimeout 60s "${FEATURE_ROOT}/scripts/tender/provision-dashboard.sh")" || return 1
  [[ "$first" == *"dashboard_id=1"* && "$first" == *"charts=7"* ]]
  [[ "$second" == *"dashboard_id=1"* && "$second" == *"charts=7"* ]]
}

api_token() {
  local login_body
  login_body="$(jq -n \
    --arg username "${SUPERSET_ADMIN_USERNAME:-admin}" \
    --arg password "$CDS_SUPERSET_ADMIN_PASSWORD" \
    '{username:$username,password:$password,provider:"db",refresh:true}')"
  curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    -d "$login_body" \
    "http://127.0.0.1:${CDS_SUPERSET_PORT}/api/v1/security/login" \
    | jq -r .access_token
}

check_superset_objects() {
  local token dashboards charts
  token="$(api_token)" || return 1
  dashboards="$(curl --fail --silent --show-error \
    -H "Authorization: Bearer $token" \
    "http://127.0.0.1:${CDS_SUPERSET_PORT}/api/v1/dashboard/?q=(page:0,page_size:100)" \
    | jq '[.result[] | select(.slug == "tender-analytics")] | length')" || return 1
  charts="$(curl --fail --silent --show-error \
    -H "Authorization: Bearer $token" \
    "http://127.0.0.1:${CDS_SUPERSET_PORT}/api/v1/chart/?q=(page:0,page_size:100)" \
    | jq '[.result[] | select(.datasource_name_text == "tender_analytics.tenders_dashboard")] | length')" || return 1
  [[ "$dashboards" == "1" && "$charts" == "7" ]]
}

check_browser() {
  local output result
  playwright-cli -s="$BROWSER_SESSION" open \
    "http://127.0.0.1:${CDS_SUPERSET_PORT}/login/" >/dev/null 2>&1 || return 1
  playwright-cli -s="$BROWSER_SESSION" localstorage-set \
    __cds_e2e_user "${SUPERSET_ADMIN_USERNAME:-admin}" >/dev/null 2>&1 || return 1
  playwright-cli -s="$BROWSER_SESSION" localstorage-set \
    __cds_e2e_password "$CDS_SUPERSET_ADMIN_PASSWORD" >/dev/null 2>&1 || return 1
  output="$(playwright-cli -s="$BROWSER_SESSION" run-code '
    async page => {
      const consoleErrors = [];
      const chartStatuses = [];
      page.on("console", message => {
        if (message.type() === "error") consoleErrors.push(message.text());
      });
      page.on("pageerror", error => consoleErrors.push(error.message));
      page.on("response", response => {
        if (response.url().includes("/api/v1/chart/data")) {
          chartStatuses.push(response.status());
        }
      });
      const credentials = await page.evaluate(() => {
        const values = {
          username: localStorage.getItem("__cds_e2e_user"),
          password: localStorage.getItem("__cds_e2e_password"),
        };
        localStorage.removeItem("__cds_e2e_user");
        localStorage.removeItem("__cds_e2e_password");
        return values;
      });
      await page.getByLabel("Username:").fill(credentials.username, {timeout: 30000});
      await page.getByLabel("Password:").fill(credentials.password, {timeout: 30000});
      await page.getByRole("button", {name: "Sign in"}).click({timeout: 30000});
      await page.waitForURL("**/superset/welcome/", {timeout: 30000});
      const origin = page.url().split("/").slice(0, 3).join("/");
      await page.goto(`${origin}/superset/dashboard/tender-analytics/`);
      const deadline = Date.now() + 30000;
      while (chartStatuses.length < 7 && Date.now() < deadline) {
        await page.waitForTimeout(100);
      }
      if (chartStatuses.length < 7) throw new Error("Timed out waiting for seven charts");
      const text = await page.locator("body").innerText();
      return {
        title: await page.title(),
        chartStatuses,
        consoleErrors,
        hasTotal: text.includes("Total tenders") && text.includes("150k"),
        hasRecent: text.includes("Published in last 30 days") && text.includes("1.1k"),
        hasPdf: text.includes("PDF enriched tenders") && text.includes("2.75k"),
        hasAuthorities: text.includes("Gemeente Rotterdam"),
      };
    }
  ')" || return 1
  printf '%s\n' "$output" >"$BROWSER_LOG"
  result="$(printf '%s\n' "$output" | awk '/^### Result$/{getline; print; exit}')"
  jq -e '
    .title == "TenderNed Analytics"
    and (.chartStatuses | length == 7)
    and all(.chartStatuses[]; . == 200)
    and (.consoleErrors | length == 0)
    and .hasTotal and .hasRecent and .hasPdf and .hasAuthorities
  ' <<<"$result" >/dev/null
}

if [[ ! -f "${CDS_REPO_ROOT}/.env" ]]; then
  fail "local .env exists"
else
  set -a
  # shellcheck disable=SC1091
  source "${CDS_REPO_ROOT}/.env"
  set +a
  pass "local .env exists"
fi

check_cluster && pass "all local k3s pods are ready" || fail "all local k3s pods are ready"
check_materialization && pass "Dagster materializes every snapshot row" || fail "Dagster materializes every snapshot row"
check_noop_load && pass "unchanged Dagster load performs no row rewrites" || fail "unchanged Dagster load performs no row rewrites"
check_dashboard_provisioning && pass "Superset provisioning converges twice" || fail "Superset provisioning converges twice"
check_superset_objects && pass "Superset owns one dashboard and seven tender charts" || fail "Superset owns one dashboard and seven tender charts"
check_browser && pass "browser renders seven charts with zero console errors" || fail "browser renders seven charts with zero console errors"

printf '=== TENDER ANALYTICS E2E DONE pass=%s fail=%s ===\n' "$PASS_COUNT" "$FAIL_COUNT"
[[ "$FAIL_COUNT" -eq 0 ]]
