# CycleCoach — Backup and Recovery Strategy

Phase 10, WS-J/P. Status: **DESIGNED. NOT OPERATIONAL.**

> **No backup has been taken, no restore has been performed, and no recovery time
> objective has been measured.** This document specifies the strategy and defines
> how each claim will be verified. It does not assert that backups work. See §7
> for exactly what is outstanding.

---

## 1. What is durable

The recovery question has exactly one real answer, and it is short:

| Store | Durable | Backup | Rationale |
|---|---|---|---|
| **PostgreSQL 16** — 33 tables | Yes | **Required** | The only system of record. Losing it loses accounts, rides, routes, training history, chat and social graphs. No reconstruction path exists. |
| **Redis 7** — live positions | No | **Excluded, deliberately** | See §3. |
| Application logs (stdout) | Depends on the host | **PENDING** | Not yet shipped to durable storage. Needed for incident response, not for data recovery. |
| GPX uploads | N/A | N/A | Never stored — parsed and discarded. |
| Avatars / bike images | N/A | N/A | No file or object storage exists. |

**One database to protect.** That is the single most important fact in this
document: there is no distributed state, no blob store, no external queue, and no
cache holding anything authoritative.

---

## 2. PostgreSQL backup policy

### 2.1 Method

Physical base backups (`pg_basebackup`) plus continuous WAL archiving, giving
point-in-time recovery:

- **Base backup** — daily, full, compressed with `pg_dump -Fc`-equivalent
  tooling or `pg_basebackup` in tar format.
- **WAL archiving** — continuous, shipped off-host as written.
- **PITR** — any moment to any point, bounded by WAL retention.

Logical dumps (`pg_dump`) are **not** sufficient for point-in-time recovery: they
capture an instant and nothing between. They remain useful for a
schema-only artifact and for seeding test databases.

### 2.2 Frequency and retention — PROPOSED, PENDING SIGN-OFF

| Tier | Proposed | Purpose |
|---|---|---|
| Base backup | Daily | Recovery point |
| WAL archive | Continuous | PITR |
| Retention — daily | **30 days** PENDING | Recent regressions |
| Retention — monthly | **12 months** PENDING | Year-over-year comparison, abuse investigation |
| Off-site copy | **≥ 1, PENDING** | A backup in the same failure domain is not a backup |

**These numbers are proposals, not decisions.** They trade cost against the
window of data loss a rider would experience, and that trade is a business
decision. They require sign-off before they are configured.

### 2.3 RPO and RTO — TARGETS, NOT MEASUREMENTS

| Objective | Target | Status |
|---|---|---|
| **RPO** (max data loss) | ≤ 15 minutes | **NOT MEASURED** — depends on WAL shipping interval |
| **RTO** (max downtime) | ≤ 4 hours | **NOT MEASURED** — depends on restore procedure and infrastructure |

Both are targets. Neither has been demonstrated, and neither may be reported as
met until §7 is complete.

---

## 3. Why Redis is excluded from backups

Redis holds **consented live rider positions** under key `gr9:share:{ride_id}`,
TTL 300 s, with a 60 s staleness cutoff.

Backing it up would defeat the control it exists to provide:

- The TTL **is** the deletion guarantee. A backup copy would preserve a position
  long after the rider stopped sharing and after the key expired.
- Restoring Redis would **resurrect expired positions** — showing riders a
  location from hours or days ago as if it were live. The staleness filter would
  hide them from display, but the data would exist, which is the actual harm.
- There is no recovery value: if Redis is lost, the correct state is "nobody is
  sharing", and riders re-publish within seconds.

Redis is therefore treated as **disposable**, configured with no persistence
requirement (`appendonly no`, no RDB snapshot retention to preserve). If
persistence is enabled for operational reasons, its backups must be handled as
HIGHLY SENSITIVE with the same access controls as PostgreSQL, and their retention
bounded to hours rather than months.

**A rider's live location is not recoverable data.** It is transient state with an
expiry, and treating it as an asset to restore is how a privacy control becomes a
liability.

---

## 4. Restore procedure

Written to be executed by an operator who has not read the codebase. Every step
is verifiable, and the verification steps are not optional — a restore that is not
verified is an assumption.

### 4.1 Preconditions

- A known-good base backup plus the WAL segment covering the target time
- A **restored copy**, never the production instance
- Credentials for the restored instance distinct from production
- `docs/privacy-data.md` reviewed: a restored database contains every rider's GPS
  history, biometrics and chat

### 4.2 Steps

1. Provision an isolated PostgreSQL 16 instance. **Do not** point it at the
   production connection string.
2. Restore the base backup with `pg_basebackup` / `pg_combinebackup`.
3. Replay WAL to the target timestamp (`recovery_target_time`).
4. Confirm recovery reached the target: `SELECT pg_last_wal_replay_lsn()` and a
   row-count comparison against the expected snapshot.
5. Verify the Alembic head matches the code being deployed:
   `alembic current` must equal `alembic heads` — a **single** head. Multiple heads
   means the code and schema disagree and the restore is not trustworthy.
6. Run the test suite against the restored instance. A restore that cannot pass
   its own tests is not a restore.
7. Only then, and only through a reviewed change, point traffic at it.

### 4.3 Post-restore verification checklist

| Check | Command / method | Pass criterion |
|---|---|---|
| Schema head is single | `alembic heads` | Exactly one line |
| Code matches schema | `alembic current` vs `alembic heads` | Equal |
| Row counts plausible | `SELECT count(*)` per table | Non-zero for core tables; orders of magnitude match production |
| Test suite passes | `pytest -q` | All green |
| Auth works | Login flow against the restore | Token issued, refresh rotation functions |
| **No stale positions** | Inspect Redis for `gr9:share:*` | **Empty.** A restored database must not be paired with restored live locations |
| Migration round-trip | `alembic downgrade -1 && alembic upgrade head` | Succeeds |

That Redis row is called out because restoring Redis alongside PostgreSQL is the
one mistake that would silently reintroduce expired rider locations. The correct
post-restore state is: full historical data, no live positions.

---

## 5. Environments and their backup obligations

| Environment | PostgreSQL | Redis | Notes |
|---|---|---|---|
| Local development | None | None | Disposable; seeded from schema |
| CI | None | None | Ephemeral per run; the Phase 9 Redis fixture resets the cached client between tests for this reason |
| Staging | Proposed daily | No | Restore testing happens here |
| Production | Daily + WAL | **No** | Per §2 |

---

## 6. Failure modes

| Failure | Effect | Response | Data loss |
|---|---|---|---|
| Redis unavailable | Live map degraded to 503; rides, routes, chat, training unaffected | Restore Redis; riders re-publish within seconds | None |
| Redis data lost | Positions gone | **Nothing to restore — correct behaviour** | None |
| PostgreSQL unavailable | API `/ready` returns 503 and stops receiving traffic; `/health` stays 200 so orchestrators do not crash-loop healthy processes | Restore per §4 | Up to RPO |
| Single node lost | Full outage | Rebuild from backup, or fail over | Up to RPO |
| Region lost | Full outage | Restore off-site copy | Up to RPO |
| WAL archive lost | PITR degraded to the last base backup | Re-archive immediately; investigate | Up to 24 h |
| Log storage lost | Incident forensics degraded; **no rider data lost** | Reconfigure shipping | None |

The `/health` vs `/ready` split is load-bearing here: a database outage must stop
*traffic* without restarting every process. That separation was hardened in
Phase 10 WS-N.

---

## 7. Verification status — what must happen before this is operational

| Item | Status | Owner action |
|---|---|---|
| Automated base backups configured | **NOT DONE** | Configure per §2 |
| WAL archiving to off-site storage | **NOT DONE** | Configure; confirm the copy is in a different failure domain |
| **A full restore actually performed** | **NOT DONE** | Execute §4 in staging, timed |
| Restore time measured | **NOT DONE** | Record the real RTO and replace the §2.3 target |
| Data-loss window measured | **NOT DONE** | Record the real RPO |
| Post-restore checklist automated | **NOT DONE** | Script §4.3 so it runs on every restore |
| Retention numbers approved | **PENDING DECISION** | Business sign-off |
| Log shipping to durable storage | **PENDING DECISION** | Infrastructure |
| Alerting on backup failure | **NOT DONE** | A backup that fails silently is worse than none |

**Until at least one full restore has been performed and timed, this document is a
design, not a capability.** The difference matters: a plan that has never been
executed is a hypothesis, and reporting it as a working backup would be the exact
kind of unverified claim this phase forbids.

---

## 8. Related

- `docs/privacy-data.md` — what the data is, who can reach it, and the retention
  decisions still pending
- `docs/SECURITY_AUDIT.md` — credential, logging and configuration findings
