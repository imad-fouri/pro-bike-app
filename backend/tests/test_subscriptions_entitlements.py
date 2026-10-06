"""Phase 10 WS-S entitlement tests.

These tests prove the product boundary the workstream exists to create: the
backend decides Pro access from its own rows, while the client is only ever a
messenger. No test here purchases anything, contacts a store, or treats a
client claim as authority.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.metrics import metrics
from app.models.subscription import (
    Entitlement,
    EntitlementSource,
    EntitlementStatus,
    Feature,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.subscription_providers import (
    ProviderSubscriptionView,
    SubscriptionProviderProtocol,
    VerifiedPurchase,
)
from app.services.subscription_service import (
    FREE_CAPABILITIES,
    EntitlementError,
    ProviderSubscriptionEvent,
    apply_provider_event,
    denial_detail,
    has_feature,
    resolve_state,
)

AUTH = "/api/v1/auth"
ME = "/api/v1/me/entitlements"
COACH_SUMMARY = "/api/v1/coach/weekly-summary"


@pytest.fixture(autouse=True)
def _clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


def _account_payload(tag: str) -> dict[str, str]:
    return {
        "email": f"entitlement-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "display_name": f"Rider {tag}",
    }


async def _account(client, tag: str) -> dict[str, object]:
    payload = _account_payload(tag)
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


def _event(
    user_id: uuid.UUID,
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    start_days: float = -1,
    end_days: float = 30,
    occurred_days: float = 0,
    provider: SubscriptionProvider = SubscriptionProvider.MANUAL,
    subscription_id: str | None = None,
    event_id: str | None = None,
    cancel_at_period_end: bool = False,
    start_tz=None,
) -> ProviderSubscriptionEvent:
    now = datetime.now(UTC)
    start = now + timedelta(days=start_days)
    end = now + timedelta(days=end_days)
    if start_tz is not None:
        start = start.astimezone(start_tz)
        end = end.astimezone(start_tz)
    return ProviderSubscriptionEvent(
        provider=provider,
        provider_subscription_id=subscription_id or f"manual-{uuid.uuid4().hex}",
        provider_event_id=event_id or f"event-{uuid.uuid4().hex}",
        user_id=user_id,
        plan=Plan.PRO,
        status=status,
        effective_start=start,
        effective_end=end,
        occurred_at=now + timedelta(days=occurred_days),
        cancel_at_period_end=cancel_at_period_end,
    )


async def _apply(db_session_factory, event: ProviderSubscriptionEvent) -> Subscription:
    async with db_session_factory() as db:
        return await apply_provider_event(db, event)


async def _entitlement_rows(db_session_factory, user_id: uuid.UUID) -> list[Entitlement]:
    async with db_session_factory() as db:
        rows = (
            (await db.execute(select(Entitlement).where(Entitlement.user_id == user_id)))
            .scalars()
            .all()
        )
        return list(rows)


async def _subscription_count(db_session_factory) -> int:
    async with db_session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Subscription))


def _check_counts() -> dict[tuple[str, str, str], float]:
    counts = {}
    for entry in metrics.snapshot().get("entitlement_checks_total", []):
        labels = entry["labels"]
        counts[(labels["feature"], labels["outcome"], labels["plan"])] = entry["value"]
    return counts


def _denial_counts() -> dict[tuple[str, str], float]:
    counts = {}
    for entry in metrics.snapshot().get("entitlement_denials_total", []):
        labels = entry["labels"]
        counts[(labels["feature"], labels["plan"])] = entry["value"]
    return counts


# ---------------------------------------------------------------------------
# Explicit Free state
# ---------------------------------------------------------------------------


async def test_new_account_has_an_explicit_free_state(client):
    account = await _account(client, "free")
    res = await client.get(ME, headers=account["headers"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"] == Plan.FREE.value
    assert body["entitlements"] == []
    assert body["free_capabilities"] == list(FREE_CAPABILITIES)
    assert set(body["free_capabilities"]) == {
        "core_ride_recording",
        "core_routes",
        "core_training",
        "limited_ai_status",
    }
    assert datetime.fromisoformat(body["evaluated_at"]).tzinfo is not None


async def test_entitlement_state_requires_authentication(client):
    res = await client.get(ME)
    assert res.status_code == 401, res.text


async def test_there_is_no_parameterized_entitlement_route(client):
    account = await _account(client, "no-idor-route")
    res = await client.get(f"/api/v1/users/{account['user_id']}/entitlements")
    assert res.status_code == 404, res.text


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_entitlement_state_is_read_only(client, method):
    account = await _account(client, f"read-only-{method}")
    res = await getattr(client, method)(ME, headers=account["headers"])
    assert res.status_code == 405, res.text


# ---------------------------------------------------------------------------
# Pro grants, expiration, revocation, and future starts
# ---------------------------------------------------------------------------


async def test_active_grant_makes_a_pro_state_with_all_premium_features(client, db_session_factory):
    account = await _account(client, "pro")
    await _apply(db_session_factory, _event(account["user_id"]))
    body = (await client.get(ME, headers=account["headers"])).json()

    assert body["plan"] == Plan.PRO.value
    assert {row["feature"] for row in body["entitlements"]} == {f.value for f in Feature}
    assert {row["status"] for row in body["entitlements"]} == {EntitlementStatus.ACTIVE.value}
    assert {row["source"] for row in body["entitlements"]} == {EntitlementSource.SUBSCRIPTION.value}
    assert all(row["effective"] for row in body["entitlements"])
    assert all(
        datetime.fromisoformat(row["starts_at"]).tzinfo is not None
        and datetime.fromisoformat(row["expires_at"]).tzinfo is not None
        for row in body["entitlements"]
    )
    # The commercial record stays server-side. The state response is product
    # vocabulary, not a subscription dump.
    assert "provider_subscription_id" not in repr(body)
    assert "provider_event_id" not in repr(body)
    assert "is_pro" not in repr(body)


async def test_free_user_is_denied_a_premium_answer_with_a_stable_code(client):
    account = await _account(client, "free-coach")
    res = await client.get(COACH_SUMMARY, headers=account["headers"])

    assert res.status_code == 403, res.text
    body = res.json()
    assert body["error"]["code"] == "ENTITLEMENT_REQUIRED"
    assert body["error"]["details"]["feature"] == Feature.AI_COACH.value
    assert body["error"]["details"]["required_plan"] == Plan.PRO.value
    assert "provider" not in repr(body).lower()
    assert "subscription" not in repr(body).lower()


async def test_pro_user_receives_the_premium_answer(client, db_session_factory):
    account = await _account(client, "pro-coach")
    await _apply(db_session_factory, _event(account["user_id"]))
    res = await client.get(COACH_SUMMARY, headers=account["headers"])

    assert res.status_code == 200, res.text
    assert res.json()["intent"] == "weekly_summary"


async def test_expired_grant_is_stored_but_not_effective(client, db_session_factory):
    account = await _account(client, "expired")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(
        db_session_factory,
        _event(account["user_id"], start_days=-40, end_days=30, subscription_id=subscription_id),
    )
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.EXPIRED,
            start_days=-40,
            end_days=-1,
            occurred_days=0,
            subscription_id=subscription_id,
        ),
    )

    body = (await client.get(ME, headers=account["headers"])).json()
    assert body["plan"] == Plan.FREE.value
    assert len(body["entitlements"]) == len(Feature)
    assert {row["status"] for row in body["entitlements"]} == {EntitlementStatus.INACTIVE.value}
    assert not any(row["effective"] for row in body["entitlements"])
    denied = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert denied.status_code == 403, denied.text


async def test_revoked_grant_is_terminal_until_a_newer_event(client, db_session_factory):
    account = await _account(client, "revoked")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(db_session_factory, _event(account["user_id"], subscription_id=subscription_id))
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.REVOKED,
            occurred_days=1,
            subscription_id=subscription_id,
        ),
    )

    body = (await client.get(ME, headers=account["headers"])).json()
    assert {row["status"] for row in body["entitlements"]} == {EntitlementStatus.REVOKED.value}
    assert not any(row["effective"] for row in body["entitlements"])
    denied = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert denied.status_code == 403, denied.text


async def test_future_grant_is_not_effective_before_its_start(client, db_session_factory):
    account = await _account(client, "future")
    await _apply(db_session_factory, _event(account["user_id"], start_days=1, end_days=31))
    body = (await client.get(ME, headers=account["headers"])).json()

    assert body["plan"] == Plan.FREE.value
    assert {row["status"] for row in body["entitlements"]} == {EntitlementStatus.ACTIVE.value}
    assert not any(row["effective"] for row in body["entitlements"])
    denied = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert denied.status_code == 403, denied.text


async def test_trialing_and_past_due_keep_current_period_access(client, db_session_factory):
    account = await _account(client, "grace")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.TRIALING,
            subscription_id=subscription_id,
        ),
    )
    trial = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert trial.status_code == 200, trial.text

    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.PAST_DUE,
            occurred_days=1,
            subscription_id=subscription_id,
        ),
    )
    grace = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert grace.status_code == 200, grace.text


async def test_end_of_period_cancellation_preserves_the_paid_period(client, db_session_factory):
    account = await _account(client, "cancel-period")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(
        db_session_factory,
        _event(account["user_id"], start_days=-2, subscription_id=subscription_id),
    )
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.CANCELED,
            start_days=-2,
            occurred_days=1,
            cancel_at_period_end=True,
            subscription_id=subscription_id,
        ),
    )
    res = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert res.status_code == 200, res.text


async def test_immediate_cancellation_ends_access_at_once(client, db_session_factory):
    account = await _account(client, "cancel-now")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(db_session_factory, _event(account["user_id"], subscription_id=subscription_id))
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.CANCELED,
            occurred_days=1,
            subscription_id=subscription_id,
        ),
    )
    denied = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert denied.status_code == 403, denied.text


@pytest.mark.parametrize("feature", list(Feature))
async def test_every_premium_feature_requires_pro(client, db_session_factory, feature):
    account = await _account(client, f"feature-{feature.value}")
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], feature) is False
        assert denial_detail(feature)["required_plan"] == Plan.PRO.value


# ---------------------------------------------------------------------------
# Cross-user isolation and fake client state
# ---------------------------------------------------------------------------


async def test_one_riders_pro_state_is_invisible_to_another(client, db_session_factory):
    pro = await _account(client, "pro-isolation")
    free = await _account(client, "free-isolation")
    await _apply(db_session_factory, _event(pro["user_id"]))

    free_state = (await client.get(ME, headers=free["headers"])).json()
    assert free_state["plan"] == Plan.FREE.value
    assert free_state["entitlements"] == []
    assert (await client.get(COACH_SUMMARY, headers=free["headers"])).status_code == 403
    assert (await client.get(COACH_SUMMARY, headers=pro["headers"])).status_code == 200


async def test_subscription_cannot_be_moved_between_riders(client, db_session_factory):
    first = await _account(client, "owner")
    second = await _account(client, "hijacker")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(db_session_factory, _event(first["user_id"], subscription_id=subscription_id))

    with pytest.raises(EntitlementError) as excinfo:
        await _apply(
            db_session_factory,
            _event(second["user_id"], subscription_id=subscription_id),
        )
    assert excinfo.value.code == "SUBSCRIPTION_USER_MISMATCH"
    second_state = (await client.get(ME, headers=second["headers"])).json()
    assert second_state["plan"] == Plan.FREE.value


async def test_client_supplied_pro_claims_have_no_authority(client):
    account = await _account(client, "fake-pro")
    res = await client.get(
        COACH_SUMMARY,
        params={"is_pro": "true", "plan": "pro", "feature": "ai_coach"},
        headers={**account["headers"], "X-Is-Pro": "true"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "ENTITLEMENT_REQUIRED"


# ---------------------------------------------------------------------------
# Idempotency, ordering, uniqueness, time, and concurrency
# ---------------------------------------------------------------------------


async def test_duplicate_provider_event_does_not_duplicate_state(client, db_session_factory):
    account = await _account(client, "duplicate")
    event = _event(account["user_id"])
    first = await _apply(db_session_factory, event)
    second = await _apply(db_session_factory, event)

    assert first.id == second.id
    assert await _subscription_count(db_session_factory) == 1
    assert len(await _entitlement_rows(db_session_factory, account["user_id"])) == len(Feature)
    assert (await client.get(COACH_SUMMARY, headers=account["headers"])).status_code == 200


async def test_out_of_order_cancellation_does_not_undo_a_renewal(client, db_session_factory):
    account = await _account(client, "out-of-order")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            occurred_days=1,
            subscription_id=subscription_id,
        ),
    )
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=SubscriptionStatus.CANCELED,
            occurred_days=0,
            subscription_id=subscription_id,
        ),
    )
    res = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert res.status_code == 200, res.text


async def test_provider_subscription_identity_is_unique(db_session_factory, client):
    first = await _account(client, "unique-first")
    second = await _account(client, "unique-second")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(db_session_factory, _event(first["user_id"], subscription_id=subscription_id))

    async with db_session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            Subscription(
                user_id=second["user_id"],
                provider=SubscriptionProvider.MANUAL,
                provider_subscription_id=subscription_id,
                plan=Plan.PRO,
                status=SubscriptionStatus.ACTIVE,
                started_at=now,
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                cancel_at_period_end=False,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    assert await _subscription_count(db_session_factory) == 1


async def test_non_utc_event_times_are_evaluated_correctly(client, db_session_factory):
    account = await _account(client, "timezone")
    pacific = timezone(timedelta(hours=-8))
    await _apply(
        db_session_factory,
        _event(account["user_id"], start_days=-1, end_days=1, start_tz=pacific),
    )
    body = (await client.get(ME, headers=account["headers"])).json()
    assert body["plan"] == Plan.PRO.value
    assert all(row["effective"] for row in body["entitlements"])


async def test_concurrent_reads_agree_on_one_pro_state(client, db_session_factory):
    import asyncio

    account = await _account(client, "concurrent-read")
    await _apply(db_session_factory, _event(account["user_id"]))

    async def _read_once():
        async with db_session_factory() as db:
            state = await resolve_state(db, account["user_id"])
            return state.plan, await has_feature(db, account["user_id"], Feature.AI_COACH)

    results = await asyncio.gather(*(_read_once() for _ in range(8)))
    assert results == [(Plan.PRO, True)] * 8


async def test_concurrent_duplicate_events_converge_on_one_subscription(db_session_factory, client):
    import asyncio

    account = await _account(client, "concurrent-event")
    event = _event(account["user_id"])

    async def _apply_once():
        async with db_session_factory() as db:
            return await apply_provider_event(db, event)

    first, second = await asyncio.gather(_apply_once(), _apply_once())
    assert first.id == second.id
    assert await _subscription_count(db_session_factory) == 1
    assert len(await _entitlement_rows(db_session_factory, account["user_id"])) == len(Feature)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_days": 2, "end_days": 1},
        {"cancel_at_period_end": True},
    ],
)
async def test_invalid_events_write_nothing(db_session_factory, client, kwargs):
    account = await _account(client, f"invalid-{len(kwargs)}")
    with pytest.raises(EntitlementError):
        await _apply(db_session_factory, _event(account["user_id"], **kwargs))
    assert await _subscription_count(db_session_factory) == 0
    assert await _entitlement_rows(db_session_factory, account["user_id"]) == []


async def test_naive_event_times_are_rejected_before_any_write(db_session_factory, client):
    account = await _account(client, "naive")
    now = datetime.now(UTC).replace(tzinfo=None)
    with pytest.raises(EntitlementError):
        event = ProviderSubscriptionEvent(
            provider=SubscriptionProvider.MANUAL,
            provider_subscription_id=f"manual-{uuid.uuid4().hex}",
            provider_event_id=f"event-{uuid.uuid4().hex}",
            user_id=account["user_id"],
            plan=Plan.PRO,
            status=SubscriptionStatus.ACTIVE,
            effective_start=now,
            effective_end=now + timedelta(days=30),
            occurred_at=datetime.now(UTC),
        )
        await _apply(db_session_factory, event)
    assert await _subscription_count(db_session_factory) == 0


# ---------------------------------------------------------------------------
# Metrics, logs, and stored-data minimization
# ---------------------------------------------------------------------------


async def test_denials_are_counted_without_identifiers(client, db_session_factory):
    account = await _account(client, "denial-metrics")
    await _apply(db_session_factory, _event(account["user_id"], start_days=-30, end_days=-1))
    res = await client.get(COACH_SUMMARY, headers=account["headers"])
    assert res.status_code == 403, res.text

    assert _check_counts()[("ai_coach", "denied", "free")] == 1
    assert _denial_counts()[("ai_coach", "free")] == 1
    blob = repr(metrics.snapshot())
    assert account["user_id"].hex not in blob
    assert "@" not in blob


async def test_subscription_events_are_counted_without_external_ids(
    client, db_session_factory, caplog
):
    account = await _account(client, "subscription-metrics")
    event = _event(account["user_id"])
    with caplog.at_level("INFO", logger="cyclecoach"):
        await _apply(db_session_factory, event)

    kinds = {
        (entry["labels"]["event"], entry["labels"]["provider"], entry["labels"]["status"])
        for entry in metrics.snapshot().get("subscription_events_total", [])
    }
    assert ("applied", "manual", "active") in kinds
    logs = "\n".join(record.message for record in caplog.records)
    assert "subscription.event_applied" in logs
    assert event.provider_subscription_id not in logs
    assert event.provider_event_id not in logs


async def test_tables_store_no_payment_or_purchase_secrets():
    columns = {
        table.name: {column.name for column in table.columns}
        for table in (Subscription.__table__, Entitlement.__table__)
    }
    forbidden = {
        "card",
        "purchase_token",
        "purchase_receipt",
        "provider_secret",
        "access_token",
        "secret",
    }
    for table, names in columns.items():
        assert not {name for name in names if any(part in name for part in forbidden)}, table
    assert columns["subscriptions"] >= {
        "user_id",
        "provider",
        "provider_subscription_id",
        "plan",
        "status",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
    }
    assert columns["entitlements"] >= {
        "user_id",
        "feature",
        "status",
        "source",
        "starts_at",
        "expires_at",
    }


# ---------------------------------------------------------------------------
# Provider contract (fake provider only; never a real store)
# ---------------------------------------------------------------------------


class FakeSubscriptionProvider:
    """A test double for the provider protocol, not a billing integration.

    It speaks a deliberately toy wire format: signatures are the literal string
    ``valid:<event_id>``. Nothing about a passing test here transfers to Apple
    or Google.
    """

    name = SubscriptionProvider.APP_STORE

    def __init__(self) -> None:
        self.verifications = 0

    async def verify_purchase(
        self, *, purchase_token: str, expected_user_id: uuid.UUID
    ) -> VerifiedPurchase:
        self.verifications += 1
        now = datetime.now(UTC)
        if purchase_token == f"expired-for-{expected_user_id}":
            return VerifiedPurchase(
                provider=self.name,
                provider_subscription_id=f"app-store-{expected_user_id.hex[:8]}",
                user_id=expected_user_id,
                plan=Plan.PRO,
                status=SubscriptionStatus.EXPIRED,
                current_period_start=now - timedelta(days=60),
                current_period_end=now - timedelta(days=30),
                occurred_at=now - timedelta(days=30),
                provider_event_id=f"expired-{expected_user_id.hex[:8]}",
            )
        if purchase_token == f"revoked-for-{expected_user_id}":
            return VerifiedPurchase(
                provider=self.name,
                provider_subscription_id=f"app-store-{expected_user_id.hex[:8]}",
                user_id=expected_user_id,
                plan=Plan.PRO,
                status=SubscriptionStatus.REVOKED,
                current_period_start=now - timedelta(days=30),
                current_period_end=now,
                occurred_at=now,
                provider_event_id=f"revoked-{expected_user_id.hex[:8]}",
            )
        if purchase_token != f"valid-for-{expected_user_id}":
            raise EntitlementErrorShim("PURCHASE_INVALID")
        return VerifiedPurchase(
            provider=self.name,
            provider_subscription_id=f"app-store-{expected_user_id.hex[:8]}",
            user_id=expected_user_id,
            plan=Plan.PRO,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=30),
            occurred_at=now,
            provider_event_id=f"verified-{expected_user_id.hex[:8]}",
        )

    async def get_subscription(self, *, provider_subscription_id: str) -> ProviderSubscriptionView:
        now = datetime.now(UTC)
        return ProviderSubscriptionView(
            provider=self.name,
            provider_subscription_id=provider_subscription_id,
            user_id=uuid.uuid4(),
            plan=Plan.PRO,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )

    async def process_event(
        self, *, payload: Mapping[str, object], signature: str | None
    ) -> ProviderSubscriptionEvent:
        event_id = str(payload.get("event_id", ""))
        if signature != f"valid:{event_id}":
            raise EntitlementErrorShim("EVENT_SIGNATURE_INVALID")
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
        if occurred_at.tzinfo is None:
            raise EntitlementErrorShim("EVENT_TIME_NAIVE")
        return ProviderSubscriptionEvent(
            provider=self.name,
            provider_subscription_id=str(payload["provider_subscription_id"]),
            provider_event_id=event_id,
            user_id=uuid.UUID(str(payload["user_id"])),
            plan=Plan.PRO,
            status=SubscriptionStatus(str(payload["status"])),
            effective_start=datetime.fromisoformat(str(payload["effective_start"])),
            effective_end=datetime.fromisoformat(str(payload["effective_end"])),
            occurred_at=occurred_at,
        )


class EntitlementErrorShim(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def test_provider_protocol_shape_is_implementable():
    provider: SubscriptionProviderProtocol = FakeSubscriptionProvider()
    assert provider.name is SubscriptionProvider.APP_STORE
    assert hasattr(provider, "verify_purchase")
    assert hasattr(provider, "get_subscription")
    assert hasattr(provider, "process_event")


async def test_verified_purchase_never_returns_the_purchase_credential(db_session_factory, client):
    account = await _account(client, "verified-purchase")
    provider = FakeSubscriptionProvider()
    token = f"valid-for-{account['user_id']}"
    verified = await provider.verify_purchase(
        purchase_token=token, expected_user_id=account["user_id"]
    )

    assert verified.status is SubscriptionStatus.ACTIVE
    assert token not in repr(verified)
    event = ProviderSubscriptionEvent(
        provider=verified.provider,
        provider_subscription_id=verified.provider_subscription_id,
        provider_event_id=verified.provider_event_id,
        user_id=verified.user_id,
        plan=verified.plan,
        status=verified.status,
        effective_start=verified.current_period_start,
        effective_end=verified.current_period_end,
        occurred_at=verified.occurred_at,
    )
    await _apply(db_session_factory, event)
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.AI_COACH) is True


async def test_failed_verification_rejects_foreign_and_malformed_tokens(client):
    account = await _account(client, "failed-purchase")
    provider = FakeSubscriptionProvider()
    with pytest.raises(EntitlementErrorShim):
        await provider.verify_purchase(
            purchase_token="valid-for-someone-else", expected_user_id=account["user_id"]
        )
    with pytest.raises(EntitlementErrorShim):
        await provider.verify_purchase(
            purchase_token="not-a-purchase", expected_user_id=account["user_id"]
        )
    assert provider.verifications == 2


async def test_expired_and_revoked_purchases_do_not_grant_access(db_session_factory, client):
    expired = await _account(client, "expired-purchase")
    revoked = await _account(client, "revoked-purchase")
    provider = FakeSubscriptionProvider()

    for account, token in (
        (expired, f"expired-for-{expired['user_id']}"),
        (revoked, f"revoked-for-{revoked['user_id']}"),
    ):
        verified = await provider.verify_purchase(
            purchase_token=token, expected_user_id=account["user_id"]
        )
        await _apply(
            db_session_factory,
            ProviderSubscriptionEvent(
                provider=verified.provider,
                provider_subscription_id=verified.provider_subscription_id,
                provider_event_id=verified.provider_event_id,
                user_id=verified.user_id,
                plan=verified.plan,
                status=verified.status,
                effective_start=verified.current_period_start,
                effective_end=verified.current_period_end,
                occurred_at=verified.occurred_at,
            ),
        )
        denied = await client.get(COACH_SUMMARY, headers=account["headers"])
        assert denied.status_code == 403, denied.text


async def test_provider_events_are_duplicate_safe_and_order_aware(db_session_factory, client):
    account = await _account(client, "provider-events")
    provider = FakeSubscriptionProvider()
    now = datetime.now(UTC)
    subscription_id = f"app-store-{uuid.uuid4().hex}"
    renewal = {
        "event_id": f"renewal-{uuid.uuid4().hex}",
        "provider_subscription_id": subscription_id,
        "user_id": str(account["user_id"]),
        "status": "active",
        "effective_start": (now - timedelta(days=1)).isoformat(),
        "effective_end": (now + timedelta(days=30)).isoformat(),
        "occurred_at": now.isoformat(),
    }
    cancellation = {
        **renewal,
        "event_id": f"cancellation-{uuid.uuid4().hex}",
        "status": "canceled",
        "occurred_at": (now - timedelta(hours=1)).isoformat(),
    }

    first = await provider.process_event(payload=renewal, signature=f"valid:{renewal['event_id']}")
    await _apply(db_session_factory, first)
    # A retried callback must not append state.
    await _apply(db_session_factory, first)
    # An older cancellation arriving late must not undo the renewal.
    stale = await provider.process_event(
        payload=cancellation, signature=f"valid:{cancellation['event_id']}"
    )
    await _apply(db_session_factory, stale)

    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.AI_COACH) is True
    assert await _subscription_count(db_session_factory) == 1


async def test_provider_callbacks_require_a_signature():
    provider = FakeSubscriptionProvider()
    with pytest.raises(EntitlementErrorShim):
        await provider.process_event(payload={"event_id": "e1"}, signature=None)
    with pytest.raises(EntitlementErrorShim):
        await provider.process_event(payload={"event_id": "e1"}, signature="valid:wrong")
