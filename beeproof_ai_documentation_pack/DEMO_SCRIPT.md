# Competition Demo Script

## 1. Demo objective

Demonstrate a complete, evidence-backed workflow rather than a
collection of screens. The judges should see extraction, source
provenance, validation, bounded repair, human review, export, and
measurable evaluation.

## 2. Preparation

Prepare: - one clean digital PDF; - one scanned or low-quality
document; - one intentionally difficult table; - a ground-truth
manifest; - one deterministic repair fixture; - one unresolved field for
review; - a completed benchmark report; - a local backup environment.

Use synthetic or appropriately licensed documents. Do not depend on a
live model spontaneously making a particular error.

## 3. Suggested run of show

### 0:00--0:30 --- Problem

Explain why OCR text alone is insufficient when tables, headers, units,
and footnotes affect meaning.

### 0:30--1:15 --- Upload

Upload a representative rate sheet and select the schema. Show
asynchronous job creation.

### 1:15--2:00 --- Structure and extraction

Show the detected table, extracted records, normalized values, and
units.

### 2:00--2:45 --- Evidence

Select a field. Highlight the exact source region and show its raw text
and transformation history.

### 2:45--3:30 --- Validation and repair

Trigger the seeded failure. Show the rule that failed, repair action,
reprocessing result, and revalidation. Explain that the action is
bounded and logged.

### 3:30--4:00 --- Human review

Open a genuinely ambiguous field. Show source context, correct the
value, and display the immutable history.

### 4:00--4:30 --- Export

Export CSV or JSON and show that the output corresponds to the chosen
result version.

### 4:30--5:00 --- Evaluation

Show the baseline/enhanced comparison, sample count, denominators, error
categories, review rate, false auto-accept rate, latency, and cost where
measured.

### Closing

Position BeeProof AI as evidence-first document intelligence: each value
is traceable, each failure has a controlled resolution path, and
improvement is measured.

## 4. Demo reliability

-   Seed known data.
-   Use a deterministic repair fixture.
-   Pre-cache or pre-process assets only if the demo clearly labels this
    behavior and the judges are not misled.
-   Keep a live-processing path available.
-   Verify network and credentials before the presentation.
-   Prepare screenshots or a local recording as a backup, not as a
    substitute for a working product.
-   Do not display invented benchmark numbers.
-   If a metric is unavailable, state that it is not yet measured.

## 5. Judge questions to prepare for

### How is this different from OCR plus an LLM?

Evidence linking, deterministic validation, bounded repair, human
escalation, immutable history, and repeatable evaluation.

### How do you prevent hallucinations?

Require source evidence, distinguish missing/ambiguous values, enforce
deterministic validation, and route unresolved cases to review. Do not
claim hallucinations are impossible.

### How do you know it improved accuracy?

Run baseline and enhanced pipelines on the same labeled documents with
fixed normalization and documented denominators.

### What happens when repair fails?

The budget is exhausted and the field is routed to human review or the
job fails safely.

### How do corrections improve the system?

Approved corrections become versioned evaluation examples and may inform
rules/prompts only after regression testing and review. They do not
silently retrain the model.

### How do you control cost?

Page limits, targeted processing, artifact reuse, global call/retry
budgets, concurrency controls, and usage accounting.

## 6. Final checklist

-   [ ] Upload works.
-   [ ] Extraction works.
-   [ ] Evidence highlights correct region.
-   [ ] Validation failure is reproducible.
-   [ ] Repair is bounded and visible.
-   [ ] Review correction persists.
-   [ ] Export matches final version.
-   [ ] Benchmark report is genuine.
-   [ ] No secrets or sensitive content are exposed.
-   [ ] Backup demo is ready.
