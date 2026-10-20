# Document Processing Pipeline

## 1. Overview

The pipeline separates file ingestion, perception, layout
reconstruction, extraction, validation, repair, review, and export. Each
stage produces versioned artifacts and explicit status events.

## 2. Stage 1 --- Ingestion

1.  Validate extension, MIME declaration, file signature, size, and
    supported format.
2.  Enforce page-count and image-pixel limits.
3.  Compute SHA-256.
4.  Store the original using a server-generated key.
5.  Create document metadata.
6.  Record a sanitized ingestion event.
7.  Queue processing only after metadata and storage are consistent.

Never use the original filename as a storage path.

## 3. Stage 2 --- Preprocessing

### PDF

-   Check file readability.
-   Extract page count and dimensions.
-   Extract native text and positions.
-   Detect whether text is usable.
-   Render previews only as needed.
-   Preserve page rotation and source coordinate information.

### Images

-   Validate decoded dimensions.
-   Apply orientation correction where safe.
-   Preserve the original image.
-   Create a normalized working copy.
-   Detect low resolution, skew, and rotation.

Set hard resource ceilings to protect against malicious or pathological
files.

## 4. Stage 3 --- Triage

Choose an initial strategy based on observable signals: - digital
text; - scanned/image-heavy; - table-heavy; - mixed layout; -
rotated/low quality; - unsupported/ambiguous.

Use cheap deterministic signals first. Semantic classification can use a
model only where it adds value.

## 5. Stage 4 --- Parsing

Call the configured parser adapter. Store raw provider response as an
artifact and transform it into a provider-neutral representation.

Persist: - page dimensions; - text spans; - bounding polygons; - table
and cell structure; - detected headings and footnotes; - provider
confidence where available; - parser name/version; - processing
configuration.

Provider confidence is not the same as correctness.

## 6. Stage 5 --- Canonical layout graph

Create stable IDs for: - page; - text span; - region; - table; - row; -
cell; - header; - footnote; - continuation group.

Preserve parent/child relationships and source coordinates.

### Table reconstruction

-   Determine row and column order.
-   Preserve merged-cell spans.
-   Associate header rows with columns.
-   Capture units and footnotes.
-   Detect repeated headers.
-   Identify possible multi-page continuations.
-   Keep uncertain associations explicit.

Never merge tables solely because they occur on adjacent pages.

## 7. Stage 6 --- Schema-aware extraction

1.  Load the immutable schema version.
2.  Select relevant regions, tables, headers, and footnotes.
3.  Build a bounded context package.
4.  Request structured output from the model adapter.
5.  Validate the output with Pydantic.
6.  Validate evidence IDs against known artifacts.
7.  Persist candidate values and raw source text separately.
8.  Mark ambiguity explicitly.

Prompt instructions should tell the model to abstain rather than invent
a value. Source text is untrusted input and must not override system
policy.

## 8. Stage 7 --- Normalization

Use deterministic normalizers: - numeric separators; - decimal
precision; - date formats; - whitespace; - enumerations; - units; -
currency representation.

Record each transformation. Keep raw text intact. Avoid lossy
normalization. Unit conversions must be schema-defined and dimensionally
valid.

## 9. Stage 8 --- Validation

Run field-level, record-level, and cross-record rules. Store rule IDs
and versions, severity, outcome, actual/expected values, and evidence
references.

Separate: - blocking failures; - warnings; - informational findings.

A warning must not be silently treated as a pass.

## 10. Stage 9 --- Risk routing

Choose one: - accept candidate; - attempt bounded repair; - require
human review; - fail the job for a permanent error.

The policy is versioned and deterministic at the final acceptance gate.

## 11. Stage 10 --- Targeted repair

Prefer local repair over rerunning the entire document. Reprocess only
the relevant region/page/table when possible.

Every repair must: - cite a triggering failure; - use an allowlisted
action; - check budget; - store input and output artifacts; - record
duration and cost; - produce a new result version; - trigger
revalidation.

If no new evidence is obtained, avoid repeating the same action.

## 12. Stage 11 --- Human review

Present the source region, neighboring context, proposed value,
normalization, and validation findings. Record reviewer decision and
preserve previous versions. Revalidate corrected fields.

## 13. Stage 12 --- Finalization and export

Before marking a job completed: - verify all required result records are
persisted; - check mandatory acceptance conditions; - ensure evidence
references resolve; - ensure no blocking unresolved issue is hidden; -
commit the final result version; - emit a terminal job event.

Export only the explicitly selected result version. Document null
handling, column ordering, encoding, and decimal formats.

## 14. Coordinate handling

Use a canonical coordinate mapping: - provider coordinates → source page
coordinates; - source page coordinates → canonical preview
coordinates; - canonical preview coordinates → browser viewport
coordinates.

Account for page rotation, render scale, crop offsets, and aspect ratio.
Test with known rectangles on portrait and landscape pages.

## 15. Artifact naming

Use generated, immutable keys, for example: -
`documents/{document_id}/original/{content_hash}` -
`jobs/{job_id}/layout/{layout_version}.json` -
`jobs/{job_id}/pages/{page_number}/preview.png` -
`jobs/{job_id}/extractions/{result_version}.json` -
`jobs/{job_id}/exports/{export_id}.csv`

Treat these as examples; implement path construction in the storage
adapter and never accept arbitrary object keys from clients.
