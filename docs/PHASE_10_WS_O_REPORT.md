# Phase 10 — WS-O: Observability, Dependency & Release Audit

Status: **PASS**
Run: [`37407655994`](https://github.com/imad-fouri/pro-bike-app/actions/runs/37407655994) · backend **success** · mobile **success**
Branch: `master` · Commit: `4473255` · Baseline: `318499f` · Date: 2026-10-06

---

## 1. What WS-O was for

Three things, in priority order:

1. Make the service **observable** — request, auth, rate-limit, AI, notification
   and live-location counters, with a cardinality guard that cannot be defeated by
   a client sending an identifier as a label.
2. **Audit dependencies** and say honestly what is and is not covered.
3. **Report the result** without dressing up a partial pass as a complete one.

The instrumentation is in-process only. There is no exporter, no scrape endpoint
and no Prometheus dependency — a deliberate decision, recorded in
`docs/observability.md`.

---

## 2. Implementation

### 2.1 New module — `backend/app/core/metrics.py`

- Thread-safe counter and gauge registry, plus bounded duration sample rings.
- A **label-name allowlist** and a forbidden-name list. A metric cannot declare a
  label that is not on the allowlist, so "I will add a `user_id` label" fails at
  the point of declaration rather than in production.
- A **value guard** that rejects, as label *values*: long strings, anything
  containing `@`, dashed UUID fragments, and bare 32+ character hex runs.
  This is the layer that survives a developer who finds a novel label name — it
  does not care what the label is called, only what it is carrying.
- Every call site is wrapped so that a metrics failure **cannot** fail a request.
  Failures log `metrics_record_failed` with the exception *type* only, never the
  label values.

### 2.2 Wired counters

| Area | Signals |
|---|---|
| HTTP | request count by method/route/status class, duration in ms |
| Auth | login, login failed, refresh, refresh reuse, refresh invalid, logout, logout-all |
| Password reset | requested (always), failed (opaque), completed |
| Rate limit | per-endpoint refusals |
| AI | outcome, latency, fallback used |
| Notifications | created, deduplicated, delivered, deliver failed, no targets, skipped (push unconfigured / fan-out above inline limit) |
| Live location | published, publish failed, read, read failed, stopped, stop failed |

Privacy properties that are **enforced**, not merely intended:

- No coordinates in metrics or logs.
- No user IDs, ride IDs, notification IDs, emails, request IDs, raw paths or free
  text in metric labels.
- Notification labels carry the **notification type enum** and an outcome — both
  bounded.
- Live-location labels carry **outcome only** — never the ride, the user, the
  Redis key, accuracy or a coordinate.
- `password_reset_requested` is incremented **unconditionally**, including for
  addresses that do not exist, because a counter that only fired on success would
  reintroduce the account-enumeration oracle the endpoint exists to remove.

### 2.3 Bugs found and fixed in passing

These were found by writing the tests, not by review:

| Bug | Symptom | Fix |
|---|---|---|
| `create_app()` structure | `_record_request` had been written such that it captured `register_error_handlers`, router registration and `return app` — the error handlers and routes were never installed | Restored the end of `create_app`; `_record_request` moved after `return` |
| UUID detection too weak | A dashed UUID was accepted as a label value | Added dashed-fragment and bare-hex detection |
| Dedupe counted as creation | `_create_one` returned the existing row on a dedupe, and the caller counted the return length as created notifications | `_create_one` now returns `(row, was_created)` |
| Zero-valued series | Mixed create/dedupe batches produced `…_count 0` series | Amount-based counting; non-positive increments ignored |
| Fixture engine leak | An ad-hoc asyncpg engine was created per test and never disposed | Replaced with the shared `db_session_factory` |

---

## 3. Live smoke

`backend/live_smoke_observability.py` runs against **real** uvicorn, PostgreSQL,
Redis and HTTP. Nothing in `app/` is stubbed or monkeypatched.

**Result: 33/33 checks passed.**

Covered: `/health` and `/ready`; `X-Request-ID` generation, echo of a valid
client id and rejection of a malformed one; auth-before-existence (an anonymous
read of an unknown ride is 401, not 404 — a 404 there would leak ride
existence); login, refresh, logout-all; anti-enumerating password reset with
**byte-identical** responses for known and unknown addresses; friend-request
notification creation; push-device registration; a full group-ride lifecycle; a
real coordinate published to Redis, read back by a participant and
round-tripped; withdrawal after cancellation; and the OpenAPI route table.

### 3.1 Privacy sweep — canaries placed in the server log

The smoke published a distinctive coordinate (`33.5731`, `-7.5898`, accuracy
`7.5`), a real email, a real password and a real push token, then grepped the
captured server log.

| Canary | Hits in log |
|---|---|
| `33.5731` / `-7.5898` | **0** |
| `7.5` (accuracy) | **0** |
| registered email / `smoke.example.com` | **0** |
| account password | **0** |
| push device token | **0** |
| client request ids (`smoke-canary-01`, malformed) | **0** |
| `metrics_record_failed` | **0** |

The log shows the instrumentation doing its job — for example
`ride_location_published | group_ride_id=…` with no coordinate, and
`notification.created | deduped=0 recipients=1 type_=friend_request` with counts
rather than recipients' identities.

*(Note: `type_` is the notification service's existing parameter-naming
convention — `type_` rather than shadowing the `type` builtin — so it appears in
that one log field name. It is cosmetic.)*

---

## 4. Dependency audit

Full detail and method in **`docs/dependency-audit.md`**. Summary:

- 15 declared dependencies; `pip-audit -r <generated>` exited 0.
- **No known findings reported by pip-audit for the declared dependency set.**
  (Deliberately not phrased as "zero vulnerabilities" — that is a claim about the
  world that no tool can make.)
- The installed environment has 47 findings across 2 packages, both **outside**
  the declared set and both classified with evidence, not assertion:
  - `pillow` 11.3.0 (35) — not declared, not imported by `app/`, present only
    because unrelated local tooling installed `moviepy → imageio → pillow`.
  - `pip` 25.0.1 (12) — the installer itself; `Required-by: pip_api`.

### 4.1 What the audit does *not* cover — stated as a gap

Reading `backend/Dockerfile`, `docker-compose.yml` and `.github/workflows/ci.yml`
surfaced four supply-chain weaknesses that are **open, not fixed**:

| Issue | Detail |
|---|---|
| Runs as root | `Dockerfile` has no `USER` directive |
| Floating base image | `python:3.12-slim`, tag not digest |
| Non-reproducible installs | `pip install -e .` from `>=` ranges; no lockfile; two builds of one commit can differ |
| Unpinned CI actions | `actions/checkout@v7.0.1`, `actions/setup-python@v7.0.0`, `subosito/flutter-action@v2.23.0` are tag references, not commit SHAs |

None is urgent today. All are cheap. All were deliberately left out of a
workstream whose mandate is observability — changing the image or the CI workflow
is a deployment decision that deserves its own review. OS-package CVEs inside the
image remain **unknown**: that needs Trivy/Grype against a built image.

---

## 5. Verification

### 5.1 Exact CI commands (`backend`)

| Gate | Result |
|---|---|
| `ruff check app tests` | **All checks passed!** |
| `ruff format --check app tests` | **120 files already formatted** |
| `mypy app` | **Success: no issues found in 91 source files** |
| `alembic upgrade head` | exit 0, at `0011_group_rides (head)` |
| `alembic downgrade -1 && alembic upgrade head` | **downgrade_exit=0, upgrade_exit=0** |
| `pytest -q` | **911 passed, 10 failed, 5 errors** (27m25s) — see §5.2 |

### 5.2 The failures, and why they are environmental

Every one of the 15 failures/errors is the same Windows socket fault:

```
OSError: [WinError 64] The specified network name is no longer available
ConnectionResetError: [WinError 64] The specified network name is no longer available
```

Two of them superficially look like assertion failures and need explaining:

- `test_concurrent_accepts_produce_one_roster_row` → `assert [200]`
- `test_start_racing_cancel_leaves_a_coherent_ride` → `assert [200]`

Both use `asyncio.gather(..., return_exceptions=True)` and then filter out
exceptions before asserting. One of the two concurrent requests **died** with
`ConnectionResetError`, leaving a single status in the list. The captured stdout
in the report shows the traceback. These are not logic failures.

The 5 "ERROR"s in `test_security_regression.py` and `test_hardening.py` are
**teardown** errors in the `client` fixture (`tests/conftest.py:66`,
`engine.begin()` → connect → `WinError 64`). The assertions themselves passed.

### 5.3 Baseline comparison — the decisive evidence

To separate "WS-O broke this" from "this machine is flaky", the failing subset was
run against the **baseline commit with all WS-O changes stashed**:

| Test | Baseline `318499f` | WS-O tree |
|---|---|---|
| `test_concurrent_sends_get_distinct_sequences` | **FAILS** — identical `ConnectionResetError` | FAILS — identical |
| the 5 remaining group-rides / data-privacy tests | 5 passed | 5 passed in isolation |
| `test_a_hostile_request_id_cannot_break_the_header` (6 cases) | passes | all 6 assertions pass; teardown errors only |

The chat concurrency test fails on the **unmodified baseline**, which establishes
it as pre-existing and unrelated to this workstream.

`tests/test_observability.py` — all **87** WS-O tests — produced **zero** failures
in the full-suite run.

**Honest caveat on the counts:** an earlier checkpoint of this workstream recorded
`906 passed / 3 failures`. The final run recorded `911 passed / 10 failed /
5 errors`. The failing *set* is wider and the counts do not match, so this report
does **not** claim the two runs are equivalent. What the evidence supports is
narrower and stated plainly: the additional failures are the same `WinError 64`
class, they pass in isolation, and one of them reproduces on the untouched
baseline. This machine's socket exhaustion worsened over a 27-minute run. Linux CI
does not have this fault, which is why CI is the authority here.

---

## 6. Mobile gates

CI runs `flutter pub get`, `dart format --set-exit-if-changed lib test`,
`flutter analyze`, `flutter test`. All executed locally against Flutter 3.47.6
stable:

| Gate | Result |
|---|---|
| `flutter pub get` | Got dependencies! |
| `dart format --set-exit-if-changed lib test` | 141 files, **0 changed**, exit 0 |
| `flutter analyze` | **No issues found!** |
| `flutter test` | **594 tests, All tests passed!** |

WS-O made no mobile changes; the mobile job is re-verified to discharge the
cancelled mobile job on the historical run.

---

## 7. Open gaps

Carried forward deliberately, not resolved here:

1. **Lockfile** — `>=` ranges everywhere mean the audited artifact is not
   reproducible and the Docker image differs build to build.
2. **Dockerfile hardening** — non-root user, digest pin, multi-stage, drop `-e`.
3. **Image scanning** — OS-package CVEs are unknown without Trivy/Grype.
4. **CI action pinning** — tags instead of SHAs.
5. **`POSTGRES_PASSWORD` in `docker-compose.yml`** — a local dev value, but the
   wrong pattern to have in version control.
6. **Scheduled audit** — this is a one-time manual run and will go stale silently.

---

## 8. Final status

**`WS-O STATUS: PASS`**

CI is the authority, and it is green. Run `37407655994`, on commit `4473255`:

| Job | Conclusion | Notes |
|---|---|---|
| `backend` | **success** | The gate is a single `run:` block, so `ruff check`, `ruff format --check`, `mypy`, `alembic upgrade head`, **`pytest -q`** and `alembic downgrade -1 && alembic upgrade head` all passed in one step |
| `mobile` | **success** | `flutter pub get`, `dart format --set-exit-if-changed`, `flutter analyze`, `flutter test` all passed |

This is the meaningful outcome of the work: **the full backend suite passed
end-to-end on Linux**, including all 87 WS-O tests. It confirms §5.2's
attribution — the `WinError 64` failures in §5.2 are a fault of this Windows
machine, not defects in the code. CI cannot report an individual test count
without authenticated log access, so the specific pass count is not quoted here;
the step conclusion is the evidence.

The historical run `37363564252` (backend success / mobile cancelled) is left
untouched as a historical record. The mobile job of run `37407655994` covers the
current `master`; it does not retroactively change that earlier run.