async def test_health_shape(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "cyclecoach-api"
    assert body["version"] == "1.0.0"
    assert body["database"] in ("up", "down")  # infra may be down in CI unit run
    assert body["redis"] in ("up", "down")
