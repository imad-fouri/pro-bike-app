"""Phase 6 training API tests: provenance, ownership/IDOR, invariants,
optimistic locking, and the ride -> training handoff.

The GPS/sensor fixtures here are synthetic but realistic: ~10 m per step, 1 Hz
sensor readings, no live hardware (ADR-10 §6).
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

TRAINING = "/api/v1/training"
WORKOUTS = "/api/v1/workouts"
AUTH = "/api/v1/auth"
BIKES = "/api/v1/bikes"
RIDES = "/api/v1/rides"

A = {
    "email": "ta@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider A",
}
B = {
    "email": "tb@example.com",
    "password": "StrongPass123",
    "password_confirm": "StrongPass123",
    "display_name": "Rider B",
}

LAT, LON, STEP = 33.0, -6.0, 0.00009
T0 = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)


def pt(seq, *, sec, power=None, hr=None, cad=None, dlat=None):
    payload = {
        "client_point_uuid": str(uuid.uuid4()),
        "seq": seq,
        "lat": LAT + (STEP * 0.5 * seq if dlat is None else dlat),
        "lon": LON,
        "recorded_at": (T0 + timedelta(seconds=sec)).isoformat(),
    }
    if power is not None:
        payload["power_w"] = power
    if hr is not None:
        payload["hr_bpm"] = hr
    if cad is not None:
        payload["cadence_rpm"] = cad
    return payload


def chunk(points):
    return {"points": points}


def f(value):
    """Decimals are serialized as strings to preserve precision (see §10)."""
    assert value is not None
    return float(value)


async def _user(client, data):
    assert (await client.post(f"{AUTH}/register", json=data)).status_code == 201
    r = await client.post(
        f"{AUTH}/login", json={"email": data["email"], "password": data["password"]}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _bike(client, headers):
    r = await client.post(BIKES, json={"name": "Road", "category": "road"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _ride(client, headers, bike_id):
    r = await client.post(
        RIDES,
        json={"bike_id": bike_id, "client_ride_uuid": str(uuid.uuid4())},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(client, headers, ride_id, points):
    r = await client.post(f"{RIDES}/{ride_id}/points", json=chunk(points), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _power_ride(client, headers, bike_id, *, minutes=40, watts=250, hr=150):
    """A ride with power/HR/cadence sampled at 1 Hz (bounded for test speed)."""
    ride_id = await _ride(client, headers, bike_id)
    total = minutes * 60
    points = [pt(i, sec=i, power=watts, hr=hr, cad=90, dlat=STEP * 0.02 * i) for i in range(total)]
    # Upload in API-sized chunks.
    for start in range(0, total, 500):
        await _upload(client, headers, ride_id, points[start : start + 500])
    r = await client.post(f"{RIDES}/{ride_id}/finish", headers=headers)
    assert r.status_code == 200, r.text
    return ride_id


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


async def test_profile_defaults_to_unavailable_ftp(client):
    h = await _user(client, A)
    r = await client.get(f"{TRAINING}/profile", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ftp_w"] is None
    assert body["ftp_basis"] == "unavailable"
    assert body["effective_timezone"] == "UTC"


async def test_setting_ftp_creates_an_append_only_record(client):
    h = await _user(client, A)
    r = await client.put(
        f"{TRAINING}/profile",
        json={"ftp_w": 250, "max_hr_bpm": 185, "resting_hr_bpm": 55},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert f(r.json()["ftp_w"]) == 250.0
    assert r.json()["ftp_basis"] == "measured"

    records = (await client.get(f"{TRAINING}/ftp-records", headers=h)).json()
    assert len(records["items"]) == 1
    assert records["items"][0]["source"] == "manual"
    assert records["items"][0]["confirmed"] is True


async def test_ftp_history_is_appended_not_replaced(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 265}, headers=h)
    records = (await client.get(f"{TRAINING}/ftp-records", headers=h)).json()
    assert [f(r["value_w"]) for r in records["items"]] == [265.0, 250.0]
    # The newest same-day record wins: the resolution key ends at created_at,
    # not at the UUID, because string order on a UUID is not insertion order.
    assert f(records["effective_ftp_w"]) == 265.0


async def test_confirmed_ftp_beats_an_estimate(client):
    h = await _user(client, A)
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "estimated", "value_w": 320, "effective_at": "2026-05-01"},
        headers=h,
    )
    assert (await client.get(f"{TRAINING}/profile", headers=h)).json()["ftp_basis"] == "estimated"
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    body = (await client.get(f"{TRAINING}/profile", headers=h)).json()
    assert f(body["ftp_w"]) == 250.0
    assert body["ftp_basis"] == "measured"


async def test_implausible_ftp_is_rejected(client):
    h = await _user(client, A)
    r = await client.post(
        f"{TRAINING}/ftp-records", json={"source": "manual", "value_w": 5}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_invalid_timezone_is_rejected(client):
    h = await _user(client, A)
    r = await client.put(f"{TRAINING}/profile", json={"timezone": "Mars/Olympus"}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_TIMEZONE"


async def test_timezone_override_changes_the_local_date(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"timezone": "Pacific/Kiritimati"}, headers=h)
    profile = (await client.get(f"{TRAINING}/profile", headers=h)).json()
    assert profile["effective_timezone"] == "Pacific/Kiritimati"
    assert profile["timezone"] == "Pacific/Kiritimati"


# ---------------------------------------------------------------------------
# Ride -> training handoff
# ---------------------------------------------------------------------------


async def test_completing_a_ride_creates_a_training_activity(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    bike = await _bike(client, h)
    ride_id = await _power_ride(client, h, bike, minutes=40, watts=250)

    activities = (await client.get(f"{TRAINING}/activities", headers=h)).json()
    assert activities["total"] == 1
    activity = activities["items"][0]
    assert activity["ride_id"] == ride_id
    assert activity["has_power"] is True
    assert activity["has_heart_rate"] is True
    assert activity["has_cadence"] is True
    assert f(activity["normalized_power_w"]) == 250.0
    assert f(activity["intensity_factor"]) == 1.0
    assert f(activity["effective_ftp_w"]) == 250.0
    assert activity["ftp_basis"] == "measured"
    assert activity["analysis_version"] == "activity_analysis_v1"


async def test_gps_only_ride_reports_no_power_rather_than_zero(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    await _upload(
        client,
        h,
        ride_id,
        [pt(i, sec=i * 5, dlat=STEP * i) for i in range(120)],
    )
    assert (await client.post(f"{RIDES}/{ride_id}/finish", headers=h)).status_code == 200

    activity = (await client.get(f"{TRAINING}/activities", headers=h)).json()["items"][0]
    assert activity["has_power"] is False
    assert activity["power_load"] is None
    assert activity["normalized_power_w"] is None
    assert activity["intensity_factor"] is None
    assert f(activity["power_seconds"]) == 0
    assert activity["ftp_basis"] == "unavailable"
    # GPS-derived metrics still come from the canonical ride summary.
    assert activity["moving_seconds"] > 0
    assert f(activity["distance_m"]) > 0


async def test_power_metrics_need_an_ftp(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    await _power_ride(client, h, bike, minutes=35, watts=250)
    activity = (await client.get(f"{TRAINING}/activities", headers=h)).json()["items"][0]
    assert f(activity["normalized_power_w"]) == 250.0  # NP needs no FTP
    assert activity["intensity_factor"] is None  # IF does
    assert activity["power_load"] is None
    assert activity["ftp_basis"] == "unavailable"


async def test_recording_a_new_ftp_does_not_rewrite_history(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    await _power_ride(client, h, bike, minutes=35, watts=250)
    before = (await client.get(f"{TRAINING}/activities", headers=h)).json()["items"][0]
    assert before["effective_ftp_w"] is None

    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    after = (await client.get(f"{TRAINING}/activities", headers=h)).json()["items"][0]
    assert after["effective_ftp_w"] is None  # unchanged until explicitly re-derived
    assert after["id"] == before["id"]


async def test_reanalyze_derives_against_the_current_ftp(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _power_ride(client, h, bike, minutes=35, watts=250)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)

    r = await client.post(f"{TRAINING}/activities/{ride_id}/reanalyze", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert f(body["effective_ftp_w"]) == 250.0
    assert f(body["intensity_factor"]) == 1.0
    assert body["ftp_basis"] == "measured"
    assert f(body["power_load"]) > 0
    assert (await client.get(f"{TRAINING}/activities", headers=h)).json()["total"] == 1


async def test_reanalyze_rejects_an_unfinished_ride(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    r = await client.post(f"{TRAINING}/activities/{ride_id}/reanalyze", headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RIDE_NOT_COMPLETED"


async def test_zone_distribution_is_returned_with_the_detail(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    bike = await _bike(client, h)
    await _power_ride(client, h, bike, minutes=35, watts=250)
    activity_id = (await client.get(f"{TRAINING}/activities", headers=h)).json()["items"][0]["id"]
    detail = (await client.get(f"{TRAINING}/activities/{activity_id}", headers=h)).json()
    power_zones = [z for z in detail["zones"] if z["kind"] == "power"]
    assert len(power_zones) == 7
    assert f(power_zones[3]["seconds"]) > 0  # 250 W = 100% FTP -> threshold
    assert sum(f(z["seconds"]) for z in power_zones) == f(detail["power_seconds"])


# ---------------------------------------------------------------------------
# Test-derived FTP
# ---------------------------------------------------------------------------


async def test_a_newer_estimated_record_never_displaces_a_confirmed_one(client):
    h = await _user(client, A)
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "manual", "value_w": 250, "effective_at": "2026-01-01"},
        headers=h,
    )
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "estimated", "value_w": 300, "effective_at": "2026-06-01"},
        headers=h,
    )
    body = (await client.get(f"{TRAINING}/profile", headers=h)).json()
    assert f(body["ftp_w"]) == 250.0
    assert body["ftp_basis"] == "measured"


async def test_a_newer_confirmed_record_supersedes_an_older_one(client):
    h = await _user(client, A)
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "test_20min", "value_w": 280, "effective_at": "2026-01-01"},
        headers=h,
    )
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "manual", "value_w": 260, "effective_at": "2026-06-01"},
        headers=h,
    )
    body = (await client.get(f"{TRAINING}/profile", headers=h)).json()
    assert f(body["ftp_w"]) == 260.0


async def test_an_out_of_range_stored_record_is_ignored_not_clamped(client):
    """A bad row must not become the effective FTP; it just does not count."""
    h = await _user(client, A)
    await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "imported", "value_w": 150, "effective_at": "2026-01-01"},
        headers=h,
    )
    assert f((await client.get(f"{TRAINING}/profile", headers=h)).json()["ftp_w"]) == 150.0


async def test_20_min_test_ftp_is_computed_server_side(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    total = 22 * 60
    points = [pt(i, sec=i, power=300, hr=160, dlat=STEP * 0.01 * i) for i in range(total)]
    for start in range(0, total, 500):
        await _upload(client, h, ride_id, points[start : start + 500])
    await client.post(f"{RIDES}/{ride_id}/finish", headers=h)

    r = await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "test_20min", "ride_id": ride_id},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert f(body["value_w"]) == 285.0  # 0.95 x 300
    assert body["approximation"] is False
    assert body["evidence"]["version"] == "ftp_20min_095_v1"
    assert f((await client.get(f"{TRAINING}/profile", headers=h)).json()["ftp_w"]) == 285.0


async def test_ramp_test_is_labelled_an_approximation(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    total = 12 * 60
    points = [pt(i, sec=i, power=300, hr=160, dlat=STEP * 0.01 * i) for i in range(total)]
    for start in range(0, total, 500):
        await _upload(client, h, ride_id, points[start : start + 500])
    await client.post(f"{RIDES}/{ride_id}/finish", headers=h)

    r = await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "test_ramp", "ride_id": ride_id},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["approximation"] is True


async def test_test_ftp_needs_the_ride_and_enough_data(client):
    h = await _user(client, A)
    r = await client.post(f"{TRAINING}/ftp-records", json={"source": "test_20min"}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "RIDE_REQUIRED"

    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    await _upload(client, h, ride_id, [pt(i, sec=i * 5, dlat=STEP * i) for i in range(20)])
    await client.post(f"{RIDES}/{ride_id}/finish", headers=h)
    r = await client.post(
        f"{TRAINING}/ftp-records", json={"source": "test_20min", "ride_id": ride_id}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_DATA"


async def test_a_client_cannot_assert_a_test_ftp_value(client):
    h = await _user(client, A)
    bike = await _bike(client, h)
    ride_id = await _power_ride(client, h, bike, minutes=35, watts=250)
    r = await client.post(
        f"{TRAINING}/ftp-records",
        json={"source": "test_20min", "value_w": 900, "ride_id": ride_id},
        headers=h,
    )
    # value_w is ignored for a test protocol: the ride decides.
    assert r.status_code == 201, r.text
    assert f(r.json()["value_w"]) != 900.0


# ---------------------------------------------------------------------------
# Loads, recovery, summary
# ---------------------------------------------------------------------------


async def test_loads_and_recovery_start_insufficient(client):
    h = await _user(client, A)
    loads = (await client.get(f"{TRAINING}/loads", headers=h)).json()
    assert loads["items"] == []
    recovery = (await client.get(f"{TRAINING}/recovery", headers=h)).json()
    assert recovery["status"] == "unavailable"
    assert recovery["code"] == "insufficient_data"


async def test_completed_ride_lands_on_a_daily_load_row(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    bike = await _bike(client, h)
    ride_id = await _power_ride(client, h, bike, minutes=35, watts=250)
    await client.post(f"{TRAINING}/activities/{ride_id}/reanalyze", headers=h)
    loads = (await client.get(f"{TRAINING}/loads", headers=h)).json()
    assert len(loads["items"]) == 1
    row = loads["items"][0]
    assert f(row["power_load"]) > 0
    assert row["load_version"] == "ewma_42_7_v1"
    assert f(row["ctl"]) > 0


async def test_summary_is_one_call_and_includes_evidence(client):
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    bike = await _bike(client, h)
    await _power_ride(client, h, bike, minutes=35, watts=250)
    body = (await client.get(f"{TRAINING}/summary", headers=h)).json()
    assert f(body["profile"]["ftp_w"]) == 250.0
    assert len(body["recent_activities"]) == 1
    # FTP was set before the ride, so this ride was derived with it.
    assert f(body["week_power_load"]) > 0
    assert body["recovery"]["code"] == "insufficient_data"
    assert "required_days" in body["recovery"]["evidence"]
    assert body["suggestion"]["status"] == "ok"
    assert f(body["suggestion"]["target_load"]) > 0


async def test_calculation_versions_expose_the_registry(client):
    h = await _user(client, A)
    body = (await client.get(f"{TRAINING}/calculation-versions", headers=h)).json()
    versions = {v["version"] for v in body["items"]}
    assert "coggan_7zone_v1" in versions
    assert "tss_style_v1" in versions
    np_version = next(v for v in body["items"] if v["version"] == "np_30s_v1")
    assert np_version["params"]["window_s"] == 30.0


async def test_load_range_validation(client):
    h = await _user(client, A)
    r = await client.get(f"{TRAINING}/loads?start=2026-05-10&end=2026-05-01", headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_RANGE"


# ---------------------------------------------------------------------------
# Ownership / IDOR — the security-critical part
# ---------------------------------------------------------------------------


async def test_other_riders_activities_are_not_readable(client):
    ha = await _user(client, A)
    hb = await _user(client, B)

    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=ha)
    bike = await _bike(client, ha)
    await _power_ride(client, ha, bike, minutes=35, watts=250)
    activity_id = (await client.get(f"{TRAINING}/activities", headers=ha)).json()["items"][0]["id"]

    r = await client.get(f"{TRAINING}/activities/{activity_id}", headers=hb)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACTIVITY_NOT_FOUND"


async def test_other_riders_profiles_are_separate(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=ha)
    assert (await client.get(f"{TRAINING}/profile", headers=hb)).json()["ftp_w"] is None


async def test_other_riders_workouts_are_not_readable(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    workout = await client.post(
        f"{WORKOUTS}",
        json={
            "name": "Threshold",
            "steps": [{"label": "On", "duration_s": 600, "step_type": "interval"}],
        },
        headers=ha,
    )
    assert workout.status_code == 201, workout.text
    r = await client.get(f"{WORKOUTS}/{workout.json()['id']}", headers=hb)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WORKOUT_NOT_FOUND"
    assert (await client.get(f"{WORKOUTS}", headers=hb)).json()["total"] == 0


async def test_reanalyze_another_riders_ride_is_a_404(client):
    ha = await _user(client, A)
    hb = await _user(client, B)
    bike = await _bike(client, ha)
    ride_id = await _ride(client, ha, bike)
    r = await client.post(f"{TRAINING}/activities/{ride_id}/reanalyze", headers=hb)
    assert r.status_code == 404


async def test_endpoints_require_authentication(client):
    for path in ("/profile", "/activities", "/loads", "/recovery", "/summary"):
        r = await client.get(f"{TRAINING}{path}")
        assert r.status_code == 401, path
    assert (await client.get(f"{WORKOUTS}")).status_code == 401


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------


async def test_create_read_update_delete_workout(client):
    h = await _user(client, A)
    r = await client.post(
        f"{WORKOUTS}",
        json={
            "name": "Sweet spot",
            "discipline": "road",
            "target_duration_s": 3600,
            "steps": [
                {"label": "Warm up", "duration_s": 600, "step_type": "warmup"},
                {
                    "label": "Sweet spot",
                    "duration_s": 1200,
                    "repeat_count": 3,
                    "step_type": "interval",
                    "target_zone": 3,
                    "target_power_low_w": 205,
                    "target_power_high_w": 240,
                },
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1
    assert [s["seq"] for s in body["steps"]] == [1, 2]
    assert body["steps"][1]["repeat_count"] == 3

    r = await client.patch(
        f"{WORKOUTS}/{body['id']}", json={"expected_version": 1, "name": "Sweet spot 3x"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert r.json()["name"] == "Sweet spot 3x"

    r = await client.delete(f"{WORKOUTS}/{body['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert (await client.get(f"{WORKOUTS}/{body['id']}", headers=h)).status_code == 404


async def test_workout_optimistic_locking(client):
    h = await _user(client, A)
    workout = (await client.post(f"{WORKOUTS}", json={"name": "Base"}, headers=h)).json()

    ok = await client.patch(
        f"{WORKOUTS}/{workout['id']}", json={"expected_version": 1, "goal": "endurance"}, headers=h
    )
    assert ok.status_code == 200

    stale = await client.patch(
        f"{WORKOUTS}/{workout['id']}", json={"expected_version": 1, "goal": "race"}, headers=h
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # The schema makes expected_version mandatory, so a blind edit is refused
    # before it reaches the service.
    missing = await client.patch(
        f"{WORKOUTS}/{workout['id']}", json={"name": "no version"}, headers=h
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_workout_steps_are_renumbered_on_replace(client):
    h = await _user(client, A)
    workout = (
        await client.post(
            f"{WORKOUTS}",
            json={
                "name": "Session",
                "steps": [{"label": "a", "duration_s": 60}, {"label": "b", "duration_s": 60}],
            },
            headers=h,
        )
    ).json()
    r = await client.patch(
        f"{WORKOUTS}/{workout['id']}",
        json={
            "expected_version": 1,
            "steps": [{"label": "only", "duration_s": 300, "step_type": "steady"}],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["steps"]) == 1
    assert r.json()["steps"][0]["seq"] == 1


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


async def test_a_long_ride_stays_inside_the_api_budget(client):
    """A 90 minute power ride must analyze and list in well under a second each."""
    h = await _user(client, A)
    await client.put(f"{TRAINING}/profile", json={"ftp_w": 250}, headers=h)
    bike = await _bike(client, h)
    ride_id = await _ride(client, h, bike)
    total = 90 * 60
    points = [
        pt(i, sec=i, power=180 + (i % 200), hr=130 + (i % 40), cad=88, dlat=STEP * 0.01 * i)
        for i in range(total)
    ]
    for start in range(0, total, 500):
        await _upload(client, h, ride_id, points[start : start + 500])

    started = time.perf_counter()
    finished = await client.post(f"{RIDES}/{ride_id}/finish", headers=h)
    assert finished.status_code == 200, finished.text
    finish_seconds = time.perf_counter() - started

    started = time.perf_counter()
    activities = await client.get(f"{TRAINING}/activities?page_size=20", headers=h)
    list_seconds = time.perf_counter() - started
    assert activities.status_code == 200

    activity = activities.json()["items"][0]
    assert activity["has_power"] is True
    assert activity["normalized_power_w"] is not None
    assert finish_seconds < 5.0, f"finish+analysis took {finish_seconds:.2f}s"
    assert list_seconds < 2.0, f"listing took {list_seconds:.2f}s"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _latest_ride(client, headers):
    activities = (await client.get(f"{TRAINING}/activities", headers=headers)).json()
    return activities["items"][0]["ride_id"]


async def test_the_registry_is_built_from_code_not_the_seeded_table(client):
    """A schema built without the migration seed must still serve the registry.

    The code registry is the source of truth; a missing seed row is a migration
    bug caught by the drift test, not something to paper over in the response.
    """
    h = await _user(client, A)
    versions = (await client.get(f"{TRAINING}/calculation-versions", headers=h)).json()["items"]
    assert len(versions) == 12
    assert all(v["is_active"] is True for v in versions)
