"""Pub/Sub push authentication for Google RTDN (Phase 10).

Google Real-time Developer Notifications reach us as Pub/Sub push deliveries.
The only authentication Pub/Sub offers for the push is an OIDC identity token
in the ``Authorization: Bearer`` header: a JWT issued to the subscriber's
service account and signed with a key from Google's published JWKS. This module
verifies that token BEFORE the webhook layer decodes the envelope.

Fail closed: any problem — malformed token, unknown kid, wrong algorithm, bad
signature, wrong audience, wrong issuer, expired, or an unreachable/empty JWKS —
surfaces as the single opaque :class:`PushTokenError`. The caller maps it to a
403 and never learns which check failed, because a rejected push should give its
sender no information.

No network request is made per webhook: the signing keys are fetched once and
cached for an hour (the route-level verifier is a module singleton). Tests
inject a fixed JWKS so the real ``cryptography``/PyJWT verification machinery
runs with no external call.
"""

import base64
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

#: Timeout for a first-time JWKS fetch. Verification is the auth boundary of
#: the webhook, so a fetch that hangs must fail the request, never hang it.
_JWKS_TIMEOUT_S = 10.0

#: Plausible RSA modulus bounds, in bits. Google's published keys are 2048-bit;
#: the floor rejects a degenerate key that base64 leniency let through, and the
#: ceiling rejects a pathological modulus whose very construction is a CPU
#: denial of service. Neither bound can trip on a real Google JWKS.
_MIN_RSA_BITS = 1024
_MAX_RSA_BITS = 16384


class PushTokenError(Exception):
    """A push token failed verification.

    The message carries no reason: the verifier distinguishes only "ok" from
    "not ok", and the HTTP layer turns every failure into the same 403.
    """


class PushTokenVerifier(Protocol):
    """Contract any push-token verifier must satisfy."""

    async def verify(self, token: str) -> None:
        """Verify one OIDC push token; raise :class:`PushTokenError` otherwise."""
        ...


def _b64u_to_int(value: str) -> int:
    if not value:
        raise ValueError("missing base64url component")
    padded = value + "=" * (-len(value) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(padded), "big")


def _public_key_pem(jwk: Mapping[str, object]) -> bytes:
    """Turn one RSA JWK into the SubjectPublicKeyInfo PEM PyJWT accepts.

    The cryptography ``RSAPublicNumbers`` path builds the key in memory from
    ``n``/``e``, so no certificate or DER encoding knowledge is taken from the
    wire — exactly the two numbers RSA needs.

    The modulus is bounded on both sides because base64 decoding is lenient:
    a garbage ``n`` (a base64 string that merely contains invalid characters)
    still decodes to *some* integer, which would otherwise become a key. A
    degenerate or absurd modulus must never verify anything, so it is refused
    here rather than by a later signature check that would fail only by luck.
    """
    if jwk.get("kty") != "RSA":
        raise ValueError("not an RSA key")
    modulus = _b64u_to_int(str(jwk.get("n") or ""))
    if not _MIN_RSA_BITS <= modulus.bit_length() <= _MAX_RSA_BITS:
        raise ValueError("implausible RSA modulus")
    numbers = rsa.RSAPublicNumbers(_b64u_to_int(str(jwk.get("e") or "")), modulus)
    public_key = numbers.public_key()
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@dataclass
class GoogleOidcPushVerifier:
    """Verify Google Pub/Sub OIDC push tokens against Google's published JWKS.

    ``audience`` is the expected ``aud`` claim — the Pub/Sub subscription URL
    (``projects/<project>/subscriptions/<subscription>``). ``issuer`` defaults
    to accounts.google.com. ``jwks_uri`` points at the public signing keys.

    ``http`` is an injectable httpx client and ``jwks`` an injectable key set
    for tests; production constructs neither and fetches from Google with a
    one-hour cache.
    """

    audience: str
    issuer: str = "https://accounts.google.com"
    jwks_uri: str = "https://www.googleapis.com/oauth2/v3/certs"
    http: httpx.AsyncClient | None = None
    jwks: Sequence[Mapping[str, object]] | None = None
    cache_seconds: int = 3600

    _keys: dict[str, Mapping[str, object]] | None = field(init=False, repr=False, default=None)
    _fetched_at: float = field(init=False, repr=False, default=0.0)

    async def verify(self, token: str) -> None:
        try:
            unverified = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise PushTokenError() from exc
        if not isinstance(unverified, dict) or unverified.get("alg") != "RS256":
            raise PushTokenError()
        kid = unverified.get("kid")
        if not isinstance(kid, str) or not kid:
            raise PushTokenError()
        jwks = await self._jwks_keys()
        jwk = jwks.get(kid)
        if jwk is None:
            # An unknown key id is the attacker's cheapest probe and the most
            # likely sign of a token we did not issue: refuse without detail.
            raise PushTokenError()
        try:
            key_pem = _public_key_pem(jwk)
        except (TypeError, ValueError):
            # A malformed key (bad base64, non-RSA, absurd numbers) can verify
            # nothing. Refuse like every other failure: silently, with a 403.
            raise PushTokenError() from None
        try:
            claims = jwt.decode(
                token,
                key=key_pem,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise PushTokenError() from exc
        if not isinstance(claims, dict):
            raise PushTokenError()

    async def _jwks_keys(self) -> dict[str, Mapping[str, object]]:
        if self.jwks is not None:
            return {
                str(key["kid"]): key
                for key in self.jwks
                if isinstance(key, Mapping) and isinstance(key.get("kid"), str)
            }
        now = time.time()
        if self._keys is not None and now - self._fetched_at < self.cache_seconds:
            return self._keys
        keys = await self._fetch_jwks_keys()
        self._keys = keys
        self._fetched_at = now
        return keys

    async def _fetch_jwks_keys(self) -> dict[str, Mapping[str, object]]:
        try:
            if self.http is not None:
                response = await self.http.get(self.jwks_uri)
            else:
                async with httpx.AsyncClient(timeout=_JWKS_TIMEOUT_S) as client:
                    response = await client.get(self.jwks_uri)
        except httpx.HTTPError as exc:
            # Unable to fetch the signing keys means unable to verify, which
            # means the push is NOT accepted. Failing the auth boundary on a
            # key outage is the safe direction; Pub/Sub retries the delivery.
            raise PushTokenError() from exc
        if response.status_code != 200:
            raise PushTokenError()
        try:
            body = response.json()
        except ValueError as exc:
            raise PushTokenError() from exc
        if not isinstance(body, dict) or not isinstance(body.get("keys"), list):
            raise PushTokenError()
        keys: dict[str, Mapping[str, object]] = {
            str(key["kid"]): key
            for key in body["keys"]
            if isinstance(key, dict) and isinstance(key.get("kid"), str)
        }
        if not keys:
            # An empty key set can verify nothing. Refuse rather than accept a
            # token for which no key exists.
            raise PushTokenError()
        return keys
