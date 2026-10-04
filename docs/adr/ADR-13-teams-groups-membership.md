# ADR-13 — Teams, Membership, and the Non-Cascading Block Invariant

Status: accepted (Phase 8.2)
Date: 2026-10-02
Related: ADR-12 (social relationships & privacy), ADR-04 (route versioning),
`docs/03-database-model.md`, `docs/04-api-map.md`

## 1. Context

Phase 8.1 built a one-to-one social graph: profiles, friend requests,
friendships, blocks. Every relationship there is between exactly two riders.

A cycling team is a different shape: an *n*-ary entity with its own identity
(handle, logo, category, privacy) and its own authority ladder (owner, admin,
member). Two teams questions follow immediately and neither exists in 8.1:

- Does a team change friendship? It must not.
- Does a friendship change a team? It must not.
- Does a block cascade into team membership? This ADR answers **no**, and the
  reasoning is in §6.

## 2. Decision

Four tables, additive to 8.1. No existing table is altered, so 8.1 rows and 8.2
rows cannot interfere.

### 2.1 `teams` is a public entity, not a friendship with extra columns

`teams` has its own handle (canonical lowercase, globally unique), name,
description, logo URL, category label, visibility, and status. It is
discoverable on its own terms, exactly as `social_profiles` is in 8.1.

Rejected alternative: modelling a team as a mutual-friendship row with a flag.
Teams and friendships have opposite permission models (a team has roles and an
admin can expel; a friendship has none and cannot be expelled), opposite
lifetimes, and opposite privacy defaults. One table would need nullable role
columns that are meaningless for friendships and an `is_team` discriminator that
every query would branch on.

`member_count` is denormalized on `teams` and maintained inside the same
transaction as every membership mutation, under an advisory lock on the team.
Without it, a list of twenty teams is twenty correlated `COUNT(*)` subqueries.

### 2.2 `teams.owner_user_id` and the OWNER membership are written together

Ownership is stored twice — a column on `teams` and a `role='owner'` row in
`team_memberships` — and that is deliberate:

- The column makes "is this my team?" an index-only lookup and keeps ownership
  meaningful even if membership rows are audited or repaired.
- The membership row makes role checks uniform: every authorization question is
  "what role does this user hold in this team?", with one code path.

Both are written in **one transaction** when the team is created, and a partial
unique index (`uq_team_memberships_single_owner` on `team_id WHERE role='owner'`)
makes a second owner row impossible at the storage layer. They cannot drift
because there is never a moment when one exists without the other.

Rejected alternative: ownership derived from a membership query alone. Then
"the owner of this team" is a join on every authorization check, and an
accidental delete of the owner row would silently orphan the team.

### 2.3 Join requests and invitations are separate tables

They have **opposite** permissions:

| | Who acts | Who decides |
|---|---|---|
| Join request | the applicant | a manager of the team |
| Invitation | a manager of the team | the invitee |

Merging them into one "pending association" table would force one awkward union
of two authorization models on every read and write, and the two have genuinely
different lifecycles.

Rejected: a single `pending_memberships` table with a `direction` column. It is
smaller by one table and much harder to reason about — every query has to know
whose queue it is reading.

### 2.4 Requests are deleted; invitations are retained

A **rejected or accepted join request is deleted**, exactly as 8.1 deletes a
rejected friend request. A rejected request is not a fact worth storing, and
keeping it would let a rejected applicant reappear in a history the team never
agreed to keep.

**Invitations are retained** in every terminal state (`accepted`, `declined`,
`revoked`). The recipient needs a durable inbox, and both sides benefit from a
record of what was offered and what happened to it.

An invitation does not permanently block a future one: the uniqueness index is
**partial**, covering only `status = 'pending'`. A team may re-invite someone who
previously declined. The same rider cannot, however, hold two pending
invitations for the same team.

### 2.5 Inviting collapses a redundant join request

If a rider has an outstanding join request and a manager invites them, the
request is deleted inside the invitation transaction. Otherwise the applicant
would see two competing paths to the same outcome. The invitation is strictly the
stronger offer (it needs no approval), so it wins.

## 3. Public vs private teams

| | Public | Private |
|---|---|---|
| Appears in search | yes | **excluded**, not redacted |
| `GET /teams/{id}` by a non-member | 200 | 404 |
| `POST /teams/{id}/join` | joins immediately | creates a join request |

Private teams are excluded from search rather than returned with blanked
fields. A redacted-but-present row would still confirm the team exists and still
leak its name.

The 404 for a private team is identical to the 404 for a nonexistent one. This
is not a nicety: a `403` would turn `GET /teams/{id}` into an oracle for probing
which team ids exist (ADR-12 §4).

Archived teams keep their members and their roster readable, but stop
appearing in search and accept no new requests or invitations.

## 4. Roles and what each may do

Authorization is read from the viewer's own membership row on every call. No
function in `team_service` accepts a role, an `actor_id`, or an `is_admin` from
a caller that could have taken it from a request body.

| Capability | Owner | Admin | Member | Non-member |
|---|:-:|:-:|:-:|:-:|
| View team + roster | ✓ | ✓ | ✓ | public only |
| Edit description / logo | ✓ | ✓ | — | — |
| Edit name / handle / visibility | ✓ | — | — | — |
| Archive team | ✓ | — | — | — |
| Invite | ✓ | ✓ | — | — |
| Accept / reject join requests | ✓ | ✓ | — | — |
| Change a member's role | ✓ | — | — | — |
| Remove a member | ✓ | ✓ | — | — |
| Leave the team | — | ✓ | ✓ | — |

Three notes:

- **The owner cannot leave.** Leaving is voluntary; an owner who wants out
  archives the team. The alternative — a team with no owner — has no coherent
  resolution, and owner transfer is explicitly out of scope for this phase.
- **An admin gets 403, a non-member gets 404, for an owner-only field.** An
  admin already knows the team exists, so 403 leaks nothing. A non-member does
  not, so 403 would be an existence oracle.
- **Ownership transfer is not implemented.** Attempting to promote anyone to
  owner returns `TEAM_OWNER_IMMUTABLE`. A partial unique index makes a second
  owner impossible, so transfer is a deliberate schema-visible operation for a
  later phase, not something to improvise here.

## 5. Teams and friendships are independent

```
A <--friendship--> B          friend_relationships   (Phase 8.1)
A --member------> Team X      team_memberships       (here)
B --member------> Team X      team_memberships       (here)
```

Nothing in `team_service` imports or touches `friend_relationships`. Enforced by
tests, not just by intent:

- Two friends join a team, one leaves → **they are still friends**.
- Inviting a rider does not make them a friend.
- Declining an invitation changes no friendship.
- `POST /teams/{id}/invitations` accepts any rider id the server can resolve; it
  is not restricted to friends at the API level, because the server has no
  business enforcing a social rule that would duplicate ADR-12's friendship
  model. The Flutter invite screen *offers* friends because that is the useful
  candidate set, but that is a UI affordance, not an authorization rule.

The only coupling between the two subsystems is a **refusal to start**: a block
prevents a new team association between the two riders (§6). It never modifies
existing state in either.

## 6. Blocks are non-cascading (load-bearing)

**A block refuses new association actions. It deletes nothing.**

| Event | Effect on friendship | Effect on team membership |
|---|---|---|
| A blocks B | friendship removed (8.1 wall semantics) | **unchanged** |
| B tries to join / request / be invited to A's team | — | refused (404 or 409) |
| B tries to invite A to a team | — | refused |
| A unblocks B | **nothing is recreated** | **nothing is recreated** |

The asymmetry with 8.1 is intentional and is not an inconsistency:

- ADR-12 §2.3 defines a block as a wall that destroys the friendship row. That
  behaviour is unchanged and still tested.
- ADR-13 adds a *different* rule for teams, because membership in a group is a
  weaker, more public act than a personal friendship. Silently expelling people
  from a team because of a private disagreement would broadcast the
  disagreement to every other member, which is exactly the outcome a block is
  supposed to prevent.

So: a friendship ends when blocked; a team membership survives, but the two
riders cannot form any *new* association while the block stands, and a member
who wants out can still leave voluntarily (a block must never trap somebody in a
group).

Unblocking restores nothing. A rider who left genuinely left; an unblock does
not undo that. A rider who was never removed is still a member — the block
simply stopped working for them, and now works again.

## 7. Concurrency

Two advisory transaction locks, matching the 8.1 pattern:

- `pg_advisory_xact_lock(hashtext(f"team:{team_id}"))` — every membership
  mutation (add, remove, leave, role change, archive). Without it, two admins
  removing two members both read `member_count=5` and both write 4. One lock per
  transaction means no lock ordering and no deadlock.
- `pg_advisory_xact_lock(hashtext(f"teampair:{team_id}:{user_id}"))` — join,
  request, invite, accept, and invitation responses for one pair, so a block
  landing mid-decision cannot produce a contradictory end state.

Partial unique indexes remain the final arbiter for inserts; the losers of a
race re-read and answer deterministically. Tested with real concurrent
connections: simultaneous joins, simultaneous accepts, concurrent leaves, and
an invitation racing a join request.

## 8. Privacy and location

- A member list is a **public social profile projection** plus a role. The
  service reads `social_profiles`, never `users`, so a roster cannot leak an
  email address or any other account-private column. Enforced by a test that
  greps every team payload for banned key fragments.
- **No team table, response, or screen contains coordinates, GPS, live
  location, or route position.** There is no field to render one. A standing
  notice on the team profile and settings screens states that membership is not
  location access. When location ships it will be an explicit, time-bounded,
  revocable grant table, never a team column (ADR-12 §3, §7).
- Search matches **name and handle only**, never member names, so it cannot be
  used to enumerate a roster.

## 9. Known consequences

- `member_count` is denormalized and therefore a consistency risk. It is
  maintained under the team lock in the same transaction as the membership
  change, and asserted by a concurrency test. Accepted.
- Join requests have no message history after resolution. Accepted (§2.4).
- There is no team-level block scope. A block here is always between two
  *riders*, never between a rider and a team. If team-scoped blocking is ever
  needed, it must be a separate construct: it would have to decide whether
  expelling someone is acceptable, and that is a product decision this ADR does
  not make.

## 10. Future integration (deferred)

- **Group rides** attach to a team via `group_rides(team_id, …)`. A team is the
  natural scope for a ride, and team roles are the natural permission source for
  creating one.
- **Team communication** (chat, announcements) must respect this ADR: a blocked
  pair must not reach each other through a team channel, and a member removed
  from a team must lose access to it.
- **Live location** in a group-ride context remains an explicit revocable grant.
  Being in a team is never an implicit grant (ADR-12 §3).
- **Owner transfer** needs its own design: a single atomic operation that
  demotes the old owner and promotes the new one under the team lock, with a
  deliberate decision about whether the old owner leaves or stays as an admin.