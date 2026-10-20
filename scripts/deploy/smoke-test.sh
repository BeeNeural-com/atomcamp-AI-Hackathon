#!/usr/bin/env bash
# Post-deployment smoke test through the public hostname (Cloudflare -> Tunnel -> ILB -> Cloud Run).
#   scripts/deploy/smoke-test.sh https://proof.beeneural.com [expected-api-revision]
# Exits non-zero on failure, which triggers the traffic rollback in deploy-production.yml.
set -euo pipefail

BASE="${1:?usage: smoke-test.sh BASE_URL [EXPECTED_API_REVISION]}"
BASE="${BASE%/}"
EXPECT_REV="${2:-}"
ATTEMPTS="${SMOKE_ATTEMPTS:-20}"
SLEEP="${SMOKE_SLEEP_SECONDS:-6}"

fail() { echo "::error::smoke test: $*"; exit 1; }

echo "1/4 API health (database reachable${EXPECT_REV:+, served by $EXPECT_REV})"
ok=0
for i in $(seq 1 "$ATTEMPTS"); do
  body=$(curl -fsS --max-time 10 "$BASE/api/v1/health" 2>/dev/null || true)
  if echo "$body" | grep -q '"database_ok":true' \
     && { [ -z "$EXPECT_REV" ] || echo "$body" | grep -q "\"revision\":\"$EXPECT_REV\""; }; then
    ok=1; break
  fi
  echo "   attempt $i/$ATTEMPTS not ready yet"
  sleep "$SLEEP"
done
[ "$ok" = 1 ] || fail "health check did not pass: ${body:-no response}"

echo "2/4 frontend responds"
code=$(curl -sS -o /tmp/beeproof-web.html -w '%{http_code}' --max-time 15 "$BASE/")
[ "$code" = 200 ] || fail "GET / returned $code"
grep -qi "<html" /tmp/beeproof-web.html || fail "GET / did not return HTML"

echo "3/4 published extraction schema is served"
curl -fsS --max-time 10 "$BASE/api/v1/schemas" | grep -q '"status":"published"' || fail "no published schema"

echo "4/4 worker endpoints are not publicly reachable"
resp=$(curl -sS -X POST --max-time 10 "$BASE/internal/sweep" || true)
if echo "$resp" | grep -q '"reenqueued"'; then fail "worker endpoint reachable through the public hostname"; fi

echo "smoke test passed"
