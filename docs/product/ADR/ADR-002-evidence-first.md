# ADR-002: Make Evidence a First-Class Domain Object

-   **Status:** Proposed
-   **Context:** Extraction values are not sufficiently trustworthy if
    users cannot trace them to the source document.
-   **Decision:** Store field-to-evidence relationships with page
    number, region, coordinates, source text, and artifact references.
    Require evidence for fields whose schema/policy mandates it.
-   **Alternatives:** Store only raw OCR text; keep evidence in
    transient frontend state; store only page-level references.
-   **Rationale:** First-class evidence enables source highlighting,
    auditability, validation context, and evaluation of localization
    correctness.
-   **Consequences:** Coordinate normalization and parser-to-canonical
    mappings must be tested. Multiple evidence regions may be needed for
    headers, units, and footnotes.
-   **Revisit when:** A schema explicitly supports fields whose
    provenance comes from non-document sources; those sources must still
    be represented through an equivalent provenance contract.
