# Task 7 real-stack slice report

Date: 2026-10-08

## Scope

Implemented only the stack-owned slice:

- `platform/deploy/test/admin-web-e2e.compose.yml`
- `platform/deploy/test/run-admin-web-e2e.sh`
- `platform/tests/e2e/admin_web_gate.py`
- `platform/tests/e2e/test_admin_web_gate.py`

No file under `platform/apps/admin-web/**` was modified by this slice.

## Delivered behavior

- A project-scoped Compose overlay starts an isolated PostgreSQL 16 database,
  platform, publish gateway, LimeSurvey test engine and admin-web container.
- Platform, gateway and admin-web ports are assigned randomly and bound only to
  `127.0.0.1`; the engine and both databases have no host port.
- The runner does not call or source `run-p1-e2e.sh` and does not use the shared
  `platform-db`. It reuses `lib.sh` only for the isolated LimeSurvey setup.
- `prepare` seeds the tenant, plan, owner and engine instance through public
  platform APIs, writes non-sensitive metadata, and writes the engine event
  secret with mode `0600`. It does not mint a browser JWT.
- `issue-browser-token` runs only after the engine installation and the cold
  gateway/admin-web builds are healthy, immediately before Playwright. The JWT
  has exactly `exp - iat = 600`, is written with mode `0600`, and is never
  printed or included in metadata.
- The runner exports `ADMIN_WEB_BASE_URL`, `ADMIN_WEB_JWT_FILE`,
  `ADMIN_WEB_METADATA_FILE`, `ADMIN_WEB_RESULT_FILE` and
  `ADMIN_WEB_TEST_RESULTS_DIR`, then invokes the Node 22 desktop and mobile
  Playwright projects from `platform/apps/admin-web`. The results directory is
  `$WORK_DIR/test-results`, private to this run.
- Node 22 is accepted from the caller, `NODE22_BIN`, or an installed NVM
  `v22*/bin`, so the documented one-line command works on the current host even
  though its default Node is 24.
- `verify` validates the exact redacted result schema, then independently checks
  the published survey, live version and public route through the platform API,
  the current binding in platform PostgreSQL, exactly one gateway publish call,
  and `lime_surveys.active = 'Y'` in the engine database.
- The EXIT trap always removes the complete private work directory, including
  JWT, metadata, event-secret, engine config and result, even when
  `ADMIN_WEB_E2E_KEEP=1`; KEEP preserves only Compose resources.
- Result and this run's private `test-results` files are scanned byte-for-byte
  for the JWT. Matches are deleted and fail the gate without printing the
  credential. The runner never scans or deletes the shared
  `platform/apps/admin-web/test-results` directory. On Playwright failure,
  retained image/video/trace media are deleted before any read attempt because
  pixel content cannot be safely validated without OCR. Any artifact read or
  delete failure fails closed with `StepFailed`. Directory traversal is
  explicit and fail-closed: every existing root and directory entry is statted,
  every directory is scanned, and traversal, stat, read or delete errors become
  `StepFailed`.

## TDD evidence

Initial RED:

- 4 errors because `admin_web_gate.py` did not exist.
- 2 errors because the Compose overlay and runner did not exist.
- A focused failure exposed the frontend container port mismatch (`80` versus
  the initial overlay's `8080`).
- A focused failure exposed the Playwright result-schema mismatch.
- A focused failure exposed the absent gateway log counter.
- A focused failure exposed the missing Node 22 auto-selection.

Initial implementation GREEN:

```text
python3 -m unittest discover -s platform/tests/e2e -p 'test_admin_web_gate.py' -q
Ran 7 tests ... OK

shellcheck -x platform/deploy/test/run-admin-web-e2e.sh
PASS

bash -n platform/deploy/test/run-admin-web-e2e.sh
PASS

docker compose ... config --quiet
PASS

python3 -m py_compile platform/tests/e2e/admin_web_gate.py platform/tests/e2e/test_admin_web_gate.py
PASS (with PYTHONPYCACHEPREFIX under /private/tmp)

git diff --check -- <owned files>
PASS
```

Focused tests cover private-file overwrite permissions, output/token separation,
strict browser-result schema, platform/engine mismatch failures, gateway publish
counting, isolated Compose wiring, runner exports, cleanup and shell syntax.

## Review fix round 1

Review findings were reproduced before changes:

- `cleanup` returned before deleting `WORK_DIR` when KEEP was enabled.
- `prepare` required `--jwt-file`, so the 10-minute browser token was minted
  before engine installation and image builds.
- The runner scanned only the JSON result after a successful Playwright run and
  did not sanitize `test-results` on failure.
- Sourcing the runner started the real Docker workflow, preventing executable
  shell behavior tests.

Fixes:

- Made the runner source-safe with an explicit `main` entry point and testable
  `cleanup_run` / browser lifecycle functions.
- Split seed and browser-token issuance into separate CLI commands.
- Moved browser-token issuance to immediately before Playwright.
- Added failure-safe artifact scanning and media removal.
- Added executable shell tests with fake stack commands for cold-start ordering,
  KEEP cleanup and Playwright failure cleanup.

Round 1 GREEN:

```text
python3 -m unittest discover -s platform/tests/e2e -p 'test_admin_web_gate.py' -q
Ran 15 tests ... OK

shellcheck -x platform/deploy/test/run-admin-web-e2e.sh
PASS

bash -n platform/deploy/test/run-admin-web-e2e.sh
PASS

docker compose ... config --quiet
PASS

python3 -m py_compile platform/tests/e2e/admin_web_gate.py platform/tests/e2e/test_admin_web_gate.py
PASS (with PYTHONPYCACHEPREFIX under /private/tmp)
```

## Review fix round 2

Review findings were reproduced before changes:

- The runner scanned `platform/apps/admin-web/test-results`, so concurrent E2E
  lanes could inspect or delete each other's artifacts.
- A file read `OSError` was ignored with `continue`, allowing an unreadable
  artifact to be reported as safe.
- Failure media was read before deletion, although visual secrets cannot be
  ruled out by a byte scan.

Fixes:

- Added the `ADMIN_WEB_TEST_RESULTS_DIR=$WORK_DIR/test-results` interface and
  restricted every scan and cleanup to that directory plus this run's result.
  Frontend consumption of the interface is owned by the parallel frontend slice.
- Made non-media read failures and every deletion failure raise `StepFailed`.
- In failure mode, media is deleted first and is never read.
- Added fault-injection tests for read/delete errors and an executable two-lane
  isolation test proving another results directory is untouched.

Round 2 GREEN:

```text
python3 -m unittest discover -s platform/tests/e2e -p 'test_admin_web_gate.py' -q
Ran 19 tests ... OK

shellcheck -x platform/deploy/test/run-admin-web-e2e.sh
PASS

bash -n platform/deploy/test/run-admin-web-e2e.sh
PASS

docker compose ... config --quiet
PASS

python3 -m py_compile platform/tests/e2e/admin_web_gate.py platform/tests/e2e/test_admin_web_gate.py
PASS (with PYTHONPYCACHEPREFIX under /private/tmp)
```

## Review fix round 3

The review finding was reproduced before changes: `Path.rglob()` silently
ignored mocked failures while opening a nested directory, iterating its entries
and statting an entry, so all three scans incorrectly returned success.

Fixes:

- Replaced `Path.rglob()` with an explicit recursive `os.scandir()` traversal.
- Fully consumes each directory iterator under `OSError` handling, then calls
  `DirEntry.stat(follow_symlinks=False)` for every entry before deciding whether
  to recurse or scan the file.
- Converts root stat, directory open/iteration and entry stat errors to
  `StepFailed`; unsupported entry types also fail closed.
- Preserved failure-mode media deletion before any content read.
- Added deterministic mock tests for unreadable nested directories, interrupted
  directory iteration and entry stat failures, plus a normal nested-directory
  secret scan.

Round 3 GREEN:

```text
python3 -m unittest discover -s platform/tests/e2e -p 'test_admin_web_gate.py' -v
Ran 23 tests ... OK
```

## Integration status and concerns

The full real-stack command was not run in this slice because the instruction
prohibited downloading dependencies; building the frontend image executes
`npm ci`, while the required Playwright Chromium revision was not installed.

The frontend slice separately owns manual masking of the E2E login control and
was hardened in commit `65f284be`. The stack independently removes all retained
media on a Playwright failure, so a failed run cannot leave a possibly sensitive
screenshot even if frontend masking regresses.

Intended integrated command after those frontend prerequisites are complete:

```bash
SURVEY_TEST_PREFIX=adminweb COMPOSE_PROJECT_NAME=adminweb TEST_DB=mysql \
  platform/deploy/test/run-admin-web-e2e.sh --fresh
```
