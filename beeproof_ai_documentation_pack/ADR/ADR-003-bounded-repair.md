# ADR-003: Use Bounded Repair with Explicit Escalation

-   **Status:** Proposed
-   **Context:** Automatic retries can increase cost, repeat the same
    failure, and produce uncontrolled loops.
-   **Decision:** Every repair is triggered by a recorded failure,
    selected from an allowlisted action registry, constrained by a
    global budget, and followed by validation. Unresolved ambiguity goes
    to human review.
-   **Alternatives:** Unlimited agent loops; always rerun the whole
    document; never repair automatically.
-   **Rationale:** Bounded repair offers measurable recovery while
    protecting cost, latency, and auditability.
-   **Consequences:** The system needs a useful error taxonomy,
    action-selection policy, attempt tracking, and tests for budget
    exhaustion.
-   **Revisit when:** Evaluation demonstrates that different document
    classes need separate budgets or action policies.
