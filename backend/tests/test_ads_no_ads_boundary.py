"""Phase 10 WS-AF backend tests: the NO_ADS entitlement boundary.

WS-AF adds no backend endpoint, table, or metric — that absence is itself a
verified decision (see docs/ads-foundation.md §4). These tests prove the
existing entitlement machinery carries the advertising boundary correctly:
NO_ADS is granted, expires, revokes, and stays per-rider through the same
code paths as every other capability, and no client can mutate or observe
another rider's advertising state.

State is arranged through `apply_provider_event`, the same controlled path
the WS-S tests use: there is deliberately no grant endpoint to call.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.metrics import metrics
from app.models.subscription import (
    Entitlement,
    EntitlementStatus,
    Feature,
    Plan,
    Subscription,
    SubscriptionProvider,
    SubscriptionStatus,
)
from app.services.subscription_service import (
    ProviderSubscriptionEvent,
    apply_provider_event,
    evaluate_feature,
    has_feature,
    resolve_state,
)

AUTH = "/api/v1/auth"
ME = "/api/v1/me/entitlements"


@pytest.fixture(autouse=True)
def _clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


async def _account(client, tag: str) -> dict[str, object]:
    payload = {
        "email": f"ads-{tag}-{uuid.uuid4().hex[:8]}@example.com",
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


def _event(
    user_id: uuid.UUID,
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    start_days: float = -1,
    end_days: float = 30,
    occurred_days: float = 0,
    subscription_id: str | None = None,
) -> ProviderSubscriptionEvent:
    now = datetime.now(UTC)
    return ProviderSubscriptionEvent(
        provider=SubscriptionProvider.MANUAL,
        provider_subscription_id=subscription_id or f"manual-{uuid.uuid4().hex}",
        provider_event_id=f"event-{uuid.uuid4().hex}",
        user_id=user_id,
        plan=Plan.PRO,
        status=status,
        effective_start=now + timedelta(days=start_days),
        effective_end=now + timedelta(days=end_days),
        occurred_at=now + timedelta(days=occurred_days),
    )


async def _apply(db_session_factory, event: ProviderSubscriptionEvent):
    async with db_session_factory() as db:
        return await apply_provider_event(db, event)


async def _no_ads_row(db_session_factory, user_id: uuid.UUID) -> Entitlement | None:
    async with db_session_factory() as db:
        res = await db.execute(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.feature == Feature.NO_ADS,
            )
        )
        return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# NO_ADS lifecycle through the existing entitlement machinery
# ---------------------------------------------------------------------------


async def test_free_account_holds_no_no_ads_grant(client, db_session_factory):
    account = await _account(client, "free-ads")
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.NO_ADS) is False
        state = await resolve_state(db, account["user_id"])
        assert state.plan is Plan.FREE
        assert state.entitlements == ()

    body = (await client.get(ME, headers=account["headers"])).json()
    assert body["plan"] == Plan.FREE.value
    assert body["entitlements"] == []


async def test_active_pro_grant_carries_an_effective_no_ads(client, db_session_factory):
    account = await _account(client, "pro-ads")
    await _apply(db_session_factory, _event(account["user_id"]))

    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.NO_ADS) is True
    row = await _no_ads_row(db_session_factory, account["user_id"])
    assert row is not None
    assert row.status is EntitlementStatus.ACTIVE
    assert row.starts_at.tzinfo is not None and row.expires_at.tzinfo is not None

    body = (await client.get(ME, headers=account["headers"])).json()
    grants = {row["feature"]: row for row in body["entitlements"]}
    assert grants["no_ads"]["effective"] is True


@pytest.mark.parametrize(
    "status,start_days,end_days",
    [
        (SubscriptionStatus.EXPIRED, -40, -1),  # lapsed period
        (SubscriptionStatus.REVOKED, -1, 30),  # revoked, period intact
    ],
)
async def test_dead_no_ads_grants_suppress_nothing(
    client, db_session_factory, status, start_days, end_days
):
    account = await _account(client, f"dead-{status.value}")
    subscription_id = f"manual-{uuid.uuid4().hex}"
    await _apply(db_session_factory, _event(account["user_id"], subscription_id=subscription_id))
    await _apply(
        db_session_factory,
        _event(
            account["user_id"],
            status=status,
            start_days=start_days,
            end_days=end_days,
            occurred_days=1,
            subscription_id=subscription_id,
        ),
    )
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.NO_ADS) is False
        state = await resolve_state(db, account["user_id"])
        assert state.plan is Plan.FREE


async def test_future_no_ads_grant_is_not_yet_effective(client, db_session_factory):
    account = await _account(client, "future-ads")
    await _apply(db_session_factory, _event(account["user_id"], start_days=1, end_days=31))
    async with db_session_factory() as db:
        assert await has_feature(db, account["user_id"], Feature.NO_ADS) is False


async def test_no_ads_is_per_rider(client, db_session_factory):
    pro = await _account(client, "pro-rider")
    free = await _account(client, "free-rider")
    await _apply(db_session_factory, _event(pro["user_id"]))

    async with db_session_factory() as db:
        assert await has_feature(db, pro["user_id"], Feature.NO_ADS) is True
        assert await has_feature(db, free["user_id"], Feature.NO_ADS) is False

    free_body = (await client.get(ME, headers=free["headers"])).json()
    assert free_body["plan"] == Plan.FREE.value
    assert free_body["entitlements"] == []


# ---------------------------------------------------------------------------
# No advertising state is mutable or observable across riders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_no_ads_state_cannot_be_written_by_clients(client, method):
    account = await _account(client, f"immutable-{method}")
    call = getattr(client, method)
    if method == "delete":
        res = await call(ME, headers=account["headers"])
    else:
        res = await call(ME, headers=account["headers"], json={"feature": "no_ads"})
    assert res.status_code == 405, res.text


async def test_no_parameterized_ad_policy_route_exists(client):
    account = await _account(client, "no-route")
    for path in (
        f"/api/v1/users/{account['user_id']}/entitlements",
        "/api/v1/me/ad-policy",
        "/api/v1/me/no-ads",
    ):
        res = await client.get(path, headers=account["headers"])
        assert res.status_code == 404, f"{path} -> {res.status_code}"


# ---------------------------------------------------------------------------
# Privacy: the advertising boundary leaks nothing
# ---------------------------------------------------------------------------


async def test_entitlement_response_carries_no_commercial_identifiers(client, db_session_factory):
    account = await _account(client, "private-ads")
    event = _event(account["user_id"])
    await _apply(db_session_factory, event)

    body = (await client.get(ME, headers=account["headers"])).json()
    blob = repr(body)
    assert event.provider_subscription_id not in blob
    assert event.provider_event_id not in blob
    assert "purchase" not in blob.lower()
    assert "token" not in blob.lower()


async def test_subscription_events_log_no_identifiers(client, db_session_factory, caplog):
    account = await _account(client, "quiet-ads")
    event = _event(account["user_id"])
    with caplog.at_level("INFO", logger="cyclecoach"):
        await _apply(db_session_factory, event)
    logs = "\n".join(record.message for record in caplog.records)
    assert event.provider_subscription_id not in logs
    assert event.provider_event_id not in logs


async def test_no_ads_checks_are_counted_without_identity(client, db_session_factory):
    account = await _account(client, "counted-ads")
    async with db_session_factory() as db:
        allowed, state = await evaluate_feature(db, account["user_id"], Feature.NO_ADS)
        assert allowed is False
        assert state.plan is Plan.FREE

    checks = metrics.snapshot().get("entitlement_checks_total", [])
    assert {
        (entry["labels"]["feature"], entry["labels"]["outcome"], entry["labels"]["plan"])
        for entry in checks
    } == {("no_ads", "denied", "free")}
    denials = metrics.snapshot().get("entitlement_denials_total", [])
    assert {(entry["labels"]["feature"], entry["labels"]["plan"]) for entry in denials} == {
        ("no_ads", "free")
    }
    assert account["user_id"].hex not in repr(metrics.snapshot())


async def test_no_ads_tables_store_no_credentials():
    # No ads table may ever appear without updating this file: the table-name
    # assertion below names the only two tables the advertising boundary may
    # touch, so a future `ad_impressions`/`ad_clicks`/`ad_revenue` table
    # cannot slip past an untouched test.
    assert {Subscription.__table__.name, Entitlement.__table__.name} == {
        "subscriptions",
        "entitlements",
    }
    columns = {
        column.name
        for table in (Subscription.__table__, Entitlement.__table__)
        for column in table.columns
    }
    forbidden = ("ad_id", "idfa", "aaid", "impression", "click", "revenue", "token")
    assert not {name for name in columns if any(part in name for part in forbidden)}
