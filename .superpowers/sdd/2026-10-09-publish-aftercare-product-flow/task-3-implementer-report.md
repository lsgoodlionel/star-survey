# Task 3 Implementer Report

## Result

- Commit: `da14f8f0 feat: add isolated runtime preview sessions`.
- Implemented isolated runtime preview sessions and committed only preview-related Business, Gateway and contract files.
- Selected migration `V930__survey_preview_session.sql` after verifying the existing maximum was `V920`; the planned `V550` was not used.
- Preview sessions persist tenant, survey, fixed draft version/definition, actor, engine binding, generation, TTL, lifecycle status, failure and cleanup retry count.
- API contract: `POST /v1/surveys/{id}/preview-sessions`, `GET /v1/preview-sessions/{id}`, `DELETE /v1/preview-sessions/{id}`.
- Gateway contract: authenticated `POST /v1/preview`, persistent request-id idempotency, compiler/publisher stage reuse, dedicated preview generation and token-bearing short-lived URL.
- Preview SID is never written to `survey_published_version` or `survey_route`. A database guard removes preview SID rows after attempted projection insert while preserving the existing projector's successful transition semantics.
- Cross-tenant reads and cleanup return 404 through RLS. Cleanup failures remain `cleanup_failed` with retry count and can be retried manually or by expiry cleanup.

## TDD Evidence

- RED, Gateway: 5 errors because `PublishService.preview` did not exist.
- RED, TTL boundary: expired and over-one-hour preview expiry was accepted before validation.
- RED, Business: test compilation failed because preview gateway/session types did not exist.
- GREEN, Business: `PreviewSessionApiTest` 6/6 passed.
- GREEN, Gateway: preview and publish tests 29/29 passed.

## Verification

- `PLATFORM_DB_NAME=preview_task3_v2 platform/deploy/platform-dev/mvn.sh -Dtest='cn.mjy.platform.survey.preview.*Test' test` passed, 6 tests.
- A fresh PostgreSQL database validated and applied all 47 migrations from empty schema through `V930`.
- `python3 -m unittest tests/test_preview.py tests/test_publish.py` passed, 29 tests.
- `python3 -m compileall -q ...` passed.
- `git diff --check` passed.
- Scope audit found no production or release files in the change set.

## Notes

- The repository-root Python command in the plan collides with Python's standard-library `platform` module. Tests were run from `platform/tools/publish-gateway`, preserving the named test modules and coverage.
- The shared default development database had an unrelated historical `V111` checksum drift. Verification used the project-supported isolated `PLATFORM_DB_NAME` mechanism and did not repair or mutate that shared database.

## Review Fix Round 1/5

### Result

- Addressed all 2 Critical and 3 Important findings from `task-3-review.md`.
- Preview creation is now a durable two-phase operation: Gateway prepare imports/applies an inactive survey and persists/reconciles its SID; Business registers that SID before Gateway activation.
- Gateway operation identity is `(tenantId, sessionId, requestId)`. An import marker reconciles a process exit after engine import but before local SID persistence, and activation reconciliation reads active state plus the deterministic preview participant instead of activating or issuing a token twice.
- `creating` remains recoverable on timeout/network/unknown outcomes. Duplicate create and the stale-creating sweeper replay the same operation; an expired known SID is closed instead of being dropped as failed. Surviving orphan SIDs are surfaced to Business and routed into cleanup.
- Fresh migration `V931__harden_survey_preview_lifecycle.sql` replaces the post-projection delete with a `BEFORE INSERT` guard on `engine_event_inbox`. Preview events now stop before inbox acceptance, projection transition, and engine outbox creation.
- Public preview URLs now terminate at `GET /v1/preview/access`. That runtime entry validates the complete HMAC with `compare_digest`, enforces expiry and current ready state, and only then redirects to the engine with the participant token. The signed public URL does not contain the participant token.
- DELETE uses a database `close_owner` CAS. Only the successful owner calls Gateway, owner-qualified settlement prevents stale failure overwrite, and stale `closing` leases can be reclaimed for idempotent retry.

### TDD And Verification

- RED confirmed the old Gateway rejected tenant/session fields, activated during create, had no durable preview operation, and did not expose a validating access route.
- Added crash/fault tests at both external side-effect boundaries: import-result loss reconciles by marker without a second import; activation-result loss reconciles active state and participant without a second activation/token issue.
- Added cross-tenant same-request-id, strict schema, tamper, expiry, closed-link, restart, and idempotent close tests.
- Added Business timeout recovery and concurrent DELETE ownership tests.
- Added `PreviewEngineEventIsolationTest`: preview completed events leave inbox/projection/outbox counts unchanged, while a formal completed event still increments all three.
- Fresh PostgreSQL migration validation applied 48 migrations through `V931`.
- Java targeted suite passed: `PreviewSessionApiTest`, `PreviewEngineEventIsolationTest`, `ResponseProjectionTest`, `EngineOutboxTest`, and `EngineEventTenancyTest`.
- Gateway targeted suite passed: preview, publish, drift, RPC allowlist, and HTTP server tests, 82 tests total.
- `python3 -m compileall -q pubgw tests` and `git diff --check` passed.
- Release/production files were not modified or staged by this task; concurrent Release work remains outside this change set.

## Review Fix Round 2/5

### Result

- Addressed the concurrent-create Critical and close-lease Important findings from `task-3-fix-review.md`.
- Business keeps the database unique key on `(tenant_id, request_id)`, rereads the winning row after insert conflict, and resumes the same fixed draft session instead of returning a conflict or terminalizing it.
- Gateway prepare, activate and close now use a durable SQLite operation owner/lease CAS. Only the owner executes publisher or engine side effects; followers wait for and replay the same persisted response, SID, participant token and preview URL.
- Prepare and activate takeover is allowed only after the 210-second operation lease expires. A takeover reconciles import marker, active state and deterministic participant before performing any side effect, preventing orphan SID, duplicate activation and duplicate token issuance after a crash.
- Business close ownership now has an explicit `close_lease_until` persisted by fresh migration `V932__serialize_preview_operations.sql`. Its 150-second lease covers the 120-second Gateway HTTP timeout plus a 30-second margin; stale cleanup only claims an expired lease.
- Gateway's 210-second close lease covers its 180-second engine transport timeout plus a 30-second margin. Concurrent or early retry callers replay the durable `closed` result and cannot issue a second close operation.

### TDD And Verification

- RED reproduced concurrent prepare as one `200` plus five `409` responses, concurrent close as four engine close writes, and the missing Business `close_lease_until` schema.
- Added barrier-based multithread tests proving six concurrent prepare and activate calls all return `200` with one SID/result/token while import, activation and participant creation each execute once.
- Added concurrent close coverage proving all callers replay one `closed` response and only one engine close write occurs.
- Added crash takeover and movable-clock boundary coverage: ownership cannot be taken at 120 or 209 seconds and becomes claimable exactly at the 210-second Gateway lease boundary.
- Added Business concurrent-create coverage and a database assertion that the close lease remains beyond the 120-second client timeout.
- Gateway targeted suite passed: preview, publish, drift, RPC allowlist and server tests, 85 tests total.
- Java targeted suite passed: `PreviewSessionApiTest`, `PreviewEngineEventIsolationTest`, `ResponseProjectionTest`, `EngineOutboxTest`, and `EngineEventTenancyTest`, 31 tests total.
- Existing database validation passed at V932 after upgrading from V931. A fresh PostgreSQL database applied all 49 migrations from an empty schema through V932; `PreviewSessionApiTest` then passed 10 tests.
- Release/production files remain outside this change set.
