# Task 8 Security Final Closure Review

## Scope And Identity

- Exact reviewed control-plane range: `c7b9366142ab6f8b0570d208c156fc0392c007f1..ef5e1d3b61f2944731ba1be6aa5996548a88cf31`.
- Final hosted-runtime focus diff: `717c1d82cbe77ccd55cf19a4949583ab03c9571b..ef5e1d3b61f2944731ba1be6aa5996548a88cf31`.
- Local identity verification confirmed `HEAD=ef5e1d3b61f2944731ba1be6aa5996548a88cf31` and both range endpoints.
- The focus diff changes the wrapper, its adapter regression tests, and three status documents. No tracked file was edited during this review, no push was performed, and no real Codex process was run.

## Findings

No residual security or code-quality finding was identified. The prior P1 hosted bootstrap identity/version mismatch is closed. PATH-poisoning and bootstrap identity-change controls remain effective.

## Closure Evidence

1. **P1 hosted identity mismatch: closed.** The trusted bootstrap now always resolves the running process image at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:220`. Hosted candidates and all versioned candidates require that resolved `sys.executable` equal the fingerprinted final target. At `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:223`, `require_version_binding` is true for the hosted root even when the entry is named only `python3`; the comparison at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:224` therefore cannot be bypassed by the unversioned entry name.

2. **Actual minor is bound to the final versioned target.** When the resolved target is named `python3.x`, `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:225` requires both a supported 3.11-3.14 interpreter and exact agreement between the target basename and the current process's actual major/minor. The hosted branch independently enforces the supported range at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:228`. The positive `python3 -> python3.11` case passes; the negative `python3 -> python3.12` name backed by an actual 3.11 executable is rejected with the typed bootstrap identity error. These cases are fixed at `/Users/lionel/Develop/survey/.worktrees/admin-web/tools/agent-harness/tests/test_codex_adapter.py:471`.

3. **Legacy unversioned /usr bootstrap remains discovery-only for old Python.** Non-hosted unversioned `python3` may start the isolated discovery code, preserving compatibility with an old system bootstrap, but `trusted_runtime` at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:235` only permits unversioned or explicitly supported versioned names. Its `python3` branch at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:251` binds the candidate to the current resolved executable and requires version 3.11-3.14. An old bootstrap therefore cannot itself become the controlled runtime; a separate trusted supported runtime must be discovered.

4. **PATH poisoning remains closed.** The wrapper still replaces caller `PATH` with `/usr/bin:/bin` at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:15`, uses absolute bootstrap utilities, and searches only approved absolute roots. The escaped/writable candidate fixture at `/Users/lionel/Develop/survey/.worktrees/admin-web/tools/agent-harness/tests/test_codex_adapter.py:522` passed without executing attacker-controlled candidates. Host-side caller-PATH, writable executable, controlled-path, and alternatives-chain tests also passed.

5. **Identity-change protection remains closed.** Shell-side trusted-root, owner/mode, symlink-depth, executable and device/inode fingerprint checks remain unchanged. The Python phase reconstructs and compares that fingerprint before proceeding at `/Users/lionel/Develop/survey/.worktrees/admin-web/scripts/agent-harness:218`. The mutation fixture at `/Users/lionel/Develop/survey/.worktrees/admin-web/tools/agent-harness/tests/test_codex_adapter.py:538` continues to return exit 2 with a typed bootstrap error.

## Verification

- Fixed-digest Linux integration using `python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`: 7 tests passed, zero skipped. This included the hosted positive and identity/minor mismatch negative cases, path poisoning, and identity mutation.
- Complete adjacent adapter and governance suite: 58 tests executed; 51 passed and 7 Docker tests were sandbox-skipped. The same 7 tests independently passed with zero skips in the authorized fixed-digest Docker run above.
- Current repository workflow scan returned `PASS forbidden-option-policy`.
- Status/document drift check returned `PASS plan-memory-drift`.
- `git diff --check c7b9366142ab6f8b0570d208c156fc0392c007f1..ef5e1d3b61f2944731ba1be6aa5996548a88cf31` returned clean.
- Codex Security review of the focus diff completed with zero reportable findings across hosted identity/minor binding, PATH poisoning, and identity-change surfaces.

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
