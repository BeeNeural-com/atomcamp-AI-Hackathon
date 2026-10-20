#!/usr/bin/env bash
# One-time: create the Terraform remote-state bucket (cannot be managed by the state it holds).
# Idempotent: re-running only re-applies the protective settings.
#   PROJECT_ID=absolute-access-415010 REGION=asia-south1 ./infra/bootstrap/create-state-bucket.sh
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
BUCKET="${STATE_BUCKET:-${PROJECT_ID}-beeproof-tfstate}"

gcloud services enable storage.googleapis.com --project "$PROJECT_ID"

if gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "state bucket gs://${BUCKET} already exists"
else
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "$PROJECT_ID" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi

# Versioning lets you recover a previous state file; keep 10 noncurrent versions for 90 days.
gcloud storage buckets update "gs://${BUCKET}" --versioning --public-access-prevention
cat > /tmp/beeproof-tfstate-lifecycle.json <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"isLive": false, "numNewerVersions": 10, "daysSinceNoncurrentTime": 90}}]}
JSON
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file=/tmp/beeproof-tfstate-lifecycle.json
rm -f /tmp/beeproof-tfstate-lifecycle.json

echo
echo "State bucket ready. Initialise Terraform with:"
echo "  cd infra/terraform && terraform init -backend-config=\"bucket=${BUCKET}\""
echo "Restrict access: only infrastructure administrators need roles/storage.objectAdmin on this bucket."
