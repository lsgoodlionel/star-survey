# Production Task 4 Implementer Report

## Scope

- Task: backup, restore, doctor and clean-host acceptance.
- Branch: `feat/admin-product-alignment`.
- Commit: `3f141be4 feat: verify production backup upgrade and recovery`.
- Base contract: reviewed Task 3 `surveyctl` lifecycle and trust boundaries.
- Frontend and concurrent response/export work were not modified or staged.

## Delivered

- `backup.py`
  - Pauses all writer services, then creates PostgreSQL custom-format and MariaDB single-transaction dumps.
  - Archives seven non-database named-volume data classes.
  - Encrypts each payload with AES-256-CBC/PBKDF2 and authenticates the streaming ciphertext with HMAC-SHA256.
  - Publishes `manifest.json`, `SHA256SUMS`, exact inventory and encrypted payloads atomically.
  - Rejects unsafe target/output paths, unsafe key permissions, insufficient free space and empty/truncated command output.
  - Removes partial artifacts and resumes the stack after failures.
- `restore.py`
  - Rejects path escape, symlinks, special files, incomplete inventory, checksum mismatch, truncated payload, authentication failure and undeclared database schemas before Docker actions.
  - Restores first into a random isolated Compose project, checks the complete long-running service set, and only then enters production restore mode.
  - Creates an automatic pre-switch safety backup; production apply failures restore that backup and restart the prior stack, or fail closed with an explicit recovery-required error if compensation also fails.
  - Clears target volume contents before archive extraction so stale files cannot survive a restore.
  - Supports both JSON-array and line-delimited Docker Compose health output.
- `doctor.py`
  - Emits always-redacted JSON with `ok`, `warning` and `blocked` states.
  - Checks public ports, TLS deployment marker, exact container health including exited `engine-init`, internal database exposure, Flyway/LimeSurvey migration state, all nine volumes, backup checksums and read-only minimal application probes.
  - Never includes secret values or the secret directory path in the report.
- `surveyctl` integration
  - Generates a dedicated `backup_encryption_key` with existing `0600` secret handling.
  - Requires all three recovery helpers in the verified Release bundle.
  - Allows restore only from schemas explicitly declared compatible by the installed Release.
- Acceptance and docs
  - Added unit/fault-injection tests and an opt-in native clean-host test.
  - Native sequence is install -> doctor -> public minimal probe -> backup -> upgrade -> doctor -> restore through second project -> uninstall.
  - The native test explicitly skips unless real Release assets, Docker, DNS and TLS parameters are supplied; no fake-success path exists.
  - Added the production operator README and corrected the platform README's prior “no production path” statement without claiming release readiness.

## TDD Evidence

- Initial RED: helper imports failed because `backup.py`, `restore.py` and `doctor.py` did not exist.
- Subsequent RED cases covered insufficient disk, partial backup cleanup, path/symlink escape, truncated/checksum-corrupt payloads, incompatible schema, unhealthy isolated restore, stale-volume cleanup, secret redaction, migration mismatch, clean-host upgrade failure, missing Release helpers, compatible old-schema restore, `engine-init` visibility and line-delimited Compose JSON.
- Each RED was rerun GREEN before the next behavior was added.

## Verification

- Production full suite: **88 tests run, 87 passed, 1 skipped, 0 failures/errors**.
- Skip: native clean-host acceptance only; requires `SURVEY_PRODUCTION_E2E=1` plus two real Releases and public DNS/TLS. It was not reported as passed locally.
- Docker-backed Compose/Caddy validation: passed with Docker socket access.
- Focused Task 4 + surveyctl fault tests: passed.
- Real OpenSSL encryption/authentication round trip and tamper rejection: passed.
- `docker compose config --quiet`: passed.
- Python compile for `surveyctl.py`, `backup.py`, `restore.py`, `doctor.py`: passed.
- `surveyctl`, helper CLI help and `git diff --check`: passed.
- ShellCheck for changed entrypoint (`surveyctl`): passed. Full production shell scan still reports the two pre-existing `SC2155` warnings in unchanged `init/init-platform-db.sh:4-5`.

## Remaining Release Boundary

- Real Ubuntu 22.04/24.04 x AMD64/ARM64 clean-host execution, public certificate validation, final Engine multi-architecture image and GitHub Release publication remain release-stage gates.
- Backup key off-host custody, backup replication and retention scheduling are operator responsibilities and are documented; no secret value is committed or logged.

## Review Fix Round 1/5

- Replaced unauthenticated backup control metadata with schema v2 canonical `manifest.json` + `SHA256SUMS` covered by a domain-separated `CONTROL-HMAC` trust root. The manifest now binds application version, database schema, Release manifest SHA-256 and all seven image manifest digests.
- Restore now enforces application-version/schema/source-Release compatibility and decrypts plus validates every payload before its first Docker command. All seven volume classes reject absolute/traversing paths, links, FIFO/device/socket entries, duplicate/case-colliding names, invalid parent topology, member/file/total limits and excessive expansion ratio.
- Added restore-grade `verify` entrypoint; `surveyctl` and `doctor` delegate to the same authenticated decrypt/tar/compatibility verifier rather than claiming checksum-only backups are restorable.
- Doctor now compares desired Compose policy with live container IDs, `docker inspect` port bindings/network attachments and host `ss` listeners. Runtime DB publication, listener loss and inspection-set drift are blocking.
- Replaced read-only health probes with a disposable product journey: short-lived JWT authentication, temporary project/survey creation, publish, real LimeSurvey HTTP answer, event projection, response query, export creation/download, engine close and project archive cleanup. No permanent probe user or credential output is created; any journey or cleanup failure blocks.
- Added `native-acceptance.requirement.json` for the Task 3 multiarch Release workflow. Native execution remains explicitly skipped unless enabled and is documented as release-blocking/unaccepted, not passed.
- Round-1 verification: production suite **97 run, 96 passed, 1 explicitly skipped, 0 failures**; focused OpenSSL/fault/adversarial tests, Python compile, Docker-backed Caddy/Compose validation, Compose config, Bash syntax and `git diff --check` passed. Full production ShellCheck reports only the two pre-existing `SC2155` warnings in unchanged `init/init-platform-db.sh:4-5`.
