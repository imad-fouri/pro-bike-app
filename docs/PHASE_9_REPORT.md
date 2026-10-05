# Phase 9 -- Group Rides, Immutable Route Pins, Ride Chat & Live Location

Status: **PASS**. All gates green.

Architecture decisions: `docs/adr/ADR-16-group-rides-live-location.md`.

## 1. Audit

Before writing code I inspected the backend (`models`, `services`, `api/v1`,
`core/errors`, `core/rate_limit`, migrations, tests), the Flutter feature
architecture, and the l10n/routing/test conventions.

**Reusable, unmodified:** the `{error:{code,message,details}}` envelope; the
`(code, message, status)` service-error shape; `get_current_user`; the
`items/total/page/page_size` envelope; `rate_limit.allow`; the advisory-lock
pattern; the `_uuid`/`_values`/`__table_args__` ORM conventions; the
`create_type=False` migration style; `_notify_safely`; and the whole
`features/teams` + `features/chat` Flutter shape.

**Conflicts identified and resolved before any code:**

1. **A ride needs membership that teams do not have.** A team roster is a
   membership list. A ride also needs *pending invitations*, *withdrawals* and
   *removals* -- and the roster must keep a row for a rider who left, or their
   history loses its author. That makes the roster useless as an authorization
   source, exactly as in 8.3. Resolved: authorization reads the **live
   `group_ride_participants` row status** inside the transaction (ADR-16 S1, S3).
2. **A block inside a ride channel.** Resolved: blocks bound **direct messages
   only**. A block is a personal boundary; severing or filtering a ride channel
   would broadcast it to everyone on the ride (ADR-16 S5).
3. **A route pin must not drift.** Pinning "the current version" would silently
   change the geometry riders agreed on the moment somebody edited the route.
   Resolved: the pin is the **composite pair** `(route_id, route_version)` against
   `route_versions`, immutable, and the client has no "latest" option (ADR-16 S4).
4. **`starts_at` looked like a join gate.** It is not one. Resolved: it is
   scheduled metadata; there is no `JOIN_CUTOFF_SECONDS`, and the roster freezes at
   `started`, not at the scheduled time (ADR-16 S3).

**Delivered:** 2 new tables plus a `group_ride_id` column on `conversations`,
4 new enum types, 16 endpoints, 4 Flutter screens, 92 `groupRide.*` keys in each of
3 locales, 4 notification types.

## 2. Backend

| File | Contents |
|---|---|
| `app/models/group_ride.py` | `GroupRide`, `GroupRideParticipant` + 4 enums, all `ck_`/`uq_`/`fk_` constraints named |
| `app/models/chat.py` | `conversations.group_ride_id` (UNIQUE, nullable) |
| `app/models/notifications.py` | 4 new `NotificationType` values |
| `app/schemas/group_ride.py` | create/invite/respond inputs, `GroupRideOut`, roster, invitation, batch-invite result |
| `app/services/group_ride_service.py` | lifecycle, roster transitions, route validation, re-invitation dedupe, `_notify_safely` |
| `app/services/ride_location_service.py` | Redis-only consented location: publish, revoke, read |
| `app/api/v1/group_rides.py` | 16 routes registered in `app/api/v1/__init__.py` |
| `alembic/versions/0011_group_rides.py` | additive on `0010`, reversible, constraint names match the ORM |

No function accepts a role, an actor id, or an authorization flag from a caller.
Ride state is never computed on the client.

### Lifecycle and roster

`open -> started -> completed`, with `cancelled` reachable from `open` or
`started`. A terminal state has no path back, so `complete` requires `started` and
a cancelled ride can never be dragged forward.

Roster states are `invited`, `joined`, `declined`, `left`, `removed`. Only `joined`
grants ride, chat and location visibility. **Additions freeze at `started`;
withdrawal never does** -- that asymmetry is the consent right working as intended,
and it is why the visible location set can only ever shrink.

Start and cancel race legally: both are applicable from a live state, so
`[200, 409]` and `[200, 200]` are both correct outcomes and the final state must be
coherent either way. The tests assert exactly that rather than picking one.

### Route pin

`fk_group_rides_route_pin` is a composite FK from `(route_id, route_version)` to
`route_versions(route_id, version_no)`, `ON DELETE SET NULL` clearing **both**
columns. `route_id` and `route_version` are `CHECK ((both null) OR (both set))`, so
a half pair is unrepresentable rather than merely discouraged. There is no update
path that touches either column.

### Chat

A ride channel is authorized from the **live roster**, not from
`conversation_members` -- a third authorization basis after DM (block policy) and
team (live membership). A removed or withdrawn rider gets
`CHAT_CONVERSATION_NOT_FOUND`, the same anti-enumeration answer as a stranger.

`completed` and `cancelled` close the channel: `_assert_can_send` refuses with
`CHAT_GROUP_RIDE_CLOSED`. Edit and delete still work afterwards, because they only
require `_require_participant` -- a rider's own history stays theirs.

Inbox rows and `total` use the **same SQL-filtered standing checks**. Filtering
after paginating would fix the row and leave `total` counting what was hidden,
which reads as data loss to the rider.

`_conversation_view()` loads `peer` only for `DIRECT`. A ride channel has three or
more riders and no peer, so the peer join had to be conditional or the row would
validate against a null peer it never had.

### Live location

Redis hash `gr9:share:{ride_id}`, TTL 300s, stale cutoff 60s, **server**
timestamps. Opt-in is explicit: publishing only ever happens from a button a rider
pressed. A block between two riders is filtered **bidirectionally**. Coordinates
are never logged.

Redis being unreachable is a **503, never an empty list**. Collapsing the two would
make "nobody is sharing" and "we could not ask" indistinguishable -- which is
precisely the distinction a rider needs when deciding whether to keep riding.

### Notifications

Four types: `group_ride_invitation`, `group_ride_accepted`, `group_ride_started`,
`chat_message_group_ride`. Keys are `notifications.type.<wire>`, a **storage
contract** -- renaming one orphans rows already in a rider's history.

`group_ride_started` is the one fan-out and is bounded by the roster: only `joined`
riders are told, so an `invited` rider who never responded cannot learn the ride
started and a withdrawn rider gets no push for a ride they left. Its dedupe key is
the **ride**, not the roster, so a retry after someone joined mid-ride does not
notify them of a start they missed.

Re-invitation dedupe is `participant_id` + the invitation generation timestamp.
The roster row's id is stable for the life of the ride, so keying on it alone would
make every re-invitation a silent duplicate -- the rider would be re-invited and
never told, which is worse than not re-inviting at all.

**Notification failures are isolated.** `group_ride_service._notify_safely` matches
`social_service` and `team_service`: a notification is an accelerant, never a
precondition. Every call site is after its own `db.commit()`, so the alternative to
swallowing is showing a rider a failure for an action that demonstrably succeeded --
and inviting a retry that re-opens a confirmation dialog over a roster that already
grew.

## 3. Flutter

| File | Contents |
|---|---|
| `features/group_rides/domain/group_ride.dart` | 4 enums, 7 models, `viewerRow`/`viewerStatus` |
| `features/group_rides/domain/ride_validators.dart` | mirrors the server limits; pin is both-or-neither |
| `features/group_rides/data/group_ride_repository.dart` | 16 calls, `auth: true` throughout |
| `features/group_rides/presentation/group_ride_providers.dart` | providers, mutations, teardown ordering |
| `features/group_rides/presentation/ride_location_controller.dart` | opt-in publish, scripted-clock polling |
| `features/group_rides/presentation/group_rides_page.dart` | invitations + ride history |
| `features/group_rides/presentation/group_ride_form_page.dart` | create, date picker, exact version pin picker |
| `features/group_rides/presentation/group_ride_detail_page.dart` | lifecycle, roster, chat, route, location |
| `features/group_rides/presentation/ride_invite_sheet.dart` | friend selection, batch invites, partial results |
| `features/group_rides/presentation/group_ride_widgets.dart` | badges, roster, location panel, rider dots |

Riverpod 3.4.3 throughout: no `StateNotifier`, family notifier creators receive
only the argument, dependencies are built in `build()`, methods read `.notifier`.

### Decisions worth naming

**Viewer status is derived, not sent.** The backend's `viewer` carries only
`is_organizer` and `is_joined`, which cannot distinguish "invited, awaiting my
answer" from "no roster row at all". That distinction decides whether Accept and
Decline are offered, so it is read from the roster -- already in the same payload --
via `viewerRow(myUserId)`. A null id yields null status, never a guess: putting the
wrong rider's row on screen is worse than showing no buttons.

**Closed rows in the list are not tappable.** The list shows every ride the viewer
holds any roster row on, including `cancelled` and rides they left. Those rows are
deliberately **not** tappable, because a non-member's detail read is a guaranteed
404 and a guaranteed error screen.

**No "latest version" in the pin picker.** `RadioGroup<_PinChoice>` over
`(routeId, version)` pairs, with `_PinChoice` implementing equality. A version
selector defaulting to newest is exactly the drift ADR-16 S4 forbids.

**Ride channels are read-only once terminal.** `Conversation.isRideChannelClosed`
plus an optional `closedBecauseRideStatus` passed by the two callers that already
hold an authoritative status -- the inbox row and the ride page. The router carries
it as a query parameter beside the existing `name`. This is a **hint, never an
authorization**: `_assert_can_send` re-reads the ride status inside the
conversation lock. Defaulting the other way would blank the composer of a healthy
ride whose status this build has not been taught, which is a worse failure than a
rejected send. A cold notification deep link carries no status, shows the composer,
and gets a clear `chat.error.rideClosed` sentence instead of a generic failure.

**Location teardown ordering is load-bearing.** `_afterMutation` invalidates
`rideLocationProvider`, an `autoDispose` family; invalidating it disposes the live
controller, and the next read builds a fresh one whose `_everPublished` is false.
So `leave`, `complete` and `cancel` call `_stopSharingThenInvalidate` **before**
`_afterMutation`. The reverse order silently skips the server-side revoke and leaves
a position in Redis until its TTL, on a ride the rider just left. The doc comment on
the helper said so; the call sites disagreed with it. There is now a regression test
that fails if the order is reversed.

**A notification is not a proof of a schedule.** Home's "next event" card pointed at
`/routes`, which is not an event -- a route is a path, and routes already have a tab
and a profile entry. It now points at `/group-rides`.

## 4. Localization

92 `groupRide.*` keys per locale, EN/FR/AR, with identical key sets across all
three. The namespace is `groupRide.*` and not `ride.*` because `ride.start`
already means "start recording a ride" -- reusing it would have produced two
different meanings behind one key.

Status and role labels are derived from the wire value
(`groupRide.status.$wire`), so a new backend status degrades to an English label
rather than leaking `snake_case` into the UI.

## 5. Tests

**Backend -- 672 passed.** `ruff` clean, `mypy` clean over 90 files.

| Suite | Result |
|---|---|
| `test_group_rides_api.py` | 49 passed |
| `test_ride_location.py` (real Redis) | 31 passed |
| `test_chat_api.py`, `test_notifications_api.py` | 99 passed |
| Full suite | 672 passed in 18m23s |

The group-ride tests are grouped by the **invariant** they defend, and each group
name states the rule, so a failure reads as a broken rule rather than a broken
assertion. Nothing about the ride graph is mocked: real PostgreSQL, real JWT, real
concurrent connections. The only mock in the file is a notification hook made to
raise, because the behaviour under test there is precisely that the failure is *not*
swallowed -- a mock that "works" would prove nothing.

Three defects were found by writing tests rather than by reading code:

1. **A fake-green assertion.** `test_accepting_twice_is_idempotent` ended in
   `assert ... or True`, and the real comparison underneath was wrong: it read
   `roster[0]`, which is the organizer, and compared the *guest's* `responded_at`
   against it. The property is real -- the early return on `joined` does preserve the
   timestamp -- so the fix selected both rows by `user_id` and asserted the whole
   roster is unchanged, rather than suppressing the failure.
2. **A missing notification-isolation guarantee.** `social_service` and
   `team_service` both had `_notify_safely`; `chat_service` inlined the same
   try/except; `group_ride_service` had nothing, so a notification error would 500
   a ride mutation that had already committed. Four tests now cover invitation,
   acceptance and start, plus one that proves the isolation is **per-call**: one
   rider's failure must not silence the next rider's notification.
3. **An ordering-dependent flake in the Redis fixture.** `clean_live_ride` reset
   the cached client only in *teardown*, so the first test in the file inherited a
   client bound to a previous test file's event loop and failed with
   `RuntimeError: Event loop is closed`, surfacing as a confusing 503. The file
   passed in isolation every time -- only the full suite triggered it. The reset now
   happens on both sides and cannot itself fail.

**Flutter -- 574 passed.** `flutter analyze` clean.

| File | Tests | Covers |
|---|---|---|
| `test/group_rides_test.dart` | 56 | lifecycle parsing, derived viewer status, pin pairing, validators, batch partial progress, opt-in, teardown, failure visibility, polling, teardown ordering |
| `test/group_rides_widgets_test.dart` | 55 | status labels, roster affordances, "nobody" vs "could not ask", permission remedies, EN/FR/AR, RTL, error mapping |

111 new tests. The location tests drive a scripted ticker and a fake gateway, so
"polled twice" and "stopped once" are assertions about the code and not about
scheduler timing. The widget tests settle after pumping, because `pumpWidget` alone
leaves `MaterialApp` a frame short of pushing its initial route -- the widget under
test is absent and every assertion would pass vacuously.

Three pre-existing Phase 8 tests were **updated, not weakened**, where Phase 9 made
their assertions obsolete:

- `notifications_test.dart` used `group_ride_invitation` as its example of a type
  this build does not know. That type now exists, so the input was changed to a
  genuinely future one (`group_ride_scheduled`) and a complement test was added
  asserting all four Phase 9 types parse to themselves -- otherwise a type added to
  the enum but missed by `parse` would silently degrade to `system` and lose its
  deep link.
- `chat_test.dart` and `teams_test.dart` asserted `/group-rides` **stays** a
  placeholder. They now assert the opposite, and additionally assert that
  `/group-rides/new` is registered before `/group-rides/:id`, because reversing that
  order makes the create form unreachable.

## 6. Not built

Deliberately out of scope per the phase constraints: feed, likes, comments,
followers, ads, payments, marketplace, navigation, geocoding, public tracking,
default location sharing, and any unrelated infrastructure. No location history is
stored locally or server-side; there is no GPS retry buffer and no durable queue of
fixes, because a durable copy of where somebody was is the accident ADR-16 S6
refuses to have.

## 7. Gates

```
backend  ruff      All checks passed!
backend  mypy      Success: no issues found in 90 source files
backend  pytest    672 passed
mobile   analyze   No issues found!
mobile   test      574 passed
```

## 8. Commits

One commit: the Phase 9 backend, Flutter client, tests, ADR and this report.
