# Engineering Token Efficiency

This memory records reusable method, not project status, account usage, credentials, or absolute project paths.

## Operating model

1. Externalize authoritative state into approved plans, versioned contracts, Git, and Harness run state.
2. Give each Agent a bounded Context Capsule with stable policy first and volatile task facts last.
3. Execute deterministic validation outside the model. Passing logs stay local; failures become sanitized, deduplicated diagnostics capped by policy.
4. Cache only content-addressed evidence whose repository, HEAD, command, environment, inputs, and policy all match.
5. Use leases to collapse concurrent duplicate work and keep final independent review separate from implementation.
6. Project verified milestones to human knowledge systems through marker-owned blocks; those systems never promote repository state.

## Measurement

- `rawTokens`: provider-reported cumulative usage.
- `cachedInputTokens`: input satisfied by provider cache.
- `activeTokens`: uncached input plus output and reasoning tokens.
- Main sessions use cumulative deltas; subagents and reviewers use final counters and remain separate categories.
- Reports contain numeric aggregates and identifiers only. They never contain message or source payloads.

## Escalation

Stop autonomous repair after the configured identical-failure budget, no-progress threshold, policy drift, or unavailable trust evidence. Preserve raw diagnostics locally, expose only a bounded sanitized summary, and state the exact human decision required.
