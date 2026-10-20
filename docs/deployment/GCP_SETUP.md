# GCP setup (one-time, administrator)

None of these commands have been executed against `absolute-access-415010` yet. The build environment's
account (`info.beeneural@gmail.com`) has no access to that project. Run them as an account with
`roles/owner`, or at minimum: Service Usage Admin, Compute Network Admin, Cloud SQL Admin, Cloud Run Admin,
Service Account Admin, Project IAM Admin, Workload Identity Pool Admin, Secret Manager Admin,
Storage Admin, Cloud Tasks Admin, Cloud Scheduler Admin, Artifact Registry Admin, Monitoring Admin,
and Logging Admin (plus Billing Account Costs Manager for the optional budget).

```bash
export PROJECT_ID=absolute-access-415010
export REGION=asia-south1
gcloud auth login
gcloud auth application-default login          # Terraform uses ADC
gcloud config set project "$PROJECT_ID"
```

## 1. Discover existing resources (read-only)

```bash
./infra/scripts/discover.sh
```

If any resource already exists (Artifact Registry repo `beeproof`, a database, a bucket, service
accounts, a WIF pool), copy `infra/terraform/imports.tf.example` to `imports.tf`, keep only the
matching blocks, and confirm that the plan shows **import**, not create or replace.

The script also reports organization policies. Two of them matter here:
- `iam.allowedPolicyMemberDomains` must allow `allUsers` as invoker on `beeproof-web` and `beeproof-api`.
  These are still unreachable from the internet (ingress is internal), but the policy blocks the binding itself.
- `run.allowedIngress` must allow `internal`.

## 2. Terraform state bucket

```bash
PROJECT_ID=$PROJECT_ID REGION=$REGION ./infra/bootstrap/create-state-bucket.sh
```

This creates `gs://absolute-access-415010-beeproof-tfstate` with versioning, uniform access and public
access prevention. Only infrastructure administrators should have access to it: the state contains the
generated database passwords.

## 3. Plan and apply

```bash
cd infra/terraform
cp environments/production.tfvars.example environments/production.tfvars   # edit: gemini_model, docai, alert_email
terraform init -backend-config="bucket=${PROJECT_ID}-beeproof-tfstate"
terraform fmt -check -recursive && terraform validate
terraform plan -var-file=environments/production.tfvars -out=prod.tfplan
```

**Review the plan.** Abort if it shows `destroy` or `must be replaced` for an existing database,
bucket or secret. Then:

```bash
terraform apply prod.tfplan
terraform output github_actions_variables
terraform output cloudflare_tunnel_route
```

First apply notes:
- APIs are enabled by Terraform (`google_project_service`). If an apply fails with "API has not been used",
  wait a minute and re-run `plan`/`apply`.
- Cloud SQL creation takes about 10 minutes. The Private Service Access peering is created first.
- Cloud Run services start with a placeholder image (`us-docker.pkg.dev/cloudrun/container/hello`). The first
  GitHub deployment replaces it. Terraform ignores image and traffic afterwards, so later applies never roll
  back a release.

## 4. Optional AI services

```bash
# Gemini: choose an exact model ID that Model Garden lists for this project and region,
# then set gemini_model in production.tfvars and re-apply.

# Document AI Form Parser (location us or eu):
curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" \
  -d '{"type":"FORM_PARSER_PROCESSOR","displayName":"beeproof-form"}' \
  "https://us-documentai.googleapis.com/v1/projects/${PROJECT_ID}/locations/us/processors"
# Copy the processor id (last segment of "name") into docai_processor_id and re-apply.
```

## 5. Tunnel token into Secret Manager

Use either the GitHub workflow **Sync Cloudflare tunnel token** (preferred), or, locally, without echoing
the token:

```bash
read -rs TOKEN && printf '%s' "$TOKEN" | gcloud secrets versions add beeproof-cloudflare-tunnel-token --data-file=- && unset TOKEN
gcloud compute instances reset beeproof-tunnel-1 --zone asia-south1-a   # connector re-reads the token at boot
```

## 6. Verify

```bash
gcloud run services list --region $REGION --format="table(metadata.name,status.url,metadata.annotations.'run.googleapis.com/ingress')"
gcloud compute ssh beeproof-tunnel-1 --zone asia-south1-a --tunnel-through-iap -- 'systemctl is-active cloudflared && curl -fsS 127.0.0.1:20241/ready'   # needs enable_iap_ssh=true
gcloud logging read 'resource.type="gce_instance" AND textPayload:"cloudflared"' --limit 20 --freshness 1h
```
