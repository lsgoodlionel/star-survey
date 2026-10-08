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
  platform APIs. The short-lived owner JWT and engine event secret are written
  with mode `0600`; neither value is printed or included in metadata.
- The runner exports `ADMIN_WEB_BASE_URL`, `ADMIN_WEB_JWT_FILE`,
  `ADMIN_WEB_METADATA_FILE` and `ADMIN_WEB_RESULT_FILE`, then invokes the Node 22
  desktop and mobile Playwright projects from `platform/apps/admin-web`.
- Node 22 is accepted from the caller, `NODE22_BIN`, or an installed NVM
  `v22*/bin`, so the documented one-line command works on the current host even
  though its default Node is 24.
- `verify` validates the exact redacted result schema, then independently checks
  the published survey, live version and public route through the platform API,
  the current binding in platform PostgreSQL, exactly one gateway publish call,
  and `lime_surveys.active = 'Y'` in the engine database.
- The EXIT trap removes only this Compose project, its volumes and its temporary
  JWT, metadata, event-secret and result files.

## TDD evidence

Initial RED:

- 4 errors because `admin_web_gate.py` did not exist.
- 2 errors because the Compose overlay and runner did not exist.
- A focused failure exposed the frontend container port mismatch (`80` versus
  the initial overlay's `8080`).
- A focused failure exposed the Playwright result-schema mismatch.
- A focused failure exposed the absent gateway log counter.
- A focused failure exposed the missing Node 22 auto-selection.

Final GREEN:

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

## Integration status and concerns

The full real-stack command was not run in this slice. At implementation time
the frontend Task 7 files were still uncommitted and the instruction for this
slice prohibited downloading dependencies; building the frontend image executes
`npm ci`, while the required Playwright Chromium revision was not installed.

Before the integrated run, the frontend slice must also ensure a login failure
cannot leave the JWT visible in a retained screenshot. The current E2E-only login
control is a plain `textarea`; `screenshot: only-on-failure` can capture it if the
login fails before the test clears the field. The stack slice cannot repair that
without modifying the explicitly excluded `platform/apps/admin-web/**` scope.

Intended integrated command after those frontend prerequisites are complete:

```bash
SURVEY_TEST_PREFIX=adminweb COMPOSE_PROJECT_NAME=adminweb TEST_DB=mysql \
  platform/deploy/test/run-admin-web-e2e.sh --fresh
```
