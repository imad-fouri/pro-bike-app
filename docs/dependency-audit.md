# CycleCoach — Dependency Audit

Phase 10, WS-O. Tool: `pip-audit` 2.10.1. Date of run: 2026-10-06.

> **Headline, stated precisely:**
> **No known findings reported by pip-audit for the declared dependency set.**
>
> Not "zero vulnerabilities" — that is a claim about the world that no tool can
> make. What was actually observed is that pip-audit's advisory database, on the
> day it ran, contained no entry matching any package the application declares.
> A newly published advisory would change this sentence.

---

## 1. What was audited

Two different questions, answered with two different commands, because they were
being conflated:

| Scope | Command | Question it answers |
|---|---|---|
| **Declared set** | `pip-audit -r requirements-declared.txt` | Does anything the application depends on have a known advisory? |
| **Installed environment** | `pip-audit --local` | Does anything present on this machine have one? |

The installed environment is a **superset** of the declared set: it also contains
build tooling, test tooling, and media libraries left by unrelated tooling. Its
findings are real, but they are not findings about this application unless they
are reachable from a declared dependency — and that reachability is the thing
that has to be checked rather than assumed.

### The declared set

Fifteen direct dependencies, from `backend/pyproject.toml`:

```
fastapi>=0.115            uvicorn[standard]>=0.30    pydantic>=2.7
pydantic-settings>=2.3    sqlalchemy[asyncio]>=2.0   asyncpg>=0.29
alembic>=1.13             psycopg[binary]>=3.1       redis>=5.0
arq>=0.26                 httpx>=0.27                pyjwt>=2.8
argon2-cffi>=23.1         email-validator>=2.0       python-multipart>=0.0.9
```

> **No lockfile exists.** Every direct dependency is a `>=` range, so the resolved
> versions differ between machines and between CI runs. That means a
> "vulnerability introduced by a new release" cannot be detected by re-running
> this audit — only by re-running it against whatever was resolved. Committing a
> lockfile is the single highest-value follow-up to this document, and it is not
> done here because pinning the tree is a change to how every developer and every
> deploy resolves packages, which deserves its own decision. See §5.

---

## 2. Declared set — result

```
$ python -m pip_audit -r requirements-declared.txt --progress-spinner off
No known vulnerabilities found
(exit 0)
```

Resolution pulled the full transitive tree, so this is not a shallow check of the
15 direct names.

**No known findings reported by pip-audit for the declared dependency set.**

---

## 3. Installed environment — result and classification

98 packages scanned, 47 findings across 2 packages. Both are classified
**out of scope**, with the evidence stated rather than asserted.

### 3.1 `pillow` 11.3.0 — 35 findings

| Question | Answer | How it was checked |
|---|---|---|
| Is `pillow` a declared dependency? | **No** | Absent from `pyproject.toml` `[project].dependencies` |
| Does the application import it? | **No** | Regex sweep of `backend/app/**/*.py` for `import PIL` / `from PIL`: **no matches** |
| Why is it installed? | Unrelated local tooling | `pip show pillow` → `Required-by: ImageIO, moviepy` |
| Why is `ImageIO` installed? | Unrelated local tooling | `pip show moviepy` → `Requires: imageio<3.0,>=2.5`; `imageio` → `Requires: pillow>=8.3.2` |
| Is `moviepy` declared or reachable? | **No** | Not in the declared set; nothing in `app/` references it |

The chain is `moviepy → imageio → pillow`, and `moviepy` is not in this project's
dependency graph at all. It is present in this Python installation because
unrelated work on this machine installed it.

**Classification: environment contamination, not an application dependency.** The
35 advisories are real advisories against `pillow`; none of them describes a code
path in CycleCoach.

### 3.2 `pip` 25.0.1 — 12 findings

| Question | Answer | How it was checked |
|---|---|---|
| Is `pip` a declared dependency? | **No** | Absent from `pyproject.toml` |
| Does the application import it? | **No** | No import in `app/` |
| Why is it installed? | It is the installer | `pip show pip` → `Required-by: pip_api` (its own vendored API shim) |

`pip` is the tool that installed everything else. Auditing it audits the
instrument, not the application.

**Classification: tooling.** Upgrading it is a local-environment action and
explicitly out of scope for a change that must not touch unrelated tooling.

### 3.3 What this means for the application

Nothing in the application's declared dependency graph is known-vulnerable as of
the run date. The remediation paths for both findings exist and are trivial
(`pip install -U pillow pip`) but they belong to a machine cleanup, not to this
repository, and doing them here would change packages the project does not own.

**The honest caveat:** if a future change adds a media or image dependency,
`pillow` *would* enter the declared graph, at which point 35 findings become real
and this document is wrong. Re-run the audit when that happens.

---

## 4. Container, CI and base images

`pip-audit` covers Python packages. It does **not** cover the OS packages, OpenSSL
or Python runtime inside the image those packages are installed into, so those
surfaces were inspected separately. The result is a mix of one good finding, four
real gaps, and no attempt to dress any of them up.

### 4.1 `backend/Dockerfile`

```
FROM python:3.12-slim
WORKDIR /code
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
```

| Check | Result | Why it matters |
|---|---|---|
| Base image tagged | Yes — `python:3.12-slim` | A *floating* tag. The next build silently gets different OS packages. |
| Base image **digest** pinned | **No** | A tag can be re-pushed. A digest cannot. Reproducibility is not achieved today. |
| Runs as non-root | **No `USER` directive** | The container runs as **root**. A container escape starts as root. |
| `HEALTHCHECK` | **Missing** | The image cannot report its own health; an orchestrator must probe out of band. |
| Multi-stage build | **No** | Build tooling and any compiler left behind by `pip install -e .` ship in the runtime image. |
| `pip install --no-cache-dir` | Yes | Good — no package cache in the image layer. |
| Dependency pinning at install | **No** — installs from `pyproject.toml` ranges | The image resolves `>=` ranges at build time, so **two builds of the same commit produce different dependency trees.** |
| Requirements/lockfile used | **No** | Direct consequence of the row above. |

The `pip install -e .` at line 4 is the sharpest problem: `-e` installs the
package in *editable* mode, which is a development convenience that writes a path
reference into site-packages and is meaningless in an image that will not have the
source mounted at the same path. This Dockerfile is a development-grade file
that would not survive contact with a production deployment, and it is recorded
here as **an open gap, not a pass**.

### 4.2 `docker-compose.yml`

| Service | Pinning | Note |
|---|---|---|
| `postgres:16` | Tag only, no digest | Minor-version upgrades arrive on a pull |
| `redis:7` | Tag only, no digest | Same |
| `cyclecoach-api` | Not in compose | Built separately; compose covers the dependencies only |

`POSTGRES_PASSWORD: cyclecoach2026` is a **hard-coded credential in version
control**. It is a local development value and the service is not published in
this file, so it is not an exposed secret — but it is exactly the pattern that
becomes an exposed secret when someone copies the file to a deployment and
changes one line. WS-B made the *application* fail closed on missing secrets;
this compose file is the remaining hard-coded value in the repository and is
listed as a follow-up rather than fixed here, because changing the local database
password invalidates every developer's existing volume.

### 4.3 CI workflow pinning

| Action | Reference | Pinned by SHA |
|---|---|---|
| `actions/checkout` | `@v7.0.1` | **No** |
| `actions/setup-python` | `@v7.0.0` | **No** |
| `subosito/flutter-action` | `@v2.23.0` | **No** |
| Python | `3.12` | Minor-version range, not `.12.x` |
| Flutter | not pinned in-workflow | `flutter-action` default |

Version tags on actions are **mutable**: the owner of `actions/checkout` can move
the `v7.0.1` tag to different code. Pinning to a full commit SHA is the standard
supply-chain mitigation and is not done. This is the highest-value, lowest-effort
item in §5.

### 4.4 Not audited

| Surface | Status |
|---|---|
| OS package CVEs inside `python:3.12-slim` | **Unknown** — needs an image scan (Trivy/Grype) against a built image, which requires Docker to be running |
| Advisory database coverage | pip-audit uses the PyPI/OSV database; it does not report packages it has no data for |
| License compatibility | Not audited |
| Supply-chain provenance (SLSA, sigs) | Not audited |

---

## 5. Follow-ups, not done here

Ordered by value per unit of effort.

| # | Follow-up | Effort | Why it is not in this workstream |
|---|---|---|---|
| 1 | **Pin CI actions to commit SHAs** | Low | Mutating a workflow changes what runs on every future push; it deserves its own reviewed change |
| 2 | **Commit a lockfile** (`pip-compile` / `uv lock`) and install from it | Low–Med | A `>=`-only tree means the audited artifact is not reproducible, and the Docker image differs build to build |
| 3 | **Harden `backend/Dockerfile`**: digest pin, `USER`, multi-stage, drop `-e` | Med | Rewriting the image changes how the service is built and deployed — a deployment decision, not an observability one |
| 4 | **Scan the built image** (Trivy/Grype) in CI | Med | Needs the Dockerfile fixed first (§4.1) or the scan reports on the wrong thing |
| 5 | Move `POSTGRES_PASSWORD` out of `docker-compose.yml` | Low | Changing it invalidates every developer's existing local volume |
| 6 | Schedule the audit | Low | This is a one-time manual run with no automation, so it will go stale silently |
| 7 | Re-run on any dependency change | — | The declared graph is small enough that this is cheap |

Items 1–3 are all supply-chain hardening of the same family: today, a compromised
`actions/checkout` tag, a re-pushed `python:3.12-slim` tag, or a newly published
`>=`-range release would each enter the build without any signal. None of them is
urgent today. All three are cheap relative to the codebase.

---

## 6. Method and reproducibility

```
pip-audit version : 2.10.1
advisory source   : PyPI Advisory Database (OSV-backed), as resolved by pip-audit
declared set      : backend/pyproject.toml [project].dependencies (15 entries)
declared audit    : pip-audit -r requirements-declared.txt --progress-spinner off
environment audit : pip-audit --local --format json
reachability      : importlib.metadata.requires / .version for moviepy, imageio,
                    pillow, pip; regex sweep of backend/app/**/*.py for imports
container audit   : static read of backend/Dockerfile, docker-compose.yml and
                    .github/workflows/ci.yml (no image build or image scan)
```

The declared-set audit needs a requirements file, so one is generated from
`pyproject.toml` at audit time. It is a **transient artifact and is not committed** —
a checked-in file that shadows `pyproject.toml` is a second place for the
dependency list to drift.

`pip-audit` was installed into the local Python environment to perform this run.
It is **not** a project dependency and was **not** added to `pyproject.toml` —
adding an audit tool to a runtime manifest to satisfy an audit is not a trade
this project makes.

Findings are a snapshot of a moment. The date above is the date the database was
queried.