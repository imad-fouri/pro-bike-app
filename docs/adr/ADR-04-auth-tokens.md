# ADR-04 — Auth Token Strategy

Date: 2026-09-27 | Status: Accepted | Phase 2

## Access tokens: short-lived JWT (HS256, 15 min)
Claims: `sub` (user id), `jti`, `type=access`, `iat`, `exp`. Stateless
verification via SECRET_KEY. Never stored server-side; client keeps in memory
only. Logout does not need to revoke access tokens (15-min blast radius).

## Refresh tokens: opaque, rotating, multi-device
- 32 random bytes (`secrets.token_urlsafe`), SHA-256 hash stored in
  `refresh_sessions.refresh_hash`. Plaintext never persisted.
- Lifetime 30 days, sliding on rotation. Each login creates a new
  `family_id` (= session chain per device). Refresh consumes the old token:
  old row → `revoked_at` + `replaced_by=new_id`, new row same `family_id`.
- Reuse detection: presenting an already-rotated token revokes the whole
  family (compromise containment) and returns 401.
- Logout revokes the single session; "logout all" revokes all user sessions.

```text
User
 ├── Device A (family_a): sess_3 (active) <- sess_2 (rotated) <- sess_1
 ├── Device B (family_b): sess_1 (active)
 └── Device C (family_c): revoked (logout)
```

## Password reset / email verify
Opaque one-time tokens, SHA-256 stored, 60-min / 24-h expiry, single-use
(`used_at`), constant-time compare. Reset request always returns 200
(anti-enumeration).

## Rate limits (Redis, in-memory fallback in dev)
register 10/h, login 20/10min, refresh 60/min, password-reset 5/h — per IP.

## Why not JWT refresh
Opaque refresh = instant revocation + reuse detection; JWT refresh would need
a denylist that reintroduces server state without these benefits.
