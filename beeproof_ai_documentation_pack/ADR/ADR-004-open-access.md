# ADR-004: Open Access, No Authentication or Authorization

-   **Status:** Accepted
-   **Context:** The hackathon build has a short window, a single
    rate-sheet use case, and judges who need to try the product without
    setting up accounts.
-   **Decision:** Ship without authentication, authorization, user
    accounts, roles or workspaces. Every API endpoint and UI screen is
    public. Cloud Run services deploy with `--allow-unauthenticated`.
    GCP service-account IAM stays, because the backend still needs
    credentials to call Document AI, Vertex AI and storage.
-   **Alternatives:** Identity Platform or Firebase Auth with workspace
    roles; a shared demo password; Cloud Run IAM invoker restrictions.
-   **Rationale:** Removes a whole module, its data model and its tests,
    and keeps the demo frictionless.
-   **Consequences:** Anyone with the URL can upload, review, correct and
    export. Use only synthetic or public documents. Reviewer identity is
    optional free text and unverified. Upload, page, call and cost limits
    are the main protection against abuse.
-   **Revisit when:** Real or sensitive documents are processed, or more
    than one organization uses the same deployment.
