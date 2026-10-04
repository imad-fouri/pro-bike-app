# ADR-12 — Social Identity, Relationships, and the Privacy Boundary

Status: accepted (Phase 8.1)
Date: 2026-10-02
Supersedes: nothing
Related: ADR-05 (bikes), ADR-10 (training engine), ADR-11 (AI coach), `docs/06-realtime-social.md`

## 1. Context

Phases 1–7 built accounts, bikes, rides, routes, a training engine, and a coach.
Every one of those features is private to a single rider. The product also
promises a social layer: riders find each other, become friends, and later ride
together in groups.

A social layer is the first feature in CycleCoach where one rider's action is
observable by another. That changes the threat model from "can I read my own
data" to "can I read or manipulate someone else's". Two failure modes matter:

- **Over-exposure.** A friendship list, a public profile, or a search index is a
  standing invitation to leak private data. Email addresses must not become
  discoverable. Precise location must not become a side effect of friendship.
- **Under-authorization.** If relationship state can be forged or manipulated by
  a third party, the social graph becomes an oracle: an attacker can probe who
  blocked them, who is friends with whom, and whether a given account exists.

This ADR fixes the data model and the rules. It does not implement teams,
groups, chat, or live location.

## 2. Decision

Three tables, and one rule that shapes all of them: **the public social profile
is a projection, not the account.**

### 2.1 `social_profiles` is a separate public projection

The private account profile (`user_profiles`) holds email and account state and
is never exposed. `social_profiles` holds only what may be shown to other riders
— username, display name, bio, avatar URL, cycling category, country/city labels
— plus the three privacy settings that govern it.

Rejected alternative: widen `user_profiles` with an "is public" flag. That makes
every future column a leak waiting to happen; a new sensitive field added to
`user_profiles` becomes publicly readable by default. A separate table makes
the public surface explicit and enumerable, and `social_profiles` can be
reviewed as a whole.

The projection is created lazily on first access and seeded with the display
name from the private profile. **It never invents a username**: a username is
opt-in, so `NULL` is the normal state and `UNIQUE` allows many NULLs.

### 2.2 One canonical row per unordered pair

`friend_relationships` stores `user_a_id < user_b_id`, enforced by a CHECK
constraint, with `UNIQUE(user_a_id, user_b_id)`. A pair therefore has exactly
one row; "A requested B" and "B requested A" cannot both exist.

Rejected alternative: directional rows (`requester_id`, `addressee_id`). That
permits a duplicate inverted pair, requires a merge path, and makes "are these
two friends?" a two-row question with a race.

Status is `PENDING` or `ACCEPTED` only. Rejection and cancellation **delete the
row** rather than recording a `REJECTED` state. A rejected request is not a
relationship fact; keeping it would let a rejected rider reappear in history and
would grow the table without bound. `requested_by_user_id` records direction
within the pending row, which is all that is needed to decide who may accept.

### 2.3 A block is a wall, not a status

`user_blocks` is a separate directional table. Blocking annihilates any
`friend_relationships` row for that pair in the same transaction.

Rejected alternative: `RelationshipStatus.BLOCKED`. A block is not a stage of
friendship and must not be represented as one. Separate tables also make the
"block deletes the relationship" rule a transaction over two tables rather than a
state transition that could be forgotten.

Consequences, all deliberate:

- **Unblocking restores nothing.** The relationship row was deleted when the
  block went up. Reconnecting starts from a new request. A rider who blocks and
  unblocks must never find a friendship silently restored.
- **The blocked party is never told.** Requesting a blocked rider returns
  `404`, identical to a nonexistent account, so the block list is not an oracle.
- **Inbound blocks are invisible.** `GET /social/blocks` lists only riders *you*
  blocked. A rider who blocked you sees `BLOCKED_BY_USER` and no action.

### 2.4 Privacy is a filter at read time

Three independent settings, all on `social_profiles`:

| Setting | Values | Effect |
|---|---|---|
| `profile_visibility` | `public` / `friends` / `private` | how much of the profile others see |
| `allow_friend_requests` | `everyone` / `nobody` | whether requests are accepted at all |
| `search_visibility` | `discoverable` / `hidden` | whether the rider appears in search |

`private` and `hidden` riders are **excluded** from search results, not returned
with blanked fields: a redacted-but-present row still confirms the account
exists and still leaks a display name.

`friends` visibility redacts rather than excludes, because the rider has already
granted a relationship; `limited: true` in the response tells the client the
blank sections are *withheld by policy* and not *empty by choice*, so the UI
shows a locked affordance instead of implying the rider wrote no bio.

## 3. Why live location is excluded from the social profile

**No social model contains coordinates, and none will.** `city` is a
free-text label a rider types about themselves; it is not derived from GPS and
is not a coordinate.

The reasoning:

1. **Friendship is not consent to location.** A rider who accepts a friend
   request has agreed to share a name and a riding history. They have not agreed
   to be tracked. Bundling the two would make the social layer a tracking
   opt-in that nobody reads.
2. **A profile field is the wrong shape for a moving value.** "Where is this
   rider now" needs sessions, grants, expiry, throttling, and audit — the model
   in `docs/06-realtime-social.md`. A column on a profile table would invite
   exactly the naive implementation that leaks.
3. **City is not location.** "Casablanca" is a claim a rider makes once. It is
   coarse, static, self-reported, and true even when the rider is abroad. It
   cannot be used to find anyone.

Location sharing, when it ships, will be an explicit, time-bounded, revocable
grant — never a field on a profile. The mobile client states this in the UI
rather than leaving riders to guess.

## 4. Authorization principles

1. **The viewer comes from the JWT.** Never from a request body. Client-supplied
   ids may only name the *target* of an action, and every one is re-resolved
   server-side against the caller's identity.
2. **404, not 403, for anything that is not yours.** Accepting someone else's
   request, cancelling someone else's request, or acting on a blocked pair all
   answer identically to a nonexistent object. A `403` would confirm the object
   exists.
3. **Read paths are filtered, not gated by the client.** The server decides what
   a viewer may see. The client renders the decision; it never decides.
4. **Search matches username and display name only.** Never email, never
   phone, never an internal id.
5. **Logs carry ids only.** `social.request_created` records who acted on whom.
   No tokens, emails, bios, or message content.

## 5. Concurrency

The canonical pair UNIQUE constraint is the arbiter for `INSERT`, but unique
constraints alone leave a check-then-act window: a block landing between the
blocked-check and the request `INSERT` would produce a blocked pair with a live
request.

Every pair mutation therefore takes one **advisory transaction lock** on a hash
of the canonical pair key before doing anything:

```
pg_advisory_xact_lock(hashtext(f"social:{low}:{high}"))
```

This makes send / accept / reject / cancel / remove / block mutually exclusive
per pair. One lock per transaction means no lock ordering and no deadlock.
`FOR UPDATE` additionally serializes accept against concurrent mutation of the
same row.

Concurrent cross-requests resolve to exactly one row; the loser re-reads and
answers from the winning row (409 with a specific code). Simultaneous accepts
converge on `ACCEPTED` — accept is idempotent.

Mobile mirrors this rather than replacing it: it prevents duplicate submission
client-side, but the server's constraint is what actually holds.

## 6. Consequences

Accepted costs:

- Two queries per profile view where one join might do. Bounded and indexed.
- The social graph is not queryable as a graph (no recursive CTEs over an
  adjacency table). Acceptable: Phase 8.1 needs one hop, not friend-of-friend.
- Deleting a rejected request loses the history. Intentional.

## 7. Future integration

Deferred, but constrained by this ADR:

- **Teams / group rides** extend `user_blocks` with team-scoped blocks, not the
  relationship rows. A team block must not silently destroy a personal
  friendship.
- **Live location** is a separate grant table
  (`location_sessions(granter, grantee, scope, expires_at, revoked_at)`), never
  a profile column. Friendship is one possible grantee scope, and never an
  implicit one.
- **Chat / notifications** must respect `BLOCKED` in both directions. A blocked
  rider must not be able to push a notification that reaches their blocker.
- **Search** may widen its matched columns only by explicit ADR change, and only
  to fields a rider has chosen to publish. Email is never a candidate.