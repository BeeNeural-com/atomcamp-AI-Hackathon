# Testing Strategy

## 1. Principles

-   Test deterministic logic without live model calls.
-   Use provider fakes for repeatable tests.
-   Keep a small optional provider smoke-test suite.
-   Test both successful workflows and failure paths.
-   Make every bug fix produce a regression test where feasible.
-   Do not mark a test as passed without executing it.

## 2. Unit tests

### Ingestion

-   valid PDF;
-   valid image;
-   invalid signature;
-   unsupported MIME type;
-   oversized file;
-   corrupt document;
-   page-count limit;
-   duplicate hash behavior.

### Layout

-   coordinate normalization;
-   rotated pages;
-   portrait and landscape mapping;
-   parent/child region links;
-   merged cells;
-   multi-row headers;
-   multi-page continuation ambiguity.

### Extraction

-   valid structured output;
-   invalid JSON/schema;
-   missing required field;
-   ambiguous value;
-   missing evidence;
-   unsupported unit;
-   explicit null handling.

### Validation

-   required fields;
-   numeric/date parsing;
-   Decimal normalization;
-   range checks;
-   allowed values;
-   cross-field rules;
-   severity and blocking behavior.

### Repair

-   correct action for each error class;
-   budget exhaustion;
-   repeated-action prevention;
-   provider failure;
-   successful repair and revalidation;
-   escalation to review.

### Export

-   stable column order;
-   null representation;
-   Unicode;
-   escaping commas and quotes;
-   decimal preservation;
-   result-version selection.

## 3. Integration tests

-   upload persists original and metadata;
-   job creation publishes a queue message;
-   duplicate message does not duplicate records;
-   worker creates canonical layout;
-   extraction result references valid evidence;
-   validation results persist;
-   repair creates a new version;
-   review decision preserves history;
-   export artifact matches the selected result version;
-   terminal failures are observable.

## 4. End-to-end scenarios

1.  Clean digital PDF.
2.  Scanned rate sheet.
3.  Low-resolution or rotated image.
4.  Multi-row table header.
5.  Missing value.
6.  Ambiguous decimal/sign.
7.  Unit in footnote.
8.  Multi-page continuation.
9.  Deliberately invalid extraction requiring repair.
10. Unresolved field requiring human review.
11. Human correction followed by revalidation.
12. CSV and JSON export.
13. Benchmark run and report display.

## 5. Failure injection

Simulate: - parser timeout; - LLM quota failure; - malformed structured
response; - database interruption; - worker crash; - duplicate queue
delivery; - object storage outage; - exhausted repair budget; - invalid
review correction; - export write failure.

## 6. Acceptance suite

Before release, run: - formatting and lint; - type checking; - all unit
tests; - integration tests; - end-to-end suite; - security tests; -
benchmark; - deployment smoke tests.

Record the commit hash, environment, command, pass/fail summary, and
known skipped tests.

## 7. Test data policy

Use synthetic or appropriately licensed documents. Do not commit
sensitive customer documents. Keep expected values and source hashes in
the fixture manifest. Do not use the held-out evaluation set to tune
prompts and then report it as independent test performance.
