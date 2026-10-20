# Evaluation and Benchmarking

## 1. Purpose

Evaluation must establish whether the enhanced pipeline improves
extraction quality and operational behavior compared with a baseline. It
must also reveal failure modes, not merely produce a headline score.

## 2. Dataset sources

### Supplied competition dataset

The prior project brief references `ps06_sample_rate_sheets.csv` and
describes 150 extracted sample records with confidence scores and review
flags. Verify the actual file, columns, label definitions, and license
before relying on this description. The CSV may not include original
PDFs/images.

### Source-grounded dataset

For visual extraction, maintain a labeled corpus of original documents
and ground truth: - source file ID and hash; - page number; -
table/row/cell/field labels; - exact raw source text; - expected
normalized value; - unit and currency; - evidence coordinates; -
ambiguity and review labels; - annotation provenance.

### Synthetic dataset

Create generated PDFs/images with known values and deliberately
controlled defects. Keep generated ground truth linked to each source
artifact.

## 3. Dataset splits

Use separate: - development/tuning set; - validation set; - held-out
test set.

Avoid document leakage across splits. Pages from the same source
document should normally stay in one split. Freeze dataset versions and
record hashes.

## 4. Baselines

Implement at least: 1. Native text/OCR plus deterministic parsing where
feasible. 2. Single-pass LLM extraction without repair. 3. Enhanced
evidence-first pipeline with validation and bounded repair.

Keep provider/model versions and configurations explicit. Do not compare
pipelines using different documents or inconsistent normalization rules.

## 5. Metrics

### Exact field accuracy

`correct normalized fields / evaluated non-excluded fields`

Publish the normalization rules and denominator.

### Precision and recall

Use for detection tasks where labels support true positives, false
positives, and false negatives.

### Evidence coverage

`fields with valid resolvable evidence / fields requiring evidence`

### Evidence correctness

Measure whether evidence regions actually support the extracted value. A
non-empty bounding box alone does not prove evidence correctness.

### False auto-accept rate

`incorrect auto-accepted fields / all auto-accepted fields`

Also report the share of all incorrect fields that were auto-accepted.
These metrics have different denominators and answer different
questions.

### Repair success rate

`eligible failed fields correctly fixed by repair / eligible failed fields for which repair was attempted`

Define eligibility before evaluation.

### Review rate

`fields sent to review / total evaluated fields`

### Operational metrics

-   median latency;
-   p95 latency;
-   throughput;
-   provider calls per document;
-   retry rate;
-   failure rate;
-   estimated cost per document;
-   reviewer correction rate.

### Calibration

Evaluate confidence calibration only if a defined score is available and
tested against ground truth. Report calibration method and test set.

## 6. Error taxonomy

Use stable labels: - OCR character error; - decimal separator error; -
sign error; - row/column association error; - header mapping error; -
unit/currency error; - missing-value hallucination; - duplicated row; -
omitted row; - merged-cell interpretation; - multi-page continuation; -
footnote interpretation; - normalization error; - evidence localization
error; - schema violation; - validation-rule defect.

## 7. Benchmark execution

For each run, persist: - dataset version and hashes; - pipeline Git
commit; - schema version; - provider/model versions; - prompt/config
versions; - thresholds and budgets; - start/end time; - raw
predictions; - normalized predictions; - field-level diffs; - aggregate
metrics; - artifact/report URI.

The same benchmark should be repeatable with a documented command. If a
provider is nondeterministic, record that limitation and consider
repeated runs.

## 8. Acceptance targets

Do not set arbitrary accuracy claims as facts. Define initial targets
after a baseline run. For a competition demo, prioritize: - all
predictions linked to source evidence; - zero hidden blocking validation
failures in accepted results; - bounded repair behavior; - reproducible
baseline comparison; - explicit unresolved cases routed to review; -
complete audit trace.

A false auto-accept is a high-priority defect even if overall accuracy
is high.

## 9. Reporting

Every report must show: - dataset and pipeline version; - number of
documents and fields; - exclusions and missing labels; - metric values
with denominators; - breakdown by document quality and error type; -
baseline comparison; - confidence intervals or repeated-run variability
where feasible; - known limitations.

Never use a synthetic test result as a claim about real-world production
performance without qualification.
