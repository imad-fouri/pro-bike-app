from app.core.config import Settings, settings


def test_config_defaults():
    assert settings.APP_NAME == "cyclecoach-api"
    assert settings.API_PREFIX == "/api/v1"
    assert len(settings.SECRET_KEY) >= 8
    assert isinstance(settings.cors_origin_list(), list)


def test_error_envelope():
    from app.core.errors import error_body

    body = error_body("NOT_FOUND", "Missing", {"id": 1})
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"] == {"id": 1}


async def test_db_redis_abstraction_returns_bool():
    from app.db.session import check_db
    from app.redis.client import check_redis

    assert await check_db() in (True, False)
    assert await check_redis() in (True, False)


# ---------------------------------------------------------------------------
# Production secret policy (Phase 8.5 remediation)
# ---------------------------------------------------------------------------


def _production_settings(secret: str) -> Settings:
    return Settings(ENVIRONMENT="production", SECRET_KEY=secret)


def test_production_rejects_missing_secret():
    import pytest

    with pytest.raises(RuntimeError, match="missing"):
        _production_settings("").require_production_secrets()
    with pytest.raises(RuntimeError, match="missing"):
        _production_settings("   ").require_production_secrets()


def test_production_rejects_development_default():
    import pytest

    from app.core.config import _DEV_SECRET_KEY

    with pytest.raises(RuntimeError, match="development default"):
        _production_settings(_DEV_SECRET_KEY).require_production_secrets()


def test_production_rejects_short_secret():
    import pytest

    with pytest.raises(RuntimeError, match="too short"):
        _production_settings("short-key").require_production_secrets()


def test_production_accepts_valid_secret():
    valid = "a" * 64
    _production_settings(valid).require_production_secrets()


def test_production_errors_never_echo_the_secret():
    import pytest

    for secret in ("", "short-key"):
        with pytest.raises(RuntimeError) as exc:
            _production_settings(secret).require_production_secrets()
        assert secret not in str(exc.value) or not secret


def test_non_production_allows_development_default():
    Settings(ENVIRONMENT="development").require_production_secrets()
    Settings(ENVIRONMENT="test").require_production_secrets()


def test_production_boot_fails_without_secret():
    import pytest

    import app.main as main_module
    from app.main import create_app

    insecure = Settings(ENVIRONMENT="production", SECRET_KEY="change-me-in-production-min-32-chars")
    assert insecure.is_production
    # create_app enforces the secret check before serving anything. `settings`
    # is bound into the main module namespace at import, so patch it there.
    original = main_module.settings
    main_module.settings = insecure
    try:
        with pytest.raises(RuntimeError, match="development default"):
            create_app()
    finally:
        main_module.settings = original


# ---------------------------------------------------------------------------
# Email provider selection (Phase 8.5 remediation)
# ---------------------------------------------------------------------------


def test_dev_email_service_is_bounded():
    from app.services.email import DevEmailService, OutboxMessage

    service = DevEmailService()
    for i in range(DevEmailService.MAX_OUTBOX_MESSAGES + 50):
        service.send(OutboxMessage(to="r@example.com", subject="t", body=f"token-{i}"))
    assert len(service.outbox) == DevEmailService.MAX_OUTBOX_MESSAGES
    # Oldest evicted, newest retained: bounded, never indefinite.
    assert service.outbox[0].body == "token-50"
    assert service.outbox[-1].body == f"token-{DevEmailService.MAX_OUTBOX_MESSAGES + 49}"


def test_email_selection_rejects_production_without_provider():
    import pytest

    from app.services.email import build_email_service

    with pytest.raises(RuntimeError, match="No email provider"):
        build_email_service(environment="production", provider="dev")
    with pytest.raises(RuntimeError, match="No email provider"):
        build_email_service(environment="production", provider="smtp")


def test_email_selection_rejects_unknown_provider():
    import pytest

    from app.services.email import DevEmailService, build_email_service

    with pytest.raises(RuntimeError, match="Unknown email provider"):
        build_email_service(environment="development", provider="smtp")
    assert isinstance(
        build_email_service(environment="development", provider="dev"), DevEmailService
    )
    assert isinstance(build_email_service(environment="test", provider="dev"), DevEmailService)
