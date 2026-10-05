"""Phase 10 production-configuration gate (WS-B, ADR-17).

A production deployment is the one configuration nobody is watching, so the
checks here exist to make an unsafe default impossible to ship by accident. They
are grouped by the class of mistake they prevent:

1. **Fail-open on the guard itself.** An unrecognised `ENVIRONMENT` used to
   disable every production check silently.
2. **Secrets.** Placeholder, short, and — the reason the gate reports a list —
   several problems at once.
3. **A development default reaching production.** The dev email provider, the
   shipped database URL, DEBUG logging.
4. **Transport and network exposure.** Cleartext CORS, wildcard CORS,
   unauthenticated remote Redis holding live rider positions.
5. **Contradictory feature flags.** "Enabled" with no provider means an operator
   believes a subsystem is on when it is not.
6. **Revocability.** An access token that outlives account deletion.

Nothing here reaches a database, a network, or a secret store: the gate is a pure
function of the settings object, which is what makes it safe to assert on.
"""

import pytest

from app.core.config import (
    _DEV_DATABASE_URL,
    _DEV_SECRET_KEY,
    ProductionConfigError,
    Settings,
)

#: A configuration that must pass every check. Tests start from this and break
#: exactly one thing, so a failure names one rule instead of a pile.
_GOOD_SECRET = "k" * 64


def production(**overrides) -> Settings:
    """A production [Settings] that is safe, minus whatever `overrides` breaks.

    Every field the gate inspects is set explicitly rather than relying on the
    class defaults, so a future change to a default cannot silently make this
    helper unsafe and quietly stop testing what it claims to.
    """
    base = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": _GOOD_SECRET,
        "EMAIL_PROVIDER": "smtp",
        "CORS_ORIGINS": "https://app.cyclecoach.app",
        "LOG_LEVEL": "INFO",
        "DATABASE_URL": "postgresql+asyncpg://app:realpassword@db.internal:5432/cyclecoach",
        "REDIS_URL": "redis://:realpassword@redis.internal:6379/0",
        "AI_ENABLED": False,
        "AI_PROVIDER": "none",
        "AI_API_KEY": "",
        "PUSH_ENABLED": False,
        "PUSH_PROVIDER": "none",
        "ACCESS_TOKEN_MINUTES": 15,
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# The safe baseline
# ---------------------------------------------------------------------------


def test_a_correctly_configured_production_deployment_passes():
    production().validate_production()


def test_a_safe_production_deployment_reports_no_problems():
    assert production().production_config_problems() == []


def test_development_and_test_are_never_gated():
    # Local work and CI must not have to configure a production secret. If this
    # ever fails, the gate has started blocking people who are not deploying.
    for environment in ("development", "test"):
        Settings(ENVIRONMENT=environment, SECRET_KEY="").validate_production()
        assert Settings(ENVIRONMENT=environment, SECRET_KEY="").production_config_problems() == []


# ---------------------------------------------------------------------------
# Rule 1 — the guard must not fail open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environment",
    ["prod", "Production", "PRODUCTION", "staging", "prodction", "", "  "],
)
def test_an_unrecognised_environment_is_rejected(environment):
    # `is_production` compares against the literal "production", so a typo used to
    # skip every production check while still serving real traffic.
    settings = Settings(ENVIRONMENT=environment, SECRET_KEY="")
    problems = settings.production_config_problems()
    assert problems, f"{environment!r} must be rejected"
    assert any("ENVIRONMENT" in p for p in problems)


def test_an_unrecognised_environment_reports_the_environment_check_first():
    # It returns early: once ENVIRONMENT is unknown, `is_production` is False and
    # every later check would be skipped, so listing them would be misleading.
    problems = Settings(ENVIRONMENT="prod", SECRET_KEY="").production_config_problems()
    assert len(problems) == 1


def test_the_unknown_environment_case_is_the_only_one_reported_for_a_typo():
    settings = Settings(ENVIRONMENT="staging", SECRET_KEY="")
    with pytest.raises(ProductionConfigError) as exc:
        settings.validate_production()
    assert exc.value.problems == settings.production_config_problems()


# ---------------------------------------------------------------------------
# Rule 2 — secrets
# ---------------------------------------------------------------------------


def test_production_rejects_a_missing_secret():
    assert any(
        "SECRET_KEY is missing" in p for p in production(SECRET_KEY="").production_config_problems()
    )


def test_production_rejects_a_whitespace_secret():
    assert any(
        "SECRET_KEY is missing" in p
        for p in production(SECRET_KEY="   ").production_config_problems()
    )


def test_production_rejects_the_shipped_development_placeholder():
    problems = production(SECRET_KEY=_DEV_SECRET_KEY).production_config_problems()
    assert any("development default" in p for p in problems)


def test_production_rejects_a_short_secret():
    problems = production(SECRET_KEY="short-key").production_config_problems()
    assert any("shorter than" in p for p in problems)


def test_the_phase_8_5_secret_gate_still_holds_independently():
    # The narrow gate is kept because it is the Phase 8.5 contract with its own
    # tests. Widening the gate must not quietly change what it accepts.
    for secret in ("", _DEV_SECRET_KEY, "short-key"):
        with pytest.raises(RuntimeError):
            production(SECRET_KEY=secret).require_production_secrets()
    production().require_production_secrets()


def test_every_problem_is_reported_at_once():
    # An operator fixing a deployment should see every fault in one restart, not
    # discover them one crash at a time.
    settings = production(
        SECRET_KEY="",
        EMAIL_PROVIDER="dev",
        LOG_LEVEL="DEBUG",
        CORS_ORIGINS="*",
    )
    problems = settings.production_config_problems()
    assert len(problems) >= 4


def test_the_error_names_every_problem_and_its_count():
    with pytest.raises(ProductionConfigError) as exc:
        production(SECRET_KEY="", EMAIL_PROVIDER="dev").validate_production()
    assert "2 problem(s)" in str(exc.value)
    assert len(exc.value.problems) == 2


def test_the_error_never_echoes_a_secret_value():
    # A configuration error reaches a crash log and often an orchestrator's
    # visible output, so echoing the value would move the secret somewhere it was
    # never meant to go.
    #
    # The secret here is deliberately too SHORT, so it is itself one of the
    # reported problems. That is the interesting case: the message must name the
    # setting and the policy, never the rejected value.
    secret = "LEAKCANARY-too-short"
    with pytest.raises(ProductionConfigError) as exc:
        production(SECRET_KEY=secret, EMAIL_PROVIDER="dev").validate_production()
    assert "LEAKCANARY" not in str(exc.value)
    assert "LEAKCANARY" not in " ".join(exc.value.problems)
    # The setting is still named, so the message stays actionable.
    assert "SECRET_KEY" in str(exc.value)


def test_a_valid_long_secret_is_not_reported_at_all():
    # The control for the test above: the canary is absent because the gate
    # ignores valid secrets, not because it echoed nothing.
    assert production(SECRET_KEY=_GOOD_SECRET).production_config_problems() == []


def test_the_error_does_not_echo_the_database_password():
    url = "postgresql+asyncpg://leaked:hunter2@db.internal:5432/cyclecoach"
    with pytest.raises(ProductionConfigError) as exc:
        production(
            AI_ENABLED=True,
            AI_PROVIDER="openai_compatible",
            AI_API_KEY="k",
            ACCESS_TOKEN_MINUTES=999,
            DATABASE_URL=url,
        ).validate_production()
    assert "hunter2" not in str(exc.value)


# ---------------------------------------------------------------------------
# Rule 3 — a development default reaching production
# ---------------------------------------------------------------------------


def test_production_rejects_the_development_email_provider():
    # The dev provider logs message bodies instead of sending them: reset and
    # verification mail would never arrive, and live reset tokens would land in
    # the log.
    problems = production(EMAIL_PROVIDER="dev").production_config_problems()
    assert any("EMAIL_PROVIDER" in p for p in problems)


def test_the_email_problem_explains_the_consequence():
    problems = production(EMAIL_PROVIDER="dev").production_config_problems()
    email_problem = next(p for p in problems if "EMAIL_PROVIDER" in p)
    # A message that only says "invalid" leaves the operator guessing.
    assert "log" in email_problem


def test_production_rejects_the_shipped_database_url():
    problems = production(DATABASE_URL=_DEV_DATABASE_URL).production_config_problems()
    assert any("DATABASE_URL" in p for p in problems)


def test_production_rejects_debug_logging():
    problems = production(LOG_LEVEL="DEBUG").production_config_problems()
    assert any("LOG_LEVEL" in p for p in problems)


def test_debug_logging_is_rejected_case_insensitively():
    assert production(LOG_LEVEL="debug").production_config_problems()
    assert production(LOG_LEVEL="Debug").production_config_problems()


@pytest.mark.parametrize("level", ["INFO", "WARNING", "ERROR", "CRITICAL"])
def test_production_accepts_every_sane_log_level(level):
    assert production(LOG_LEVEL=level).production_config_problems() == []


# ---------------------------------------------------------------------------
# Rule 4 — transport and network exposure
# ---------------------------------------------------------------------------


def test_production_rejects_a_wildcard_cors_origin():
    # Browsers refuse a wildcard whenever credentials are allowed, so it is a
    # misconfiguration even before it becomes an attack.
    problems = production(CORS_ORIGINS="*").production_config_problems()
    assert any("CORS_ORIGINS" in p and "*" in p for p in problems)


def test_production_rejects_cleartext_cors_origins():
    # Bearer tokens would cross the network in the clear.
    problems = production(CORS_ORIGINS="http://app.cyclecoach.app").production_config_problems()
    assert any("https" in p for p in problems)


def test_production_rejects_a_localhost_cors_origin():
    # https://localhost is encrypted and still a development origin. Allowing it
    # in production lets any page an operator happens to run locally make
    # credentialed calls to the live API.
    problems = production(CORS_ORIGINS="https://localhost:3000").production_config_problems()
    assert any("loopback/development" in p for p in problems)


def test_a_remote_host_that_merely_contains_localhost_is_allowed():
    # The control for the test above. A substring check on "localhost" would
    # reject this legitimate domain AND give false confidence about which origins
    # are treated as local.
    settings = production(CORS_ORIGINS="https://localhost.evil.example")
    assert settings.production_config_problems() == []


def test_a_real_development_origin_is_still_rejected():
    settings = production(CORS_ORIGINS="https://app.cyclecoach.app,http://localhost:3000")
    problems = settings.production_config_problems()
    assert any("localhost:3000" in p for p in problems)


def test_production_accepts_several_https_origins():
    settings = production(CORS_ORIGINS="https://app.cyclecoach.app,https://admin.cyclecoach.app")
    assert settings.cors_origin_list() == [
        "https://app.cyclecoach.app",
        "https://admin.cyclecoach.app",
    ]
    assert settings.production_config_problems() == []


def test_production_rejects_an_unauthenticated_remote_redis():
    # Redis holds consented live rider positions. No password on a remote host
    # means anyone who can reach the port can read them.
    problems = production(REDIS_URL="redis://redis.internal:6379/0").production_config_problems()
    assert any("REDIS_URL" in p for p in problems)


def test_production_accepts_an_authenticated_remote_redis():
    assert (
        production(REDIS_URL="rediss://:hunter2@redis.internal:6379/0").production_config_problems()
        == []
    )


def test_production_accepts_a_passwordless_local_redis_sidecar():
    # A localhost sidecar is not network-exposed, so no credential is required.
    assert production(REDIS_URL="redis://localhost:6379/0").production_config_problems() == []


def test_production_accepts_a_localhost_redis_sidecar_over_tls():
    assert production(REDIS_URL="rediss://localhost:6379/0").production_config_problems() == []


# ---------------------------------------------------------------------------
# Rule 5 — contradictory feature flags
# ---------------------------------------------------------------------------


def test_ai_enabled_with_no_provider_is_rejected():
    # "Enabled" with nothing to call is a contradiction that reads as working.
    problems = production(AI_ENABLED=True, AI_PROVIDER="none").production_config_problems()
    assert any("AI_ENABLED" in p for p in problems)


def test_ai_enabled_with_no_api_key_is_rejected():
    problems = production(
        AI_ENABLED=True, AI_PROVIDER="openai_compatible", AI_API_KEY=""
    ).production_config_problems()
    assert any("AI_API_KEY" in p for p in problems)


def test_a_fully_configured_ai_deployment_passes():
    assert (
        production(
            AI_ENABLED=True,
            AI_PROVIDER="openai_compatible",
            AI_API_KEY="a-real-key",
            AI_MODEL="gpt-4o-mini",
        ).production_config_problems()
        == []
    )


def test_ai_disabled_with_a_stale_key_is_accepted():
    # AI turned off with a leftover key is untidy, not unsafe: nothing calls it.
    assert (
        production(
            AI_ENABLED=False, AI_PROVIDER="none", AI_API_KEY="leftover"
        ).production_config_problems()
        == []
    )


def test_push_enabled_with_no_provider_is_rejected():
    problems = production(PUSH_ENABLED=True, PUSH_PROVIDER="none").production_config_problems()
    assert any("PUSH_ENABLED" in p for p in problems)


# ---------------------------------------------------------------------------
# Rule 6 — revocability
# ---------------------------------------------------------------------------


def test_production_rejects_an_access_token_longer_than_an_hour():
    # Account deletion has to be able to end a session promptly.
    problems = production(ACCESS_TOKEN_MINUTES=1440).production_config_problems()
    assert any("ACCESS_TOKEN_MINUTES" in p for p in problems)


def test_the_access_token_limit_is_inclusive():
    assert production(ACCESS_TOKEN_MINUTES=60).production_config_problems() == []


def test_the_refresh_token_may_outlive_the_access_token():
    # The refresh token is the revocable credential, so a long refresh lifetime is
    # the intended design rather than a mistake.
    assert (
        production(ACCESS_TOKEN_MINUTES=15, REFRESH_TOKEN_DAYS=30).production_config_problems()
        == []
    )


# ---------------------------------------------------------------------------
# The gate is wired into the process
# ---------------------------------------------------------------------------


def test_the_app_factory_calls_the_production_gate():
    # A gate nothing calls is documentation. Assert the call site exists so a
    # refactor that drops it fails here rather than in production.
    import inspect

    from app.main import create_app

    source = inspect.getsource(create_app)
    assert "validate_production" in source
    assert "require_production_secrets" not in source


def test_a_safe_production_settings_object_builds_an_app():
    from app.main import create_app

    # Proves the gate does not reject a legitimate production configuration. The
    # app is built but no server is started and no request is made.
    assert create_app() is not None
