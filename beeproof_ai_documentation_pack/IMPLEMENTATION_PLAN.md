# Implementation Plan and Coding-Agent Work Breakdown

## 1. Execution model

Use one lead agent to own architecture and integration. Delegate bounded
tasks to specialist agents only after shared contracts are approved.
Every task must include scope, dependencies, deliverables, test
commands, and acceptance criteria.

## 2. Phase 0 --- Repository audit

Tasks: - inspect existing repository and Git status; - identify current
framework, conventions, and code to preserve; - inspect supplied
competition files and verify their actual schema; - list environment
assumptions; - create ADR directory and implementation checklist.

Exit criteria: - repository audit committed; - no unreviewed destructive
changes; - architecture and dependency decisions documented.

## 3. Phase 1 --- Skeleton and contracts

Tasks: - create app/service/package structure; - configure lint,
formatting, typing, tests; - define domain models and status enums; -
define schema and evidence contracts; - define API error convention; -
configure Docker Compose and `.env.example`; - add CI.

Exit criteria: - local skeleton starts; - migrations run; - contracts
have tests; - CI passes.

## 4. Phase 2 --- Ingestion and jobs

Tasks: - upload validation; - object storage adapter; - document
metadata; - schema selection; - job creation; - queue dispatch; - job
status API; - idempotent worker claim; - mock parser path.

Exit criteria: - upload-to-queued-job workflow works locally; -
duplicate queue messages are safe.

## 5. Phase 3 --- Parsing and layout

Tasks: - native PDF extraction; - image preprocessing; - Document AI
adapter; - provider-neutral layout representation; - table/cell/header
graph; - coordinate normalization; - page preview API.

Exit criteria: - fixtures produce stable layout artifacts; - evidence
coordinates map correctly to previews; - provider failures are handled.

## 6. Phase 4 --- Structured extraction

Tasks: - schema versioning; - Gemini adapter; - bounded context
builder; - typed extraction output; - Pydantic validation; -
raw/normalized value persistence; - evidence linking.

Exit criteria: - one supported rate-sheet schema produces persisted
structured candidates; - invalid output fails safely; - no unsupported
values are silently fabricated.

## 7. Phase 5 --- Validation and acceptance gate

Tasks: - field-level rules; - record-level rules; - cross-record
rules; - severity model; - acceptance policy; - audit events.

Exit criteria: - mandatory rule failures block auto-acceptance; - tests
cover all legal state transitions.

## 8. Phase 6 --- Repair

Tasks: - error taxonomy; - allowlisted tool registry; - repair
planner; - global retry and cost budgets; - targeted reprocessing; -
revalidation; - escalation; - trace UI data contract.

Exit criteria: - seeded failure reliably triggers a known action; -
repair is bounded; - unresolved cases reach review; - no repeated no-op
loop.

## 9. Phase 7 --- Human review

Tasks: - review queue; - source evidence viewer; - field correction; -
validation on correction; - immutable history; - feedback-example
records.

Exit criteria: - reviewer can correct one field; - previous value and
decision are preserved; - dependent rules rerun correctly.

## 10. Phase 8 --- Frontend

Tasks: - dashboard; - upload page; - document view; - evidence
highlight overlay; - extracted table; - action timeline; - review
queue; - export flow; - evaluation dashboard.

Exit criteria: - all screens use live API data; - loading/error/empty
states exist; - no fake metrics are presented as actual results; - key
flows pass Playwright tests.

## 11. Phase 9 --- Benchmark

Tasks: - inspect actual competition dataset; - create data manifest; -
define labels and normalization; - implement baseline; - implement
enhanced pipeline runner; - calculate metrics; - produce report and
field-level diff; - add adversarial fixtures.

Exit criteria: - benchmark is reproducible; - denominators and
exclusions are explicit; - held-out data remains independent.

## 12. Phase 10 --- GCP

Tasks: - Terraform infrastructure; - service accounts and IAM; - Cloud
SQL and storage; - Pub/Sub; - Cloud Run API and worker; - provider
configuration; - secrets; - CI/CD; - smoke tests; - budgets and logs.

Exit criteria: - documented deployment succeeds; - real sample document
completes; - rollback and operational diagnostics are documented.

## 13. Phase 11 --- Demo readiness

Tasks: - freeze release candidate; - run full test suite; - benchmark
final candidate; - select representative documents; - seed reproducible
repair scenario; - prepare backup local demo; - rehearse timed
presentation; - document limitations.

Exit criteria: - full demonstration succeeds twice consecutively; -
backup path works; - all claimed metrics can be traced to reports.

## 14. Agent ownership

### Lead/architect

Owns contracts, architecture, integration, code review, release gates,
and final verification.

### Backend agent

Owns API, database models, migrations, jobs, review
persistence, and exports.

### Document-processing agent

Owns ingestion, preprocessing, parser adapters, layout graph, table
reconstruction, and coordinate mapping.

### Agentic-extraction agent

Owns structured extraction, normalization, validation rules, risk
routing, repair planner, and budgets.

### Frontend agent

Owns upload, dashboard, document viewer, evidence overlay, trace,
review, and evaluation UI.

### Evaluation/infra agent

Owns benchmark, fixtures, CI, Docker, Terraform, deployment and smoke
tests.

If fewer agents are available, combine roles. Do not parallelize tightly
coupled files without explicit ownership.

## 15. Definition of done for every task

-   Code implemented.
-   Tests added and executed.
-   Types and lint pass.
-   No secrets or sensitive fixture data.
-   Error paths handled.
-   Documentation updated.
-   Changed files listed.
-   Commands and actual test results reported.
-   Remaining limitations declared.

## 16. Lead-agent starting prompt

You are the principal engineer for BeeProof AI. First audit the
repository and available competition assets. Do not assume files,
services, or schemas exist until verified. Establish shared contracts,
state transitions, and evidence semantics before delegating work.
Implement in phases, continuously integrate, run tests, and report
actual results. Never fabricate metrics or mark unexecuted tests as
passing. Build the first end-to-end vertical slice before advanced
features. Keep the application runnable locally throughout development.
