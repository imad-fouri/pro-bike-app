# CycleCoach — Professional Product Reality Audit

Audit-only task. No files modified, no dependencies added, no migrations run,
no screens redesigned. Every claim below is tied to code read during this
audit (file:line). Prior phase reports were treated as unverified until the
code confirmed them.

Status vocabulary: IMPLEMENTED (wired end-to-end) · PARTIAL (works with
known gaps) · TESTED-ONLY (unit/integration tests, never device-verified) ·
PLACEHOLDER (visible stub) · MISSING (no code) · STATIC (hardcoded content
shown as if real) · HYPOTHESIS (likely cause from code reading, NOT verified
on device).

## 1. Executive summary

CycleCoach is a **well-architected backend with a thin product surface**.
The server side (auth, rides ingest, integrity, training, social, ranking,
subscriptions) is genuinely implemented and heavily tested (1243 backend
tests, CI gates). The mobile app talks to it correctly wherever a screen
exists. But the three things that make Strava/Komoot feel professional —
**accurate GPS recording, a live map, and an activity history** — are the
three weakest areas, and the user's report is accurate on all three:

1. **GPS tracking is unproven and structurally suspect.** The filtering
   pipeline exists and is unit-tested, but no real-device GPS evidence
   exists anywhere in the repo. Code reading finds three concrete
   inflation mechanisms (stationary drift adds distance, max/current speed
   derive from noisy position deltas, raw phone altitude feeds elevation
   gain) plus one platform gap (no Android foreground service, so the OS
   may suspend recording when the screen locks). These are HYPOTHESIS
   until a device test confirms them — but they fully explain "inaccurate".
2. **The map is real but nearly invisible.** `flutter_map` + OSM tiles is
   integrated and correct — yet it renders on exactly two screens (route
   create/edit, route detail). The ride recording screen shows a static
   doodle with two dead buttons. Group rides show live positions as a
   text list. Home shows no map. There is no place search, no routing, no
   geocoding, no elevation, no offline tiles, no turn-by-turn.
3. **There is no activity history.** `/rides` is a placeholder page and
   the mobile API client has no list-rides method, even though
   `GET /api/v1/rides` exists server-side. A cycling app with no ride
   feed is missing its core loop. The home screen fills the gap with
   hardcoded demo stats (180 km, 2,400 m) — user-visible mock data.

Honest headline: **backend B+, mobile product surface C, GPS-on-device F
(unverified)**. Nothing below should be read as "complete" unless marked
device-verified — almost nothing is.

## 2. Screen-by-screen findings

| Screen (route) | Verdict | Evidence |
|---|---|---|
| Splash (`/splash`) | IMPLEMENTED | Brand screen while restore resolves; boot covered by `app_test.dart`. |
| Onboarding (`/onboarding`) | IMPLEMENTED | Static copy + Get Started → register; real. |
| Sign In (`/login`) | IMPLEMENTED | Real login, glass card, friendly errors. No map involvement. |
| Sign Up (`/register`) | IMPLEMENTED | Real registration via existing contract. |
| Forgot Password (`/forgot-password`) | IMPLEMENTED | Real reset request, anti-enumeration preserved. |
| Home (`/home`) | PARTIAL + STATIC | Auth greeting + notification bell + local recovery banner are WIRED. **Weekly Distance 180 km, Elevation 2,400 m, sparkline trends, Today's Workout card, Next Event card are hardcoded** (`home_page.dart:71-105,204-267,367-448`). No server dashboard endpoint exists. |
| Bottom tab Rides (`/rides`) | PLACEHOLDER | `PlaceholderPage` with dead button (`app_router.dart:288-292`). **No ride history UI exists.** |
| Ride Start (`/ride/start`) | IMPLEMENTED | Bike + route pickers wired; permission gate here (correct). |
| Ride Recording (`/ride/recording`) | PARTIAL | Live tiles (distance/speed/avg/climb/GPS dot/sync) from local GPS engine — real. **Map panel is a static `CustomPaint` doodle with two empty `onPressed: () {}` buttons** (`ride_recording_page.dart:193-256`). HR/cadence honestly show `—`. No `flutter_map` here. |
| Ride Summary (`/ride/summary`) | PARTIAL | Local metrics only; **server finish summary is fetched and then not rendered**; FTP/VO₂max explicitly deferred. Offline notice works. |
| Ride Recovery (`/ride/recovery`) | IMPLEMENTED | Local-only resume/finish/discard from Drift; correct. |
| Routes list/detail/form | IMPLEMENTED | Full CRUD, GPX import/export, versions, offline geometry cache, tap-to-add points on a real map. **No place search; no "ride/navigate this route" action** (only pin-to-ride via start screen). |
| Training overview/history/settings, Workouts | IMPLEMENTED | Server-computed numbers, client formats only; honest `—` for unmeasured. No local physiology math. |
| AI Coach (`/coach`) | IMPLEMENTED | Server answers with provenance; deterministic fallback; Pro gate honest. |
| Rankings (`/performance`), Challenges | IMPLEMENTED | Server-authoritative boards/progress; verified-only activities count. |
| Teams/Chat/Group Rides/Social/Notifications/Profile/Bikes | IMPLEMENTED | All server-wired; group-ride live positions polled 2 s but **list-only, no map** (`group_ride_widgets.dart:246-399`). |
| Settings (`/settings`) | PLACEHOLDER | Reached from profile (`profile_page.dart:119`). Dead end. |

## 3. Feature matrix

| Feature | Current Evidence | Status | User Impact | Required Work | Acceptance Test |
|---|---|---|---|---|---|
| GPS acquisition | `GeolocatorSource`, best accuracy, 5 m filter (`location_source.dart:41-88`) | TESTED-ONLY | Baseline works in theory; unverified outdoors | Device field test (see §7 P0) | 10 km pocket-carry ride completes with <5% gap loss |
| GPS filtering (accuracy/jump/order) | Mirrored engines mobile+server, unit tests both sides (`gps_processor.dart`, `gps_engine.py`, `test_gps_engine.py`) | TESTED-ONLY | Rejects gross garbage; see drift row | None before device data | Replay a recorded noisy track; assert verdict parity |
| Stationary drift | `distanceM += dist` unconditional (`gps_processor.dart:127`); sub-1 m/1 s only dupe case | HYPOTHESIS (code-verified mechanism) | Standing still inflates distance; likely #1 "inaccurate" cause | Drift gate: below speed+accuracy confidence → no distance accrual | 5 min stationary adds <50 m |
| Speed display | Position-delta derived; platform `p.speed` streamed but unused for display (`ride_recorder.dart:192-194`) | PARTIAL | Jittery current/max speed | Prefer platform speed w/ fallback; clamp display | Steady 20 km/h ride shows 18–22, max sane |
| Elevation gain | Raw phone altitude, 3 m threshold (`gps_processor.dart:130-140`); no DEM | HYPOTHESIS | Phone vertical noise inflates climbing | Device verify; smoothing/DEM later | Known 300 m climb reads 270–330 m |
| Background recording (Android) | Permissions declared; **no `<service>`, bare `MainActivity`, no foreground-task plugin, no lifecycle observer in ride code** | MISSING | OS may kill/suspend track on screen lock — likely #2 cause of "inaccurate"/gaps | Foreground service + persistent notification + lifecycle handling | 1 h screen-off ride: zero gaps, drain documented |
| Background (iOS) | `location` mode declared (`Info.plist:33-36`); no background-task code verified | TESTED-ONLY | Unknown | Same as Android + iOS field test | Same as Android |
| Kill/crash recovery | `finalizing`/`pending_sync` states, recovery page, idempotent chunks+watermark (`ride_recorder.dart:272-306,414-440`) | TESTED-ONLY (unit/fake level) | Good design, unverified under real kill | Device kill test | Kill at 50% → resume → zero duplicates, completes |
| Offline queue + sync | 100-pt chunks, 15 s timer, retry-safe dedupe, 409 paused/finished (`ride_recorder.dart:340-412`; server row locks) | IMPLEMENTED (server-proven by tests; device NOT verified) | Core loop sound | Device airplane-mode test | Offline 10 min → online → full sync, no dupes |
| Live map while recording | Static doodle + dead buttons; `RouteMapView` never embedded | MISSING | "Map is missing" complaint is literally true here | Embed live-position map (flutter_map already integrated) | Rider dot tracks position on tiles during ride |
| Route map (plan/review) | `RouteMapView`: tiles + polyline + markers + attribution; tap-to-add; read-only detail | IMPLEMENTED | Works where it exists | — | Existing widget tests + eyeball |
| Place search / geocoding | Zero code (`geocod|nominatim|osrm` → no hits); backend providers `none` | MISSING | Cannot find places/destinations | Provider decision (Nominatim/Photon) + UI | Search "city" → pick → route anchored |
| Snap-to-road routing | MISSING (same as above; backend `ROUTING_PROVIDER=none`) | MISSING | Tap-to-add only; no cycling routes | Router decision (OSRM/Valhalla) or defer explicitly | A→B generates rideable geometry |
| Elevation profile chart | Parsed (`route.dart:187-201`), **never rendered**; no chart lib in app | PARTIAL (data, no UI) | Route planning blind to climbs | Draw profile from existing data | Route with climbs shows profile matching gain text |
| Offline map tiles | Explicitly out of scope (`route_map.dart:11-13`) | MISSING | Dead map without signal | Tile cache (later phase) | Airplane mode shows cached tiles on viewed routes |
| Turn-by-turn / rerouting | MISSING, no nav engine | MISSING | Not a navigation app (Komoot gap) | Phase after routing exists | Follow route with off-route warning |
| Activity history/feed | `/rides` placeholder; `RidesApi` has no list method (server `GET /rides` unused by mobile) | MISSING | No training log — Strava-core loop absent | List endpoint wiring + history UI + detail link | Past rides listed, tap opens summary |
| Ride analytics (splits/charts) | Distance/moving/avg/max/climb/current only; no splits, no charts, sensors `—` | PARTIAL | Basic but honest | Splits + profile chart; sensors later | 10 km ride shows /km splits |
| Home dashboard data | Hardcoded 180 km / 2,400 m / workout / event | STATIC (user-visible mock) | Feels fake on first screen | Real aggregates endpoint + wire-up, or remove cards | Numbers match last-7-day server data |
| Tile policy/licensing | Default OSM tiles, `userAgentPackageName: 'com.cyclecoach.app'` ≠ real id `com.cyclecoach.cyclecoach` | PARTIAL | Policy breach risk at scale | Fix UA + tile proxy/cache plan | UA correct; tile TOS reviewed |

## 4. Separate findings

### 4.1 GPS
Pipeline exists on both ends with identical thresholds (25 m accuracy
reject, 80 m/s jump reject, 1 m/s moving, 3 m elevation) and honest
unit coverage — but **zero meters of it have ever been validated on a
device** (no Phase-3-style ride exists in history). Code-verified
inflation mechanisms: unconditional distance accrual (§3 drift row),
position-derived speeds, raw-altitude gain, generous 80 m/s (288 km/h)
jump gate, 10–25 m fixes fully metered. Platform gap: Android has no
foreground service at all. Verdict: well-tested theory, unverified
product. Every GPS accuracy claim in earlier reports must be read as
"unit-tested", never "device-verified".

### 4.2 Map rendering
`flutter_map` 8.3.2 + OSM raster + mandatory attribution chip is a
correct, policy-respecting integration — confined to two screens. The
recording screen (where users stare for hours) has a painted doodle and
dead zoom buttons. Group rides render positions as text rows. OSM's
default tile endpoint is a light-use service; a fleet of riders needs a
tile proxy or commercial source before scale, and the user-agent string
doesn't match the application id today.

### 4.3 Routing
None exists. Route creation is manual tap-to-add + GPX import; the
backend routing/geocoding providers are `none` by config. Any "route to
X" expectation currently ends at a dead end (no search box exists to
even try). This is a product decision to make, not a bug to fix.

### 4.4 Navigation
No turn-by-turn, no off-route detection, no voice/prompts, no rerouting.
Group-ride "live location" is a 2-second-polled roster list behind a
consent switch — privacy-correct, mapless. Do not describe the app as
having navigation.

### 4.5 Analytics
What is shown is trustworthy (server-computed training numbers, local
honest ride metrics, `—` instead of zeros, verified-only competition).
What is missing is breadth: no history, no splits, no charts, no
rendered elevation profile (data already parsed), home numbers faked.
The analytics story is "narrow but honest", except home.

## 5. Critical blockers

**P0 (app feels broken/basic without these):**
1. Android background recording — implement foreground service +
   lifecycle handling + screen-off device test. (Hypothesis: primary
   cause of track gaps.)
2. Live map on the recording screen — embed `RouteMapView`/flutter_map
   with rider dot; remove dead buttons.
3. Activity history — wire `GET /rides` to a real feed + detail; replace
   `/rides` placeholder.
4. Home demo data — replace hardcoded stats/workout/event with real
   aggregates (new endpoint likely needed) or remove the cards.
5. Device GPS validation protocol — the field test that converts §4.1
   hypotheses into verified fixes (drift gate, speed source, altitude).

**P1 (professional parity):**
6. Stationary-drift gate + platform-speed display (after P0.5 data).
7. Elevation profile chart from already-parsed data.
8. Per-km splits on summary.
9. Place search + geocoding (provider decision required).
10. "Ride this route" affordance from route detail.
11. Tile user-agent fix + tile scaling policy.

**P2 (later phases):**
12. Snap-to-road routing engine + turn-by-turn + rerouting.
13. Offline tile packs.
14. HR/cadence sensor pairing.
15. `/settings` screen (placeholder today).

## 6. Phased implementation plan

**Phase A — Measure first (no product changes).** Instrumented field
test: 10 km pocket-carry ride + 5 min stationary + 1 h screen-off ride
on Android. Collect raw fix log (add opt-in raw logging — currently
only verdicts persist). Dependencies: none. Acceptance: gap inventory
(count/duration of signal loss), stationary drift meters, distance vs
reference (bike computer/second phone) within ±X% *recorded as-is*.
This data decides Phase B scope — do not pre-fix blindly.

**Phase B — Recording integrity (depends on A).** Foreground service +
wake/lifecycle handling; drift gate; platform-speed display; kill
recovery device test; airplane-mode sync test. Acceptance: screen-off
hour ride with zero gaps; stationary <50 m/5 min; kill-at-50% resumes
with zero duplicates; offline 10 min syncs complete.

**Phase C — Map where the rider looks (depends on B for position
quality).** Live map on recording screen (reuse `RouteMapView`
patterns); retire doodle + dead buttons. Acceptance: dot tracks on
tiles for a full ride; zoom buttons functional; no jank (>55 fps
median on mid-range device).

**Phase D — History + honest home (independent of B/C; needs one new
aggregate endpoint).** Ride feed UI (list + detail via existing
detail endpoint + summary data), `/rides` placeholder removed; home
cards wired to real 7-day aggregates or removed. Acceptance: last 10
rides visible with correct totals; home numbers equal server
aggregates; no hardcoded stats remain.

**Phase E — Route intelligence (depends on provider decisions).**
Search/geocode → snap-to-road → rendered elevation profile → splits →
"ride this route". Acceptance per matrix rows. Turn-by-turn explicitly
deferred to Phase F.

Each phase keeps existing gates green (1243 backend + 742 mobile tests,
ruff/mypy/analyze/format, CI `ci.yml`) and adds its acceptance tests as
automated tests where possible (matrix replay, widget render) plus the
device protocol where only a device suffices.

## 7. Must NOT be described as complete

Background GPS recording · GPS/distance accuracy · live ride map ·
activity history · home dashboard data · place search · routing ·
turn-by-turn navigation · offline maps · elevation profile display ·
splits/charts · sensor support · `/settings` · iOS field behavior ·
any physical-device claim whatsoever. The only device evidence in repo
history is a pre-redesign emulator login click-through — valuable for
auth, worthless for GPS.
