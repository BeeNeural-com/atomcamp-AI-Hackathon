# Rollback

## Automatic

If anything fails after traffic has shifted (including the public smoke test), `deploy-production.yml`
restores the revisions that were serving before the run.

A failure *before* the shift changes nothing:
- a failed migration;
- a revision that fails its startup probe;
- a build failure.

## Manual application rollback

```bash
REGION=asia-south1
# list recent revisions
gcloud run revisions list --service beeproof-api --region $REGION --limit 5
# send all traffic back to a known-good revision (repeat for beeproof-web / beeproof-worker)
gcloud run services update-traffic beeproof-api --region $REGION --to-revisions beeproof-api-00012-abc=100
```

Or re-run **Deploy production** (`workflow_dispatch`) on a commit you have reverted on `main`.

## Database

**Rolling back the application does not reverse database migrations.** Migrations must therefore follow
expand → deploy → contract:
- add nullable columns and new tables first;
- only drop or rename in a later release, once no deployed revision uses the old shape.

That way the previous revision keeps working against the new schema. The migration template states this
policy (`beeproof/migrations/script.py.mako`).

If a migration itself corrupted data:

```bash
# Point-in-time recovery creates a NEW instance; it never overwrites production.
gcloud sql instances clone beeproof-pg beeproof-pg-restore-$(date +%Y%m%d%H%M) \
  --point-in-time "2026-10-20T08:00:00Z"
# or restore a nightly backup into a new instance:
gcloud sql backups list --instance beeproof-pg
```

After verifying the restored instance, update the `beeproof-database-url` and `beeproof-migrator-database-url`
secrets (new private IP), then deploy a new revision. Never run `alembic downgrade` automatically against
production.

## Infrastructure

Terraform changes are applied manually after review. To undo one, revert the commit and run
`terraform plan`/`apply`. Deletion protection on the database and the Cloud Run services blocks accidental
destroys.
