# Phase 7 — AI Coach Foundation

## Status

**PASS.** All gates green. No Phase 8 work started.

## Phase Scope

What was implemented:

- Provider-neutral AI layer (`backend/app/ai/`): orchestration, bounded
  context builder, versioned prompt registry, `httpx`-only provider seam,
  output validation, pre-model safety classification, deterministic localized
  fallback, Decimal cost guard.
- Coach API (5 endpoints) with owner isolation and a finalized error contract.
- Flutter Coach feature (`mobile/lib/features/coach/`): repository, typed
  models, Riverpod state with duplicate-submission guard, `/coach` route,
  Coach page with quick actions, en/fr/ar localization with Arabic RTL.
- No migration (Coach stores nothing), no provider SDK, no transcript
  persistence anywhere.

## Architecture

One protocol (`AIProvider`), two implementations (OpenAI-compatible over
`httpx`; `FakeAIProvider` as the offline test double). The deterministic
Training Engine remains the sole authority for numbers: the context builder
reads Phase 6 services and re-derives nothing, and `validate.py` drops any
model-emitted number not present in the context while discarding whole
answers that breach engine caps. The request pipeline is
rate-limit → safety → prompt → provider → validate → respond-or-fallback.

## AI Context

Included: the referenced ride/workout/week metrics with source and
calculation version, bounded notes, unavailable list, provenance.
Excluded: GPS coordinates, names, emails, raw ride samples, the API key,
and anything the client asserts (client metrics are `422`, not ignored).
Prompts separate trusted system rules, deterministic application data, and
fenced untrusted user text.

## Supported Intents

- explain_ride
- explain_workout
- weekly_summary
- training_question

## Prompt Versions

- `coach.explain_ride.v1`
- `coach.explain_workout.v1`
- `coach.weekly_summary.v1`
- `coach.training_question.v1`

Served live by `GET /coach/status` and stamped on every model answer.

## Safety

- CRISIS — self-harm patterns; deterministic crisis reply, never touches a
  training tool, carries the `crisis` caution.
- DANGEROUS_TRAINING — train-through-pain, rest rejection (including plain
  phrasings like "no need to rest at all"), doping, extreme prescriptions;
  deterministic refusal, `dangerous_training` caution.
- MEDICAL — injury/illness/body signals; deterministic non-advice pointing
  to a professional, `medical` caution.
- NONE — normal path proceeds to the provider.

Classification runs before any provider call; a safety hit performs zero
provider requests (verified live in smoke scenario 15). Severity order is
CRISIS > DANGEROUS_TRAINING > MEDICAL.

## Privacy

- No GPS sent to provider (verified by audit of `context.py`).
- No email sent (same audit).
- No unnecessary user text logging: the coach log line is metadata only
  (provider, model, intent, prompt version, outcome, latency, token counts,
  cost); message, context, and key never enter logs, and `redact()` guards
  the payload.
- Ownership isolation: every read goes through owner-scoped services; a
  foreign ride/workout is `404` with no existence signal (verified live in
  smoke scenario 17).
- No persistence: the response is never stored server-side and the client
  keeps no transcript.

## Fallback

Every provider failure ends in a deterministic, localized answer with
`fallback_used: true` and a stable uppercase caution code:
`AI_DISABLED` (disabled/unconfigured), `AI_PROVIDER_UNAVAILABLE`
(unreachable or erroring provider), `AI_TIMEOUT`, `AI_INVALID_RESPONSE`
(unparseable or schema-invalid output), `AI_REJECTED` (usable output that
breached an engine cap). Safety rejections are likewise successful
deterministic responses with stable codes (`crisis`, `medical`,
`dangerous_training`, `not_medical_advice`). Fallback prose, notes, and
safety text are fully localized in en/fr/ar with no mixed-language leakage.

## Error Contract

Request problems are HTTP errors with stable uppercase codes; provider
problems are answered (HTTP 200 + `fallback_used: true` + caution code)
because the deterministic path still works — erroring would discard content
the rider can use. Documented in ADR-11 §8.

| Code | Transport |
| --- | --- |
| `AI_CONTEXT_INVALID` | 422 — missing/conflicting pointer, bad locale |
| `AI_UNSUPPORTED_INTENT` | 422 — not one of the four (defensive) |
| `AI_RATE_LIMITED` | 429 — per-user cap or daily cost budget |
| `AI_DISABLED` | 200 + caution |
| `AI_PROVIDER_UNAVAILABLE` | 200 + caution |
| `AI_TIMEOUT` | 200 + caution |
| `AI_INVALID_RESPONSE` | 200 + caution |
| `AI_REJECTED` | 200 + caution |

## Cost Controls

- Per-user rate limit: `AI_MAX_REQUESTS_PER_USER` per hour (`allow()`).
- Daily budget: `AI_DAILY_COST_LIMIT` USD against Decimal-estimated spend;
  input tokens × input price plus output tokens × output price, zero prices
  contributing zero without disabling the check, negative prices clamped.
- Unknown/malformed provider usage is charged conservatively (estimated
  input + full output allowance) so a silent provider is never cheaper than
  an honest one.
- Money is `Decimal` end to end (micro-dollar quantum); no float drift.

## Mobile

Coach UI (`/coach`, authenticated; strangers land on onboarding): status
banner (live vs deterministic), quick actions (last ride, today's workout =
active plan else first, week summary), intent chips, ride/workout pickers
drawn from existing training providers, question input, and an answer card
showing summary, observations, recommendations with engine-checked targets,
cautions, unmeasured metrics, and provenance. States: initial, loading,
success, error, fallback. Duplicate submissions while loading are dropped
(one request per tap storm). All strings in en/fr/ar; Arabic RTL tested.

## Tests

Backend:

- pytest: **316 passed** (229 Phase 6 baseline + 87 coach), 1 pre-existing
  asyncpg warning
- ruff check: clean
- ruff format --check: clean (80 files)
- mypy: clean (65 source files)

Mobile:

- dart format: clean (75 files)
- flutter analyze: clean
- flutter test: **117 passed** (72 Phase 6 baseline + 45 coach/auth/onboarding)

New coverage this continuation: 12 cost-guard unit tests (input-only,
output-only, both legs, zero pricing, exceeded, exactly-reached,
below-limit, float-drift, missing/malformed/verbatim/zero usage), 6 locale
tests (per-language fallback + safety responses with script-leakage guards),
10-key backend/mobile contract tests, duplicate-submission, retry, loading,
workout flow, French locale, and quick-action tests.

## Builds

- `flutter build web`: built
- `flutter build apk --debug`: built

## Migration

Recorded head: `0006_training_foundation`. Downgraded one step
(`0005_routes`), upgraded to head again (`0006_training_foundation`, single
head). No Phase 7 migration created — none required, none faked.

## Live Smoke

**17/17 passed** against real uvicorn processes, real PostgreSQL, and real
HTTP provider calls (local stub speaking the OpenAI chat-completions shape;
nothing in `app/` stubbed):

1. authenticated user — PASS
2. coach status — PASS
3. English coach request — PASS
4. French coach request — PASS
5. Arabic coach request — PASS
6. explain completed ride — PASS
7. explain workout — PASS
8. weekly summary — PASS
9. training question — PASS
10. missing metric remains unavailable — PASS
11. provider success accepted as-is — PASS
12. provider failure fallback — PASS
13. timeout fallback — PASS
14. invalid provider output fallback — PASS
15. safety rejection without provider call — PASS
16. rate-limit behavior — PASS
17. second-user ownership isolation — PASS

## Defects Found

- Cost cap gated on input price only → output-priced spend invisible.
  Fixed by charging each leg per its own price; then hardened to Decimal
  with no price precondition and conservative unknown-usage charging.
  Regression tests added.
- Fallback notes and the no-caution default were English-only inside
  fr/ar answers (mixed-language fallback). Localized all five notes plus
  the medical default; added script-leakage tests.
- Arabic fallback corruptions: a Latin "Banjar" remnant in the crisis text
  and CJK characters in the medical text; wrong cycling term "قيادة"
  (driving) instead of "رحلة" (ride), aligned with the app's Arabic
  throughout. All fixed; leakage tests guard all three locales.
- "No need to rest at all" not classified as dangerous training. Rest
  rejection now recognized in plain phrasings, with negative cases for
  questions *about* rest.
- Fallback caution codes were lowercase duplicates of the contract.
  Aligned to the uppercase codes; contract documented in ADR-11 §8.
- Mobile ask had no duplicate-submission guard. Added (drop while
  loading); this caught a real integration bug where quick actions
  invalidated-then-asked in one tick and the ask was correctly refused —
  the redundant invalidate was removed instead.
- New page tests initially asserted below-the-fold content without
  scrolling (ListView lazily builds); fixed by scrolling like a user.

## Deferred

Explicitly NOT Phase 7 and not started: Garmin/Polar/Komoot integrations,
social, BLE, live location, subscriptions, ads, route generation,
autonomous plan changes.
