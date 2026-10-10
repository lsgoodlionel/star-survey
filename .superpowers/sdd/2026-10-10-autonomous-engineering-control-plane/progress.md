# SDD ledger — plan: docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md

Spec: docs/superpowers/specs/2026-10-10-autonomous-engineering-control-plane-design.md
Branch: feat/admin-product-alignment
Start commit: c7b9366142ab6f8b0570d208c156fc0392c007f1
Execution: subagent-driven development with TDD and per-task review

## Pre-flight scan

| Scope | Produces / consumes | Finding and ruling |
|---|---|---|
| Task 1 | Rules, policy configs and config interfaces | Internally consistent. Tests precede parser implementation. |
| Task 2 | State types, atomic storage and events | Internally consistent. Depends only on Task 1 schema. |
| Task 3 | Git guard consumes policy and state | No write overlap with Tasks 4-5. May run in parallel after Task 2. |
| Task 4 | Gate runner consumes config, state and changed paths | No write overlap with Tasks 3 and 5. May run in parallel after Task 2. |
| Task 5 | Diagnostics consumes state and gate evidence | No write overlap with Tasks 3-4. May run in parallel after Task 2. |
| Task 6 | CLI composes Tasks 1-5 | Internally consistent after plan self-review added explicit next/pause/decision interfaces. |
| Task 7 | Codex adapter extends run_service.py and cli.py from Task 6 | Shared files are sequential, not concurrent. Task 7 must preserve Task 6 public interfaces. |
| Task 8 | CI, drills and documentation | Internally consistent. It modifies PROJECT_MEMORY.md and plan only after Task 1 and all implementation tasks complete. |
| Task 1 -> Task 2 | STATE_SCHEMA.json -> state model | Schema is binding; Task 2 may not silently widen it. |
| Task 1 -> Task 3 | PolicyConfig -> path classification | Action enum and precedence are fixed by spec and plan. |
| Task 2 -> Tasks 3-5 | RunState/GateEvidence -> guards, gates, diagnostics | Public dataclasses are integration boundary; incompatible changes require a ledger ruling. |
| Tasks 3-5 -> Task 6 | Three independent services -> CLI orchestration | Compose only after all three task reviews pass. |
| Task 6 -> Task 7 | RunService/CLI -> autonomous loop | Sequential shared-file change is expected and reviewable. |
| Task 7 -> Task 8 | Complete Harness -> CI and drills | Task 8 cannot weaken limits to make drills pass. |

Ruling: JSON-formatted `.yaml` files are intentional because JSON is a YAML 1.2 subset and avoids a new runtime dependency — cost if wrong: reduced human-friendly YAML syntax, but deterministic standard-library parsing.

Ruling: The current managed worktree `/Users/lionel/Develop/survey/.worktrees/admin-web` is already isolated on `feat/admin-product-alignment`; no second worktree is created — cost if wrong: less isolation from earlier work on the same feature branch, mitigated by a clean start commit and per-task commits.

Ruling: Tasks 3-5 keep separate implementer ownership but execute sequentially through per-task review because the active SDD framework forbids concurrent implementation agents in one shared plan workspace — cost if wrong: slower wall-clock completion, but no shared-contract race or unreviewed overlapping commit chain.

## Task status

Task 1: complete — commits 32b884ef5647b230083bf863c50871bb8676f50d and f08d9bdc93b61fd4792e1abb89a04c48cc4746e5; 21 tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 2: complete — commits 4db01d6a80ca6339319d0f4ced5e364f08913704 and e21a659166b00fffd5f01f41afd9669d1f4c8d17; 29 focused / 50 Harness tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 3: complete — commits 7e5458986da4caa6a0b15d5ca92d5d41a9426d04, 971b64b49bbe087acccd3b1c5a640c847efb9069, ef4be08cc69055326d1611f7670e5c2322b4556e; 49 focused / 99 Harness tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 4: complete — commits c0e70dacd61dc8a51a2b6b1af08a365c8b340f2c and d22b9e44d97d8b56b3d7d494e22772c2dcb43b18; 39 focused / 138 Harness tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 5: complete — commits 842a6f5bec1e0aaba69ceb2b58ed07ae51140911, 33cba68a2a97815f8082f0d051093c835961ddea, e2d7941b3c9a1bf390bea0f536591e0ff2b967a0; 49 focused / 187 Harness tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 6: complete — commits 101188de4cf8510bd5e5f053f6bc0f8276e90df7, 1357b67072e2f02359f5023cf7cafe2649254d94, dcf31a4af0220540dc916f03f349dc4644895b2b; 98 focused / 264 Harness tests pass; scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 7: complete — commits 0a41a247df8ef084f5a0399c94b217b5179c9a57, 917ced92c395d47534ef710b0973e10e75fa6a46, d4f7b36314a60d4ff533f4d3a50a1cf0581e9d20, 8fcd2922ad50a9be417604a910902d421bf7fb5d, 4dfc278fcf1c2eaf06480aa70f23c2a44e9dcd4f, b84c0e1e0538cc628e94e170c164db37280cbd87, 80233d79b69c81bd411a4ddc3a803047244a17ae and ce4c86cd39909b8dc4b97050059d931e19855cb7; 80 focused / 344 Harness tests pass; final scoped re-review SPEC_COMPLIANCE=APPROVED and CODE_QUALITY=APPROVED
Task 8: complete — final implementation commit 79e85b7208d9647781e978e2dff90158d28f2f0a passed GitHub run 38064236869 with Linux integration 8/8 and zero skips; local non-Linux suite is 402/402, three final reviews are double APPROVED, and repository/GitHub/Obsidian synchronization is complete

## Review findings

Task 1 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: engine event authentication classes are only `review_required`; add `approval_required` coverage and regression tests.
- P2: access-policy and scoring paths omit existing MySQL/PostgreSQL specialist gates; add fixed command-array gates/profile mappings and regression tests.

Task 2 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: `completed` Gate/HEAD invariants are enforced by transition only; direct construction, load, replace and save can accept invalid completed state. Centralize the invariant and add regression tests for missing, failed, stale and mismatched Gate evidence.

Task 3 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: text-mode Git output rewrites CR filenames and can misattribute scope.
- P1: directory-symlink routes omit remaining suffixes and can bypass exact protected paths.
- P1: assume-unchanged/skip-worktree can hide dirty content and permit exact resume.
- P1: inherited diff.ignoreSubmodules can hide committed gitlink changes.
- P2: duplicate Agent-Run-Id with one empty value is incorrectly accepted as unique ownership.

Task 3 re-review 1: original R1-R5 closed; SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P2: the R2 fix combines one outer suffix with every historical symlink prefix, creating phantom routes when a symlink target itself contains subdirectories; unrelated rules or broken links can incorrectly affect a valid target.

Task 4 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: normal/nonzero/intrerrupted exits do not always clean the owned process group before logs are sealed.
- P2: metadata is written directly to its final path and can leave truncated JSON after write failure or interruption.

Task 5 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: output-directory identity is not pinned across validation and atomic publish.
- P1: valid JWT headers outside two prefixes can leak.
- P1: comma-containing unquoted password/token/custom-secret values can leak suffixes.
- P2: direct Python traceback locations are omitted from fingerprints.
- P2: nested pytest temporary run identifiers remain unstable.
- P2: overlong first lines collapse unrelated errors to one fingerprint.
- P2: repeated long paths can truncate required diagnostic sections and resume command.
- P2: URI scheme regex has quadratic behavior on repeated prefixes.

Task 5 re-review 1: R1/R2/R3/R5/R6/R7/R8 closed; SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P2: direct traceback with bare `Exception:` still omits the stable File/line because exception-line matching requires a prefix before `Exception`.

Task 6 review 1: SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: finalize ignores `review_required` and can complete without HEAD/scope-bound independent review evidence.
- P2: init proves only an example raw-state path is ignored, not the actual UUID run directory and artifacts.
- P2: CLI JSON redacts mapping values but can leak secrets in untrusted keys such as Gate IDs.
- P2: interruption after terminal history publication can leave the event chain without a terminal/reconciled event forever.
- P2: a committed, digest-matching run history path is excluded from checkpoint ownership and blocks resume.
- P2: pause before gate omits current real changed paths from state and history.
- P2: next can advise develop after branch/plan/policy drift because non-verifying states skip read-only validation.
- P2: `scripts/agent-harness` has no Gate profile and therefore fails closed outside Harness tests.
- P2: wrapper selects Python 3.11+, but Gate matrix invokes default `python3`, so doctor can pass while real Gate fails.

Ruling: Task 6 fix round 1 may modify `docs/agent/GATE_MATRIX.yaml` and its configuration regression tests to close the new wrapper integration; this is a required integration correction, not an unrelated Task 1 refactor.

Task 6 re-review 1: F2/F3/F5/F6/F8 closed and most F1/F4/F7/F9 paths closed; SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: incomplete completed-state recovery publishes history/event before revalidating current review evidence; a failed recovery can become visibly completed.
- P1: doctor executes arbitrary matrix command[0] when command[1] is `--python`, violating the read-only boundary.
- P2: next rejects an unchanged, previously registered dirty repairing snapshot instead of returning repair.

Task 7 final re-review after fix round 5: prior PATH-injection, environment-secret, cleanup, dependency-source, Linux trusted-root and doctor-boundary findings were closed; SPEC_COMPLIANCE=CHANGES_REQUIRED; CODE_QUALITY=CHANGES_REQUIRED.

- P1: `scripts/agent-harness` Linux bootstrap requires a non-symlink `/usr/bin/python3`, rejecting trusted `/usr/local/bin/python3.11` and approved-root symlink layouts.
- P1: the real wrapper validates all controlled tools before parsing the exact `run-codex --help` path, so help exits 2 on a clean host without Codex/Java/Docker instead of remaining a non-executing documentation path.
- P2: npm spec `1.2.3+build..1` is accepted as a registry range and reaches Gates instead of pausing before Gates.

Escalation ruling: five autonomous fix rounds have been consumed for Task 7. The branch remains clean at `b84c0e1e0538cc628e94e170c164db37280cbd87`; Task 8 must not start until a human explicitly authorizes another fix round or narrows/changes the acceptance contract. Full diagnostics: `task-7-rereview-5.md`.

Ruling: the user explicitly resumed completion on 2026-10-10 and required the finished suite to be extracted to `lsgoodlionel/Autonomous-Engineering-Control`; authorize one fresh-agent fix round beyond the automatic breaker for the three reproducible findings in `task-7-rereview-5.md` — cost if wrong: one additional review cycle before Task 8, with no push or external publish until all reviews pass.

Task 8 consolidated review fixes: all supplied security, DX and whole-branch findings were deduplicated into one TDD pass. The implementation
now uses operation-scoped tool requirements, a Python 3.11 controlled entry, typed/pinned Linux fixtures with CI zero-skip enforcement,
full Git path inventory secret preflight, symlink-safe atomic smoke inputs, strict terminal and cleanup postconditions, expanded Codex-context
forbidden scanning, host-owned portability configuration, a structured delivery manifest and a genuine RunService success drill. The one prior
real Codex smoke remains the only real cycle and remains recorded as sanitized `paused`; it was not rerun or represented as completed.

Task 8 round-3 implementation commit `09e84c0a` closes rereview-2 findings with quoted YAML run-key discovery plus shlex-normalized Codex
command policy, unconditional typed smoke identity checks after cleanup errors, real hosted-toolcache Python identity, explicit pinned
Docker test parameters, a committed portable-host fixture, and controller-only structured review evidence. Local Docker integration passed
7/7 with zero skips; the independent review and bound GitHub Ubuntu run remain pending.

Task 8 round-4 implementation commit `3bb8706d` closes round-3 wrapper/YAML and review-verdict findings. Explicit RED covered `command`/`env`
and absolute wrappers, flow mappings, escaped keys, tags, anchors/aliases, unsupported block indicators, plus historical/quoted/code-block,
duplicate, missing, reversed and trailing verdicts. GREEN evidence: focused 96 tests (89 pass, 7 typed skip), full Harness 399 tests
(392 pass, 7 typed skip), 11/11 fake drills, productization 37/37 and capability current. No real Codex or Docker integration was rerun.
Ruling: without an approved stdlib YAML parser, the workflow scanner supports a documented block-style `run` subset and fails closed on
unsupported run representations or unparsed Codex-like `exec` — cost if wrong: future workflows using richer YAML run syntax must be rewritten
to the supported subset before governance passes. Independent review/Step 7 remains pending with an empty report manifest until the controller
records three round-4 reports whose unique canonical EOF verdict blocks are both APPROVED.

Task 8 round-5 implementation commit `53c1f3db` closes all round-4 findings. RED covered decoded single/double quoted run scalars,
`sh`/`bash`/`zsh -c`, `eval`, wrapper and executable expansion sinks, nested/folded block scripts, path/line diagnostics without command
echo, top-level EOF report contexts, duplicate path/digest and symlink aliases. GREEN evidence: focused 103 tests (96 pass, 7 typed skip),
full Harness 406 tests (399 pass, 7 typed skip), fixed-digest Docker integration 7/7 with zero skip outside the sandbox, 11/11 fake drills,
productization 37/37, capability current, forbidden scan and diff check. The initial in-sandbox Docker attempt failed only with typed socket
permission denial; no real Codex ran. Independent review/Step 7, GitHub and Obsidian remain pending with an empty report manifest.

Ruling: after the five automatic rounds, controller adjudication narrowed the stdlib-only workflow grammar instead of adding YAML/Shell/
Markdown dependencies. Commits `43bda24a`, `07e414c0`, and final implementation `9031fcd5` reject Codex-like folded/explicit-key forms,
recursive wrappers and executable expansions, and non-top-level raw-HTML review sentinels while retaining current real workflows. Cost if
wrong: richer workflow syntax must be rewritten to the documented subset rather than silently accepted.

Task 8 initial controller closure before hosted CI: security, DX/CI and whole-branch reports all bind
`c7b9366142ab6f8b0570d208c156fc0392c007f1..9031fcd5e0322b16d5df2ae42580af946a9d796d` and end in double
`APPROVED`. Those report slots were later superseded by the hosted-runtime closure reviews below. Step 7 and
`externalSync.independentReview` remain complete; Task 8 stays in_progress until GitHub hosted CI and Obsidian synchronization finish.

Task 8 hosted-runtime closure: GitHub run `38058476411` correctly failed when the wrapper did not select a usable setup-python entry.
Commit `717c1d82` repaired candidate discovery; final commit `ef5e1d3b` additionally binds hosted/versioned target identity and basename to
the actual Python minor, closing the follow-up symlink mismatch finding. Fixed-digest Linux integration is 7/7 PASS with zero skip, and three
new final reviews bind `c7b93661..ef5e1d3b` with double `APPROVED`. A fresh GitHub Ubuntu 24.04 run is still required before Step 4 closes.

Task 8 documented-entry closure: GitHub run `38059629731` showed setup-python exporting a valid 3.11 runtime while the wrapper still used the
undocumented `bin/python3`; unit and integration jobs therefore did not run. Commit `37f86a8d` moved to the documented `bin/python`, and
`b1b5d7c0` eliminated ambiguous environment-path splitting before bootstrap. Evidence commit `1fb39677` corrected the literal newline
regression. Full Harness is 408 tests with 400 PASS and 8 local typed Docker skips; the same 8 fixed-digest cases pass with zero skips.
Three final reviews bind `c7b93661..1fb39677` with double `APPROVED`; a new hosted run and Obsidian sync remain required.

Task 8 final hosted closure: commit `79e85b72` adds deterministic fake-Codex CI isolation and full-history checkout for review ancestry,
then passes GitHub Actions run `38064236869`. The hosted Linux integration partition is 8/8 with zero skips; local non-Linux coverage is
402/402. Three final tracked reviews bind `c7b93661..79e85b72` with double `APPROVED`. Repository, GitHub and Obsidian synchronization
is complete; Task 8 is closed and Business Dashboard v1 becomes the next governed product Milestone.
