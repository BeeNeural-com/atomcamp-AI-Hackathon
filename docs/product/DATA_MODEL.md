# Data Model and Persistence Rules

## 1. General rules

-   Use UUIDs for externally exposed identifiers.
-   Store timestamps in UTC.
-   Store monetary and rate values as decimal strings in JSON and
    fixed-precision numeric columns in PostgreSQL.
-   Keep original files in object storage.
-   Keep provider-specific responses as versioned artifacts, not as the
    only source of truth.
-   Use soft deletion only where retention policy requires it; ensure
    actual binary deletion behavior is documented.
-   Use migrations for all schema changes.

## 2. Entities

### Document

`id`, `original_filename`, `mime_type`, `size_bytes`, `sha256`,
`storage_uri`, `page_count`, `status`, `created_at`, `metadata`.

Unique deduplication may use `sha256` when product behavior allows.

### DocumentPage

`id`, `document_id`, `page_number`, `width`, `height`, `rotation`,
`preview_uri`, `native_text_available`, `layout_version`.

### LayoutRegion

`id`, `document_id`, `page_number`, `parent_region_id`, `region_type`,
`bounding_box`, `text`, `provider_confidence`, `provider_metadata`,
`layout_version`.

### TableModel

`id`, `document_id`, `page_number`, `region_id`,
`continuation_group_id`, `header_map`, `provider_metadata`,
`layout_version`.

### TableCell

`id`, `table_id`, `row_index`, `column_index`, `row_span`,
`column_span`, `region_id`, `raw_text`, `normalized_text`.

### ExtractionSchema

`id`, `name`, `description`, `created_at`.

### ExtractionSchemaVersion

`id`, `schema_id`, `version`, `schema_json`, `business_rules_json`,
`created_at`, `status`.

A published version is immutable. Edits create a new version.

### ExtractionJob

`id`, `document_id`, `schema_version_id`, `status`,
`stage`, `attempt_count`, `max_attempts`, `configuration_snapshot`,
`pipeline_version`, `trace_id`, `started_at`, `completed_at`,
`error_code`, `error_message`, `total_latency_ms`, `estimated_cost_usd`.

### ExtractionRun

Optional entity for reprocessing the same document/schema under a new
configuration. If used, distinguish the stable document from each
processing run.

### ExtractedRecord

`id`, `job_id`, `record_index`, `source_table_id`, `data_json`,
`status`, `version`, `created_at`.

### ExtractedField

`id`, `record_id`, `field_name`, `raw_value`, `normalized_value`,
`data_type`, `unit`, `confidence_score`, `risk_score`,
`verification_status`, `review_status`, `version`,
`transformation_history_json`.

### Evidence

`id`, `document_id`, `page_number`, `region_id`, `bounding_box`,
`source_text`, `source_type`, `source_artifact_uri`,
`provider_metadata`, `created_at`.

### FieldEvidence

`field_id`, `evidence_id`, `relationship_type`, `relevance_note`.

Relationship types can include `value`, `header`, `unit`, `footnote`,
`context`, and `cross_check`.

### ValidationResult

`id`, `job_id`, `record_id`, `field_id`, `rule_id`, `rule_version`,
`severity`, `status`, `expected_value`, `actual_value`, `message`,
`evidence_ids_json`, `created_at`.

### AgentAction

`id`, `job_id`, `trace_id`, `stage`, `agent_name`, `action_type`,
`reason_code`, `input_artifact_ids_json`, `output_artifact_ids_json`,
`result_status`, `duration_ms`, `attempt_number`, `model_name`,
`token_usage_json`, `estimated_cost_usd`, `created_at`.

### ReviewDecision

`id`, `job_id`, `field_id`, `reviewer_name`, `previous_value`,
`corrected_value`, `decision`, `reason_code`, `comment`,
`evidence_ids_json`, `created_at`.

`reviewer_name` is optional free text supplied by the client. It is not
verified, because the system has no user accounts.

### ExportArtifact

`id`, `job_id`, `result_version`, `format`, `status`, `storage_uri`,
`created_at`, `completed_at`, `error_code`.

### EvaluationRun

`id`, `dataset_version`, `pipeline_version`, `configuration_snapshot`,
`metrics_json`, `artifact_uri`, `status`, `started_at`, `completed_at`.

## 3. Relationship overview

-   A document has many pages and layout regions.
-   A document can have many extraction jobs/runs.
-   A job uses one immutable schema version.
-   A job produces records, fields, validation results, actions, and
    review items.
-   A field can reference multiple evidence regions.
-   A field can have multiple versions and review decisions.
-   An export points to one specific result version.
-   An evaluation run points to a dataset version and pipeline
    configuration.

## 4. Job states

Recommended states:

`queued`, `preprocessing`, `parsing`, `extracting`, `validating`,
`repairing`, `review_required`, `completed`, `failed`, `cancelled`.

Define legal transitions in code and test all transitions. A terminal
state must not transition back to processing without creating an
explicit retry/run operation.

## 5. Field verification states

Recommended values:

-   `pending`
-   `evidence_linked`
-   `validated`
-   `human_verified`
-   `rejected`
-   `unresolved`

Do not conflate model confidence with verification status.

## 6. Review states

-   `not_required`
-   `pending`
-   `in_review`
-   `accepted`
-   `corrected`
-   `rejected`
-   `deferred`

## 7. Coordinate model

Store both the parser's original coordinates and a canonical normalized
box:

``` json
{
  "x": 0.12,
  "y": 0.32,
  "width": 0.18,
  "height": 0.04,
  "coordinate_space": "normalized_page"
}
```

Normalized coordinates are relative to the canonical page preview. Keep
original page dimensions and rotation so a preview can map back to the
source. Validate `x >= 0`, `y >= 0`, positive width/height, and bounds
within the page after normalization.

## 8. Data integrity constraints

-   Unique `(job_id, record_index)` where applicable.
-   Unique field name per record/version.
-   Foreign keys for evidence references.
-   Check constraints for legal statuses where practical.
-   No accepted field without required evidence.
-   No completed job while mandatory blocking validation failures remain
    unresolved.
-   No export marked ready before its artifact exists and is verified.
-   Audit records are append-only; the API exposes no update or delete
    for them.
