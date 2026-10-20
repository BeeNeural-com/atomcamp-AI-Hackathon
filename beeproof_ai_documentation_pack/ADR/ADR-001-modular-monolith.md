# ADR-001: Use a Modular Monolith with a Separate Worker

-   **Status:** Proposed
-   **Decision date:** Initial project design
-   **Context:** The hackathon requires multiple processing stages,
    rapid iteration, and dependable integration under time constraints.
-   **Decision:** Keep API/domain modules in one codebase and run
    long-running processing in a separate worker process. Use adapters
    for cloud providers.
-   **Alternatives:** One microservice per agent; all processing
    synchronously in the API; fully serverless orchestration from day
    one.
-   **Rationale:** This minimizes deployment and coordination overhead
    while preserving independent worker scaling and testable module
    boundaries.
-   **Consequences:** Module ownership and interfaces must be enforced
    through code review and tests. Some components may be split into
    services later if measured scale or reliability needs justify it.
-   **Revisit when:** Independent deployment, scaling, security
    isolation, or team ownership requires a separate service.
