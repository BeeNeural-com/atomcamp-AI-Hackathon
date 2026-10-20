# BeeProof AI --- Documentation Pack

**Product:** BeeProof AI\
**Organization:** BeeNeural\
**Competition track:** PS-06 --- Automated data extraction from
unstructured documents (PDFs and images)\
**Document status:** Engineering blueprint; implementation details must
be verified against the actual repository and competition brief.

## 1. What this pack contains

This documentation defines the intended product, architecture,
contracts, processing workflow, agent responsibilities, evaluation
approach, security controls, testing strategy, deployment model, and
coding-agent execution plan.

  -----------------------------------------------------------------------
  File                                Purpose
  ----------------------------------- -----------------------------------
  `PRODUCT_SPEC.md`                   Product requirements, users,
                                      workflows, scope, acceptance
                                      criteria

  `ARCHITECTURE.md`                   System architecture, component
                                      boundaries, data flow, deployment
                                      topology

  `DATA_MODEL.md`                     Domain entities, relationships,
                                      status models, persistence rules

  `API_CONTRACTS.md`                  REST endpoint inventory,
                                      request/response conventions, error
                                      contract

  `AGENT_DESIGN.md`                   Agent/state-machine design, tool
                                      permissions, repair policy

  `DOCUMENT_PIPELINE.md`              Ingestion, OCR/layout parsing,
                                      evidence mapping, extraction and
                                      repair

  `EVALUATION.md`                     Dataset strategy, metrics,
                                      benchmark methodology, reporting

  `SECURITY.md`                       Threat model, privacy,
                                      prompt-injection controls,
                                      open-access model

  `DEPLOYMENT.md`                     Local development, GCP deployment,
                                      CI/CD and operational guidance

  `TESTING.md`                        Unit, integration, end-to-end,
                                      resilience and acceptance tests

  `IMPLEMENTATION_PLAN.md`            Phased build plan and coding-agent
                                      work allocation

  `DEMO_SCRIPT.md`                    Competition demonstration plan and
                                      fallback procedure

  `ADR/ADR-001-modular-monolith.md`   Architecture decision record

  `ADR/ADR-002-evidence-first.md`     Architecture decision record

  `ADR/ADR-003-bounded-repair.md`     Architecture decision record

  `ADR/ADR-004-open-access.md`        Architecture decision record
  -----------------------------------------------------------------------

## 2. Product summary

BeeProof AI is an evidence-first document intelligence platform. It
ingests PDFs and images, reconstructs document layout, extracts
schema-conformant values, validates those values deterministically,
attempts bounded repairs for recoverable failures, and routes unresolved
ambiguity to human review.

The core differentiator is not simply OCR or LLM extraction. It is the
combination of:

-   field-level source provenance;
-   deterministic validation;
-   bounded, observable repair;
-   human review with version history;
-   repeatable evaluation against labeled data.

## 3. Important implementation caveat

This is a specification, not evidence that the product has already been
implemented or deployed. Replace planned behavior with actual
implementation details as development proceeds. Do not claim accuracy,
speed, savings, or reliability until measured on a documented evaluation
set.

## 4. Access model

BeeProof AI is open by design. There is no authentication,
authorization, user account, role or workspace. Every endpoint and
screen is usable by anyone who can reach the URL. See
`ADR/ADR-004-open-access.md`.

## 5. Suggested reading order

1.  `PRODUCT_SPEC.md`
2.  `ARCHITECTURE.md`
3.  `DATA_MODEL.md`
4.  `DOCUMENT_PIPELINE.md`
5.  `AGENT_DESIGN.md`
6.  `API_CONTRACTS.md`
7.  `EVALUATION.md`
8.  `SECURITY.md`
9.  `TESTING.md`
10. `IMPLEMENTATION_PLAN.md`
11. `DEPLOYMENT.md`
12. `DEMO_SCRIPT.md`

## 6. Initial stack recommendation

-   Frontend: Next.js, React, TypeScript, Tailwind CSS.
-   API: Python, FastAPI, Pydantic.
-   Database: PostgreSQL.
-   Local queue: Redis-backed worker queue or a documented equivalent.
-   Production queue: Google Cloud Pub/Sub.
-   PDF processing: PyMuPDF.
-   OCR/layout: Google Cloud Document AI behind an adapter.
-   LLM: Gemini through Vertex AI behind an adapter.
-   Local object storage: MinIO or a filesystem adapter.
-   Production object storage: Google Cloud Storage.
-   Deployment: Docker, Cloud Run, Cloud SQL, Terraform, GitHub Actions.

Confirm service availability, supported regions, quotas, processor
selection, and pricing before provisioning GCP resources.
