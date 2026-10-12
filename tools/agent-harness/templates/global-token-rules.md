## Token-efficient engineering

- Keep durable state in repository plans, Harness state, Git evidence, and bounded summaries; do not depend on chat history as the authority.
- Start delegated work from a generated Context Capsule plus the task delta. Do not reload full plans, reports, logs, or Obsidian vaults unless the capsule identifies a specific missing fact.
- Run tests, linters, type checks, and builds locally or in CI. Return only structured pass summaries or the smallest sanitized failure diagnostic needed for repair.
- Reuse Gate evidence only when repository, HEAD, command, environment, input, and policy digests all match. Never reuse strict final acceptance evidence across commits.
- Deduplicate identical concurrent Gates and repeated failure fingerprints before spending another model turn.
- Token savings never justify skipping tests, weakening assertions, omitting review, exposing secrets, or lowering production acceptance standards.
- Keep implementation, subagent, and reviewer usage separate. Telemetry may aggregate numeric metadata but must never store or emit prompts, messages, source payloads, or raw logs.
- After three identical failed repairs, an architectural conflict, or evidence drift, stop and emit bounded diagnostics for human review.
