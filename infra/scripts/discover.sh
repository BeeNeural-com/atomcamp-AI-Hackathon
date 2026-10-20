#!/usr/bin/env bash
# Read-only inventory of BeeProof-related resources, run BEFORE the first terraform plan so
# existing resources are imported (infra/terraform/imports.tf.example) instead of duplicated.
#   PROJECT_ID=absolute-access-415010 REGION=asia-south1 ./infra/scripts/discover.sh
set -uo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
P=(--project "$PROJECT_ID")

section() { printf '\n=== %s\n' "$1"; }

section "Caller and project"
gcloud config get-value account 2>/dev/null
gcloud projects describe "$PROJECT_ID" --format="value(projectId,projectNumber,lifecycleState)" || {
  echo "No access to $PROJECT_ID with the active account. Stop here."; exit 1; }

section "Enabled APIs (relevant)"
gcloud services list --enabled "${P[@]}" --format="value(config.name)" | grep -E \
  "^(run|artifactregistry|cloudbuild|sqladmin|cloudtasks|cloudscheduler|aiplatform|documentai|secretmanager|compute|servicenetworking|iam|iamcredentials|sts|storage|logging|monitoring|cloudresourcemanager|billingbudgets)\.googleapis\.com$" | sort

section "Artifact Registry"
gcloud artifacts repositories list "${P[@]}" --location "$REGION" --format="table(name,format,createTime)"

section "Cloud Run services and jobs"
gcloud run services list "${P[@]}" --region "$REGION" --format="table(metadata.name,status.url,metadata.annotations.'run.googleapis.com/ingress')"
gcloud run jobs list "${P[@]}" --region "$REGION" --format="table(metadata.name)"

section "Cloud SQL"
gcloud sql instances list "${P[@]}" --format="table(name,databaseVersion,region,settings.tier,ipAddresses)"

section "Cloud Storage buckets"
gcloud storage buckets list "${P[@]}" --format="table(name,location,public_access_prevention)"

section "Cloud Tasks queues / Scheduler jobs"
gcloud tasks queues list "${P[@]}" --location "$REGION" --format="table(name,state)"
gcloud scheduler jobs list "${P[@]}" --location "$REGION" --format="table(name,schedule,state)" 2>/dev/null

section "Service accounts"
gcloud iam service-accounts list "${P[@]}" --format="table(email,displayName,disabled)"

section "Workload Identity pools / providers"
gcloud iam workload-identity-pools list "${P[@]}" --location global --format="table(name,state)"
for pool in $(gcloud iam workload-identity-pools list "${P[@]}" --location global --format="value(name.basename())"); do
  gcloud iam workload-identity-pools providers list "${P[@]}" --location global --workload-identity-pool "$pool" \
    --format="table(name,state,attributeCondition)"
done

section "Secrets (names only, never values)"
gcloud secrets list "${P[@]}" --format="table(name,createTime)"

section "Networks, subnets, load balancers, VMs"
gcloud compute networks list "${P[@]}" --format="table(name,subnetMode)"
gcloud compute networks subnets list "${P[@]}" --regions "$REGION" --format="table(name,network.basename(),ipCidrRange,purpose)"
gcloud compute forwarding-rules list "${P[@]}" --regions "$REGION" --format="table(name,IPAddress,loadBalancingScheme)"
gcloud compute instances list "${P[@]}" --format="table(name,zone.basename(),status,networkInterfaces[0].networkIP)"

section "Organization policies that commonly block this design"
for c in iam.allowedPolicyMemberDomains iam.disableServiceAccountKeyCreation run.allowedIngress compute.vmExternalIpAccess; do
  printf '%s: ' "$c"
  gcloud resource-manager org-policies describe "$c" --project "$PROJECT_ID" --effective --format="value(listPolicy,booleanPolicy)" 2>/dev/null || echo "(not readable)"
done

echo
echo "Done. Nothing was modified."
