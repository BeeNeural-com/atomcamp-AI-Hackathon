# Security, Privacy and Threat Model

## 1. Security objectives

-   Protect API keys and service credentials.
-   Limit resource exhaustion from hostile files.
-   Prevent document content from changing system policy.
-   Preserve audit integrity.
-   Avoid unnecessary exposure of sensitive content.
-   Support retention and deletion obligations.

## 2. Trust boundaries

1.  Browser to API.
2.  User-uploaded files to processing services.
3.  Document text to model prompts.
4.  Application to third-party/cloud providers.
5.  API/worker to database and object storage.
6.  CI/CD to production infrastructure.
7.  Reviewer actions to immutable audit records.

## 3. File security

-   Check file signature and parser compatibility.
-   Enforce maximum bytes, pages, pixels, and decompressed resource
    usage.
-   Reject malformed or unsupported files safely.
-   Use generated storage keys.
-   Prevent path traversal.
-   Keep the storage bucket non-public; serve files through the API.
-   Consider malware scanning according to deployment requirements.
-   Never execute embedded scripts, macros, or arbitrary content.
-   Apply parser timeouts and worker resource limits.

## 4. Prompt injection

Documents may contain text such as "ignore previous instructions" or
malicious tool directives. Treat all extracted document content as
untrusted data.

Controls: - Keep system policy and document content clearly separated. -
Do not allow document text to grant permissions. - Do not allow document
content to choose arbitrary tools. - Use structured, allowlisted tool
calls. - Validate all tool arguments in application code. - Do not
expose arbitrary shell, SQL, filesystem, or network access. - Require
evidence and deterministic validation independently of model claims. -
Test malicious prompt-like text embedded in PDFs and images.

## 5. Access model

The application is intentionally open (see
`ADR/ADR-004-open-access.md`). There is no authentication,
authorization, user account, role or workspace.

-   Anyone who can reach the URL can upload, view, review, correct and
    export.
-   Upload only synthetic or publicly available documents.
-   Reviewer names are optional free text and are not verified.
-   Log review and export actions with timestamp and request ID.

## 6. Secrets and identity

-   Local secrets belong in an ignored `.env` file.
-   Production secrets belong in Secret Manager or equivalent.
-   Prefer workload identity for CI/CD.
-   Use separate deployment and runtime service accounts.
-   Never embed secrets in frontend bundles.
-   Rotate credentials according to policy.
-   Ensure logs and error messages do not reveal credentials.

## 7. Data protection

-   Encrypt traffic in transit.
-   Use provider/platform encryption at rest.
-   Define document retention and deletion.
-   Avoid logging full document contents by default.
-   Store only required provider responses.
-   Define whether data is sent to external model providers and document
    the applicable provider terms.
-   Do not use customer documents for model improvement without an
    explicit, lawful policy and appropriate consent.

## 8. Audit integrity

-   Append review decisions and agent actions.
-   Expose no update or delete endpoints for audit records.
-   Record timestamp, reason, result version, and optional reviewer
    name.
-   Preserve previous field values.
-   Ensure export provenance references the selected result version.

## 9. Operational controls

-   Global and per-client-IP upload limits.
-   Per-job provider-call and cost limits.
-   API rate limits.
-   Worker concurrency limits.
-   Timeouts and bounded retries.
-   Dependency and container scanning.
-   Database backups and restore tests.
-   Security event logging.

## 10. Security acceptance tests

-   Invalid file signatures are rejected.
-   Oversized files are rejected before expensive processing.
-   Prompt injection cannot trigger non-allowlisted tool use.
-   API keys are absent from client bundles and logs.
-   Deletion follows the documented retention model.
-   Audit history is readable and cannot be modified through the API.
