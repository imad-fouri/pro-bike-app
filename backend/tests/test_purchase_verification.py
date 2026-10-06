"""Phase 10 WS-PV purchase verification tests.

Two layers, matching the architecture: service tests drive
`process_purchase` directly against real PostgreSQL (the trust boundary is
a function, and functions deserve direct tests), while route tests prove
the HTTP contract — authentication, validation, status codes, and the
absence of every credential from every response.

The fake provider below is test-only infrastructure. It never contacts a
network, never claims to be Apple or Google, and its signatures are the
literal string "valid:<id>". Nothing passing here transfers to a real
store; what transfers is the shape of the application's handling.
"""

import asyncio
import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import redact
from app.core.metrics import metrics
from app.models.subscription import (
    Feature,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.purchase_verification import (
    VerifyKind,
    process_purchase,
    register_provider,
    reset_providers,
)
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    CYCLECOACH_PRO_YEARLY,
)
from app.services.subscription_providers import (
    VerificationEnvironment,
    VerifiedPurchase,
)
from app.services.subscription_service import (
    EntitlementError,
    has_feature,
    resolve_state,
)

AUTH = "/api/v1/auth"
VERIFY = "/api/v1/store/purchases/verify"
RESTORE = "/api/v1/store/purchases/restore"
COACH_SUMMARY = "/api/v1/coach/weekly-summary"


@pytest.fixture(autouse=True)
def _clean_state():
    reset_providers()
    metrics.reset()
    yield
    reset_providers()
    metrics.reset()


class FakeVerifyProvider:
    """Deterministic test double for one billing origin.

    Behavior is configured per test through `result` (returned) and `error`
    (raised); `delay_s` simulates a slow store. The double performs no
    binding enforcement of its own — the service-side checks are the trust
    boundary under test, and a fake that did the service's job would prove
    nothing about the service.
    """

    def __init__(self, name: SubscriptionProvider = SubscriptionProvider.APP_STORE):
        self._name = name
        # Records who was verified, never the credential: even test doubles
        # must not normalize retaining purchase tokens.
        self.calls: list[uuid.UUID] = []
        self.result: VerifiedPurchase | None = None
        self.error: Exception | None = None
        self.delay_s: float = 0

    @property
    def name(self) -> SubscriptionProvider:
        return self._name

    async def verify_purchase(
        self, *, purchase_token: str, expected_user_id: uuid.UUID
    ) -> VerifiedPurchase:
        self.calls.append(expected_user_id)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        assert self.result is not None, "fake has neither result nor error"
        return self.result

    async def get_subscription(self, *, provider_subscription_id: str):
        raise NotImplementedError("not exercised by verification tests")

    async def process_event(self, *, payload, signature):
        raise NotImplementedError("not exercised by verification tests")


def _verified(
    user_id: uuid.UUID,
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    product_id: str = CYCLECOACH_PRO_MONTHLY,
    plan: Plan = Plan.PRO,
    environment: VerificationEnvironment = VerificationEnvironment.PRODUCTION,
    occurred: datetime | None = None,
    sub_id: str | None = None,
) -> VerifiedPurchase:
    now = datetime.now(UTC)
    moment = occurred or now
    return VerifiedPurchase(
        provider=SubscriptionProvider.APP_STORE,
        provider_subscription_id=sub_id or f"app-store-{uuid.uuid4().hex}",
        user_id=user_id,
        plan=plan,
        status=status,
        current_period_start=moment - timedelta(days=1),
        current_period_end=moment + timedelta(days=30),
        occurred_at=moment,
        provider_event_id=f"verify-{uuid.uuid4().hex}",
        product_id=product_id,
        environment=environment,
    )


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"pv-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag}",
    }
    registered = await client.post(f"{AUTH}/register", json=payload)
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        f"{AUTH}/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert logged_in.status_code == 200, logged_in.text
    headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    me = await client.get(f"{AUTH}/me", headers=headers)
    assert me.status_code == 200, me.text
    return {"headers": headers, "user_id": uuid.UUID(me.json()["user"]["id"])}


async def _subscription_count(db_session_factory) -> int:
    async with db_session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Subscription))


def _verification_counts() -> dict[tuple[str, str], float]:
    return {
        (entry["labels"]["outcome"], entry["labels"]["provider"]): entry["value"]
        for entry in metrics.snapshot().get("purchase_verifications_total", [])
    }


# ---------------------------------------------------------------------------
# Route contract
# ---------------------------------------------------------------------------


async def test_verify_requires_authentication(client):
    res = await client.post(
        VERIFY,
        json={"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "purchase_token": "t"},
    )
    assert res.status_code == 401, res.text


async def test_restore_requires_authentication(client):
    res = await client.post(
        RESTORE,
        json={"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "purchase_token": "t"},
    )
    assert res.status_code == 401, res.text


async def test_unknown_provider_is_rejected_before_any_provider_runs(client):
    account = await _account(client, "unknown-provider")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "apple_pay_later",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "whatever",
        },
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "UNKNOWN_PROVIDER"


async def test_manual_is_not_a_purchase_provider(client):
    account = await _account(client, "manual-provider")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "manual",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "whatever",
        },
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "UNKNOWN_PROVIDER"


async def test_unknown_product_is_rejected(client, db_session_factory):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "unknown-product")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": "cyclecoach_pro_lifetime",
            "purchase_token": "whatever",
        },
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "UNKNOWN_PRODUCT"
    # Rejected before verification: the provider heard nothing, the database
    # learned nothing.
    assert provider.calls == []
    assert await _subscription_count(db_session_factory) == 0


async def test_no_registered_provider_means_honest_unavailability(client):
    account = await _account(client, "no-provider")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "whatever",
        },
    )
    assert res.status_code == 503, res.text
    assert res.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


async def test_client_is_pro_claims_are_rejected_not_honored(client, db_session_factory):
    account = await _account(client, "fake-pro")
    for body in (
        {"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "is_pro": True},
        {
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "x",
            "plan": "pro",
        },
    ):
        res = await client.post(VERIFY, headers=account["headers"], json=body)
        # Missing token → 422 validation; extra is_pro/plan → 422 forbid.
        # Either way the claim is refused, never applied.
        assert res.status_code == 422, f"{body} -> {res.text}"
    assert await _subscription_count(db_session_factory) == 0


async def test_valid_purchase_grants_pro_end_to_end(client, db_session_factory):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "valid")
    provider.result = _verified(account["user_id"])

    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "opaque-store-token",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"] == Plan.PRO.value
    assert {row["feature"] for row in body["entitlements"]} == {f.value for f in Feature}
    assert all(row["effective"] for row in body["entitlements"])
    # The credential went in and never came back out.
    assert "opaque-store-token" not in res.text

    # Server authority, end to end: a premium feature now answers, with no
    # client state involved in the decision.
    coach = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert coach.status_code == 200, coach.text


async def test_duplicate_verify_requests_converge(client, db_session_factory):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "duplicate-request")
    moment = datetime.now(UTC)
    provider.result = _verified(account["user_id"], occurred=moment)
    body = {
        "provider": "app_store",
        "product_id": CYCLECOACH_PRO_MONTHLY,
        "purchase_token": "same-token-twice",
    }
    first = await client.post(VERIFY, headers=account["headers"], json=body)
    second = await client.post(VERIFY, headers=account["headers"], json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["plan"] == second.json()["plan"] == Plan.PRO.value
    assert await _subscription_count(db_session_factory) == 1


async def test_restore_reconciles_without_blind_grants(client, db_session_factory):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "restore")
    provider.result = _verified(account["user_id"])
    restored = await client.post(
        RESTORE,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "restore-token",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["plan"] == Plan.PRO.value

    pristine = await _account(client, "restore-pristine")
    provider.result = _verified(pristine["user_id"], status=SubscriptionStatus.EXPIRED)
    nothing = await client.post(
        RESTORE,
        headers=pristine["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "nothing-to-restore",
        },
    )
    assert nothing.status_code == 200, nothing.text
    assert nothing.json()["plan"] == Plan.FREE.value


# ---------------------------------------------------------------------------
# Service pipeline
# ---------------------------------------------------------------------------


async def test_expired_verification_writes_terminal_state(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "expired")
    moment = datetime.now(UTC)
    provider.result = _verified(
        account["user_id"], status=SubscriptionStatus.EXPIRED, occurred=moment
    )
    async with db_session_factory() as db:
        state = await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="expired-token",
            user_id=account["user_id"],
        )
    assert state.plan is Plan.FREE
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.AI_COACH) is False
    assert await _subscription_count(db_session_factory) == 1


async def test_revoked_verification_revokes(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "revoked")
    sub_id = f"app-store-{uuid.uuid4().hex}"
    provider.result = _verified(account["user_id"], sub_id=sub_id)
    async with db_session_factory() as db:
        await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="first",
            user_id=account["user_id"],
        )
    provider.result = _verified(
        account["user_id"], status=SubscriptionStatus.REVOKED, sub_id=sub_id
    )
    async with db_session_factory() as db:
        state = await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="revoke",
            user_id=account["user_id"],
        )
    assert state.plan is Plan.FREE


async def test_out_of_order_verification_does_not_regress(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "out-of-order")
    now = datetime.now(UTC)
    sub_id = f"app-store-{uuid.uuid4().hex}"
    provider.result = _verified(account["user_id"], occurred=now, sub_id=sub_id)
    async with db_session_factory() as db:
        await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="newer",
            user_id=account["user_id"],
        )
    provider.result = _verified(
        account["user_id"],
        status=SubscriptionStatus.CANCELED,
        occurred=now - timedelta(hours=1),
        sub_id=sub_id,
    )
    async with db_session_factory() as db:
        state = await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="older",
            user_id=account["user_id"],
        )
    assert state.plan is Plan.PRO


async def test_cross_user_purchase_is_rejected(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    owner = await _account(client, "owner")
    thief = await _account(client, "thief")
    provider.result = _verified(owner["user_id"])
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="owners-token",
                user_id=thief["user_id"],
            )
    assert excinfo.value.code == "CROSS_USER_PURCHASE"
    async with db_session_factory() as db:
        assert await has_feature(db, thief["user_id"], Feature.AI_COACH) is False
        assert await has_feature(db, owner["user_id"], Feature.AI_COACH) is False


async def test_product_mismatch_is_rejected(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "mismatch")
    provider.result = _verified(account["user_id"], product_id=CYCLECOACH_PRO_YEARLY)
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="yearly-token",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "PRODUCT_MISMATCH"
    assert await _subscription_count(db_session_factory) == 0


async def test_plan_mismatch_is_rejected(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "plan-mismatch")
    provider.result = _verified(account["user_id"], plan=Plan.FREE)
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="free-plan-token",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "PRODUCT_PLAN_MISMATCH"
    assert await _subscription_count(db_session_factory) == 0


async def test_sandbox_purchase_rejected_on_production(db_session_factory, client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "sandbox-prod")
    provider.result = _verified(account["user_id"], environment=VerificationEnvironment.SANDBOX)
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="sandbox-token",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "ENVIRONMENT_MISMATCH"
    assert await _subscription_count(db_session_factory) == 0


async def test_sandbox_purchase_allowed_off_production(db_session_factory, client):
    assert settings.ENVIRONMENT != "production"
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "sandbox-dev")
    provider.result = _verified(account["user_id"], environment=VerificationEnvironment.SANDBOX)
    async with db_session_factory() as db:
        state = await process_purchase(
            db,
            kind=VerifyKind.PURCHASE,
            provider_name="app_store",
            product_id=CYCLECOACH_PRO_MONTHLY,
            purchase_token="sandbox-token",
            user_id=account["user_id"],
        )
    assert state.plan is Plan.PRO


async def test_unknown_environment_is_rejected(db_session_factory, client):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "unknown-env")
    provider.result = _verified(account["user_id"], environment=VerificationEnvironment.UNKNOWN)
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="envless-token",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "ENVIRONMENT_UNKNOWN"
    assert await _subscription_count(db_session_factory) == 0


async def test_malformed_provider_response_grants_nothing(db_session_factory, client):
    provider = FakeVerifyProvider()
    provider.error = ValueError("not json, not anything")
    register_provider(provider)
    account = await _account(client, "malformed")
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="garbage",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "PROVIDER_ERROR"
    assert "not json" not in str(excinfo.value)
    assert await _subscription_count(db_session_factory) == 0


async def test_provider_timeout_grants_nothing(db_session_factory, client, monkeypatch):
    import app.services.purchase_verification as pv

    monkeypatch.setattr(pv, "PURCHASE_VERIFY_TIMEOUT_S", 0.05)
    provider = FakeVerifyProvider()
    provider.delay_s = 5.0
    provider.result = _verified(uuid.uuid4())
    register_provider(provider)
    account = await _account(client, "timeout")
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="slow-token",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "PROVIDER_TIMEOUT"
    assert await _subscription_count(db_session_factory) == 0


async def test_invalid_purchase_rejected_without_state(db_session_factory, client):
    provider = FakeVerifyProvider()
    provider.error = EntitlementError("PURCHASE_INVALID", "No such purchase.", 422)
    register_provider(provider)
    account = await _account(client, "invalid")
    async with db_session_factory() as db:
        with pytest.raises(EntitlementError) as excinfo:
            await process_purchase(
                db,
                kind=VerifyKind.PURCHASE,
                provider_name="app_store",
                product_id=CYCLECOACH_PRO_MONTHLY,
                purchase_token="made-up",
                user_id=account["user_id"],
            )
    assert excinfo.value.code == "PURCHASE_INVALID"
    assert await _subscription_count(db_session_factory) == 0


# ---------------------------------------------------------------------------
# Invariants: stated plainly, one per rule the architecture rests on
# ---------------------------------------------------------------------------


async def test_invariant_client_state_alone_never_creates_pro(client, db_session_factory):
    account = await _account(client, "invariant-client")
    for path, body in (
        (VERIFY, {"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "is_pro": True}),
        (VERIFY, {"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "plan": "pro"}),
        (RESTORE, {"provider": "app_store", "product_id": CYCLECOACH_PRO_MONTHLY, "is_pro": True}),
    ):
        res = await client.post(path, headers=account["headers"], json=body)
        assert res.status_code == 422, f"{path} {body} -> {res.status_code}"
    async with db_session_factory() as db:
        assert (await resolve_state(db, account["user_id"])).plan is Plan.FREE


async def test_invariant_unknown_product_and_provider_grant_nothing(client, db_session_factory):
    account = await _account(client, "invariant-unknown")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={"provider": "nope", "product_id": "nope", "purchase_token": "nope"},
    )
    assert res.status_code == 422
    assert await _subscription_count(db_session_factory) == 0


async def test_invariant_provider_failure_grants_nothing(client, db_session_factory):
    provider = FakeVerifyProvider()
    provider.error = ConnectionError("store unreachable")
    register_provider(provider)
    account = await _account(client, "invariant-failure")
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "unlucky",
        },
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "PROVIDER_ERROR"
    assert await _subscription_count(db_session_factory) == 0


# ---------------------------------------------------------------------------
# Security: credentials stay out of every observable surface
# ---------------------------------------------------------------------------


async def test_purchase_token_in_no_response_log_or_metric(client, db_session_factory, caplog):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "no-leak")
    provider.result = _verified(account["user_id"])
    token = f"secret-token-{uuid.uuid4().hex}"
    with caplog.at_level("INFO", logger="cyclecoach"):
        res = await client.post(
            VERIFY,
            headers=account["headers"],
            json={
                "provider": "app_store",
                "product_id": CYCLECOACH_PRO_MONTHLY,
                "purchase_token": token,
            },
        )
    assert res.status_code == 200
    assert token not in res.text
    assert token not in "\n".join(r.message for r in caplog.records)
    assert token not in repr(metrics.snapshot())


async def test_overlong_token_rejected_without_echo(client):
    account = await _account(client, "long-token")
    token = "t" * 5000
    res = await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": token,
        },
    )
    assert res.status_code == 422, res.status_code
    # The field NAME may appear (it tells the client which field failed);
    # the rejected VALUE and the raw `input` entry must never appear.
    assert token not in res.text
    assert '"input"' not in res.text


async def test_verified_purchase_model_has_no_credential_fields():
    names = {f.name for f in fields(VerifiedPurchase)}
    assert "purchase_token" not in names
    assert "receipt" not in names
    assert "secret" not in names
    assert not {n for n in names if "credential" in n or "password" in n or "card" in n}


async def test_redaction_masks_purchase_credential_keys():
    assert redact({"purchase_token": "t"}) == {"purchase_token": "***"}
    assert redact({"receipt_data": "t"}) == {"receipt_data": "***"}
    assert redact({"provider": "app_store"}) == {"provider": "app_store"}


async def test_verification_metrics_carry_no_identity(client, db_session_factory):
    provider = FakeVerifyProvider()
    register_provider(provider)
    account = await _account(client, "metric-labels")
    provider.result = _verified(account["user_id"])
    await client.post(
        VERIFY,
        headers=account["headers"],
        json={
            "provider": "app_store",
            "product_id": CYCLECOACH_PRO_MONTHLY,
            "purchase_token": "counted",
        },
    )
    entries = metrics.snapshot().get("purchase_verifications_total", [])
    assert {(e["labels"]["outcome"], e["labels"]["provider"]) for e in entries} == {
        ("verified", "app_store")
    }
    blob = repr(metrics.snapshot())
    assert account["user_id"].hex not in blob
    assert "counted" not in blob
