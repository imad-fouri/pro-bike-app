"""CycleCoach settings — typed, env-driven, no secrets committed."""

from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The shipped development placeholder. Production must never use it.
_DEV_SECRET_KEY = "change-me-in-production-min-32-chars"
#: Minimum accepted production secret length (security policy).
_MIN_SECRET_LENGTH = 32
#: The shipped development database URL, credentials included. Public in the
#: repository, so production pointing at it is always a misconfiguration.
_DEV_DATABASE_URL = "postgresql+asyncpg://cyclecoach:cyclecoach@localhost:5432/cyclecoach"
#: Environments this application recognises. An unrecognised value is REJECTED
#: rather than treated as non-production: `ENVIRONMENT=prod` or `Production` would
#: otherwise silently disable every production check below, which is a fail-OPEN
#: on the single setting that guards all the others.
_KNOWN_ENVIRONMENTS = frozenset({"development", "test", "production"})
#: Longest accepted production access-token lifetime, in minutes.
#:
#: Account deletion has to be able to end a session promptly. A multi-day access
#: token means a deleted account keeps authenticating until it expires, so the
#: refresh flow — which is revocable — has to be the thing that carries identity.
_MAX_PRODUCTION_ACCESS_TOKEN_MINUTES = 60


#: Hosts that mean "this is a developer's machine".
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_loopback_origin(origin: str) -> bool:
    """Whether a CORS origin points at a developer's own machine.

    Parsed rather than substring-matched, so `https://localhost.evil.example`
    is correctly treated as a REMOTE host — a substring check on "localhost"
    would reject a legitimate domain and, worse, give false confidence about
    which origins are local.
    """
    try:
        parsed = urlparse(origin if "//" in origin else f"//{origin}")
    except ValueError:
        return False
    return (parsed.hostname or "") in _LOOPBACK_HOSTS


class ProductionConfigError(RuntimeError):
    """Raised when production configuration is unsafe to serve traffic on.

    Carries the full list of problems rather than only the first, because an
    operator fixing a deployment should see every fault in one restart instead of
    discovering them one crash at a time.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        detail = "; ".join(self.problems)
        super().__init__(
            f"unsafe production configuration ({len(self.problems)} problem(s)): {detail}"
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"  # development | test | production
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api/v1"
    APP_NAME: str = "cyclecoach-api"
    APP_VERSION: str = "1.0.0"

    DATABASE_URL: str = "postgresql+asyncpg://cyclecoach:cyclecoach@localhost:5432/cyclecoach"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production-min-32-chars"
    CORS_ORIGINS: str = "http://localhost:3000"
    # Which email implementation to bind. Only "dev" exists in this phase; a
    # future real provider registers its own name in `email.build_email_service`.
    EMAIL_PROVIDER: str = "dev"

    # Phase 2 — auth token lifetimes
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 30
    PASSWORD_RESET_MINUTES: int = 60
    EMAIL_VERIFY_HOURS: int = 24

    # Phase 5 — maps/routes (no secrets here; provider keys come from env)
    MAP_TILE_URL_TEMPLATE: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    MAP_ATTRIBUTION: str = "© OpenStreetMap contributors"
    MAP_MAX_ZOOM: int = 19
    ROUTING_PROVIDER: str = "osrm"  # none | osrm | graphhopper | mapbox | valhalla
    GEOCODING_PROVIDER: str = "nominatim"  # none | nominatim | photon | mapbox
    # Phase D — keyless public providers, proxied server-side. The defaults
    # below are development-grade (no signup, no quota): production must point
    # these at self-hosted or contracted endpoints (see the production gate).
    ROUTING_OSRM_BASE_URL: str = "https://router.project-osrm.org"
    GEOCODING_NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"
    ROUTING_OSRM_PROFILE: str = "bike"
    ROUTING_TIMEOUT_SECONDS: int = 15
    GEOCODING_TIMEOUT_SECONDS: int = 10
    GEOCODING_MAX_RESULTS: int = 5
    GEOCODING_CACHE_SECONDS: int = 86400
    ROUTING_MAX_WAYPOINTS: int = 25
    ROUTE_MAX_POINTS: int = 5000
    GPX_MAX_BYTES: int = 5 * 1024 * 1024
    GPX_MAX_NODES: int = 200000

    # Phase 7 — AI Coach. An explanation layer only: it reads deterministic
    # metrics produced by the training engine and never sources a number
    # itself (ADR-11). Disabled by default; the key never leaves the server.
    AI_ENABLED: bool = False
    AI_PROVIDER: str = "none"  # none | openai_compatible
    AI_MODEL: str = ""
    AI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_TIMEOUT_SECONDS: int = 20
    AI_MAX_INPUT_TOKENS: int = 4000
    AI_MAX_OUTPUT_TOKENS: int = 700
    AI_MAX_REQUESTS_PER_USER: int = 20
    AI_REQUEST_RETRY_LIMIT: int = 1
    AI_DAILY_COST_LIMIT: float = 1.0
    # USD per 1k tokens. Left at 0 the daily budget is not enforced, because an
    # invented price would be a fabricated number; the token caps do the real
    # bounding. Set them to enable the backstop.
    AI_INPUT_COST_PER_1K_TOKENS: float = 0.0
    AI_OUTPUT_COST_PER_1K_TOKENS: float = 0.0
    # Context bounds — the coach is never handed an unbounded payload.
    AI_MAX_MESSAGE_CHARS: int = 1000
    AI_MAX_CONTEXT_ACTIVITIES: int = 7

    # Phase 8.4 — push notification foundation. The provider seam exists and the
    # Fake provider is wired in by default; FCM and APNs are FUTURE and require
    # no credential, SDK, or native configuration here. `none` is the correct
    # value for every environment in this phase (ADR-15 §3).
    PUSH_PROVIDER: str = "none"  # none | fcm | apns (fcm/apns: FUTURE)
    PUSH_ENABLED: bool = False
    PUSH_TIMEOUT_SECONDS: int = 10
    # Only an *unavailable* provider is retried: an unusable response or a
    # timeout is not, because the delivery may already have happened.
    PUSH_RETRY_LIMIT: int = 2
    # Devices silent for longer than this are disabled by a sweep, so
    # push_devices does not grow monotonically with every reinstall.
    PUSH_DEVICE_STALE_DAYS: int = 90
    # Above this many recipients in ONE fan-out, push delivery is left to the
    # FUTURE worker rather than turning a single chat send into N synchronous
    # provider round-trips (ADR-15 A10). The notification ROWS are still written:
    # they are the record, and skipping them would lose events. Only delivery is
    # bounded.
    PUSH_INLINE_FANOUT_LIMIT: int = 20

    # WS-RP Phase 3/6 — App Store Server API verification. The adapter enters
    # the registry only when ENABLED and fully configured (see the adapter's
    # `configured()`); every field defaults off so the provider registry stays
    # empty and honest until a deployment is actually ready. None of the
    # secret values ever leave the server: they are read for signing the
    # caller JWT only, never logged, echoed, or labeled.
    APPLE_STORE_ENABLED: bool = False
    # Which App Store Server API base URL the SERVER decides to call:
    # "production" (api.storekit.apple.com) or "sandbox"
    # (api.storekit-sandbox.apple.com). The environment of a purchase is then
    # taken from the verified transaction's own signed `environment` field,
    # never from the client.
    APPLE_API_ENVIRONMENT: str = "production"
    APPLE_BUNDLE_ID: str = ""
    APPLE_ISSUER_ID: str = ""
    APPLE_KEY_ID: str = ""
    APPLE_PRIVATE_KEY_PATH: str = ""
    # PEM bundle of Apple's published App Store Server API root certificates,
    # the trust anchors the adapter validates every signed transaction JWS
    # chain against. Empty means no chain trust; verification fails closed.
    APPLE_ROOT_CERTS_PATH: str = ""
    APPLE_REQUEST_TIMEOUT_SECONDS: int = 10
    # Server-owned App Store product bindings. These provider-side IDs are
    # bound to the canonical internal PRO monthly/yearly products during
    # startup. Empty means the Apple provider cannot start completely.
    APPLE_PRODUCT_PRO_MONTHLY: str = ""
    APPLE_PRODUCT_PRO_YEARLY: str = ""

    # WS-RP Phase 4/6 — Google Play verification (purchases.subscriptionsv2.get).
    # Same discipline as Apple: the adapter enters the registry only when
    # ENABLED and fully configured; every field defaults off so the provider
    # registry stays empty and honest until a deployment is ready. The
    # service-account JSON is loaded server-side for the OAuth2 JWT assertion
    # only; it, the resulting access token, and purchase tokens are never
    # logged, labeled, echoed, or stored.
    GOOGLE_PLAY_ENABLED: bool = False
    # Android package/application identity the server enforces as the API
    # request path and refuses to grant under a foreign package.
    GOOGLE_PACKAGE_NAME: str = ""
    # Path to the service-account JSON (type=service_account). Never committed.
    GOOGLE_APPLICATION_CREDENTIALS_PATH: str = ""
    GOOGLE_REQUEST_TIMEOUT_SECONDS: int = 10
    # Server-owned Play product bindings, bound to the canonical internal PRO
    # monthly/yearly products during startup. Empty means the Google provider
    # cannot start completely.
    GOOGLE_PRODUCT_PRO_MONTHLY: str = ""
    GOOGLE_PRODUCT_PRO_YEARLY: str = ""

    # Phase 10 — store server notifications (webhooks). Both routes consume the
    # SAME provider adapters as verification (`process_event` -> the existing
    # apply_provider_event pipeline); these toggles only expose that path over
    # HTTP. Everything defaults OFF so no callback surface exists until an
    # operator explicitly enables it, matching the provider ENABLED discipline
    # above.
    APPLE_WEBHOOKS_ENABLED: bool = False
    GOOGLE_WEBHOOKS_ENABLED: bool = False
    # Pub/Sub push authentication for RTDN. "none" accepts any push (local
    # development only); "oidc" verifies the `Authorization: Bearer` OIDC push
    # token against Google's published JWKS before the envelope is decoded.
    # The production gate refuses anything but "oidc":
    GOOGLE_RTDN_AUTH_MODE: str = "none"  # none | oidc
    # The expected `aud` claim of the push token: the Pub/Sub subscription URL
    # (`projects/<project>/subscriptions/<subscription>`). Empty means the
    # verifier has nothing to require, so production gate fails.
    GOOGLE_RTDN_OIDC_AUDIENCE: str = ""
    # Expected `iss` claim of the push token. Pub/Sub issues its OIDC push
    # tokens from accounts.google.com by default.
    GOOGLE_RTDN_OIDC_ISSUER: str = "https://accounts.google.com"
    # Where the push-token signing keys are published. Google's public JWKS;
    # overridable for test/proxy setups.
    GOOGLE_RTDN_JWKS_URI: str = "https://www.googleapis.com/oauth2/v3/certs"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def ai_configured(self) -> bool:
        """True when a real provider is selected and can plausibly answer."""
        return self.AI_ENABLED and self.AI_PROVIDER != "none" and bool(self.AI_API_KEY)

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def require_production_secrets(self) -> None:
        """Fail closed: production must never sign tokens with a known key.

        Raises RuntimeError (never returns a fallback) when ENVIRONMENT is
        production and SECRET_KEY is missing, empty, the shipped development
        default, or shorter than policy. The messages name the failed check,
        never the key value.
        """
        if not self.is_production:
            return
        key = self.SECRET_KEY or ""
        if not key.strip():
            raise RuntimeError(
                f"SECRET_KEY is missing: set a unique production secret ({self._secret_policy})."
            )
        if key == _DEV_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY is the shipped development default: set a unique "
                f"production secret ({self._secret_policy})."
            )
        if len(key) < _MIN_SECRET_LENGTH:
            raise RuntimeError(
                f"SECRET_KEY is too short: set a unique production secret ({self._secret_policy})."
            )

    @property
    def _secret_policy(self) -> str:
        return f"at least {_MIN_SECRET_LENGTH} characters, unique per environment"

    # -----------------------------------------------------------------------
    # Phase 10 — production configuration gate (WS-B)
    # -----------------------------------------------------------------------

    def production_config_problems(self) -> list[str]:
        """Every reason this deployment must not serve production traffic.

        Returns a list rather than raising, so the caller decides when to fail and
        a test can assert on the full set. **Never includes a secret value** — only
        the name of the setting and how to fix it. A configuration error lands in
        a crash log and often in an orchestrator's visible output, so echoing the
        offending value would move the secret into a place it was never meant to
        reach.

        `require_production_secrets` deliberately stays separate and unchanged: it
        is the Phase 8.5 contract with its own tests, and this is the wider gate.
        """
        problems: list[str] = []

        # 1. An unrecognised ENVIRONMENT disables every other check, because
        #    `is_production` compares against the literal "production". A typo
        #    must fail loudly rather than serve a production database with
        #    development rules.
        if self.ENVIRONMENT not in _KNOWN_ENVIRONMENTS:
            problems.append(
                f"ENVIRONMENT is {self.ENVIRONMENT!r}, which is not one of "
                f"{sorted(_KNOWN_ENVIRONMENTS)} — production checks would be "
                "silently skipped"
            )
            return problems

        if not self.is_production:
            return problems

        # 2. SECRET_KEY. Same three checks as require_production_secrets, repeated
        #    here so the wide gate is complete on its own.
        key = self.SECRET_KEY or ""
        if not key.strip():
            problems.append("SECRET_KEY is missing")
        elif key == _DEV_SECRET_KEY:
            problems.append("SECRET_KEY is the shipped development default")
        elif len(key) < _MIN_SECRET_LENGTH:
            problems.append(f"SECRET_KEY is shorter than {_MIN_SECRET_LENGTH} characters")

        # 3. The dev email provider writes message bodies to the log instead of
        #    sending them. In production that both silently drops password-reset
        #    and email-verification mail AND puts live reset tokens in the logs.
        if self.EMAIL_PROVIDER == "dev":
            problems.append(
                "EMAIL_PROVIDER is 'dev', which logs message bodies instead of "
                "sending them — reset and verification links would never arrive "
                "and their tokens would land in the log"
            )

        # 4. CORS. A wildcard is rejected by browsers whenever credentials are
        #    allowed, so it is a misconfiguration even before it is an attack;
        #    cleartext origins would put bearer tokens on the wire in the clear.
        origins = self.cors_origin_list()
        if "*" in origins:
            problems.append(
                "CORS_ORIGINS contains '*', which cannot be combined with credentialed requests"
            )
        for origin in origins:
            if _is_loopback_origin(origin):
                # Checked separately from the cleartext rule below, because the
                # reason is different: https://localhost is perfectly encrypted
                # and still a development origin. Allowing it in production lets
                # any page an operator happens to run locally make credentialed
                # calls to the live API.
                problems.append(
                    f"CORS_ORIGINS entry {origin!r} is a loopback/development "
                    "origin, which has no place in a production allowlist"
                )
            elif not origin.startswith("https://"):
                problems.append(
                    f"CORS_ORIGINS entry {origin!r} is not https, so credentials "
                    "would cross the network in cleartext"
                )

        # 5. Debug logging in production is how request bodies and tokens end up
        #    in a log aggregator.
        if self.LOG_LEVEL.upper() == "DEBUG":
            problems.append("LOG_LEVEL is DEBUG, which risks logging request content")

        # 6. A production process pointed at the shipped local database is either
        #    a staging mistake or a deployment that will lose real rides.
        if self.DATABASE_URL == _DEV_DATABASE_URL:
            problems.append(
                "DATABASE_URL is the shipped development default (credentials "
                "included, and public in this repository)"
            )

        # 7. "Enabled" with no provider is a contradiction that silently means
        #    "disabled": an operator who believes AI is on would be wrong.
        if self.AI_ENABLED and self.AI_PROVIDER == "none":
            problems.append("AI_ENABLED is true but AI_PROVIDER is 'none'")
        if self.AI_ENABLED and not self.AI_API_KEY.strip():
            problems.append("AI_ENABLED is true but AI_API_KEY is empty")

        if self.PUSH_ENABLED and self.PUSH_PROVIDER == "none":
            problems.append("PUSH_ENABLED is true but PUSH_PROVIDER is 'none'")

        # 8. Redis holds consented live rider positions. An unauthenticated
        #    connection to a REMOTE Redis would expose them to anyone who can
        #    reach the port. A localhost sidecar needs no credential and stays
        #    allowed.
        if self._redis_is_remote_without_auth():
            problems.append(
                "REDIS_URL points at a remote host with no password, and Redis "
                "holds live rider positions"
            )

        # 9. A long-lived access token outlives account deletion. See the
        #    constant for the reasoning.
        if self.ACCESS_TOKEN_MINUTES > _MAX_PRODUCTION_ACCESS_TOKEN_MINUTES:
            problems.append(
                f"ACCESS_TOKEN_MINUTES is {self.ACCESS_TOKEN_MINUTES}, above the "
                f"production maximum of {_MAX_PRODUCTION_ACCESS_TOKEN_MINUTES} — a "
                "deleted account would keep authenticating until it expires"
            )

        # 10. Store providers enabled without their bootstrap requirements. A
        #     webhook for a provider the deployment has not configured is a
        #     contradiction that silently serves 503s; Google's push mode
        #     without OIDC auth accepts ANY push that can reach the URL, which
        #     is a fail-open in production. Each is listed by name only, never
        #     by secret value.
        if self.APPLE_STORE_ENABLED:
            missing = [
                name
                for name, value in (
                    ("APPLE_ISSUER_ID", self.APPLE_ISSUER_ID),
                    ("APPLE_KEY_ID", self.APPLE_KEY_ID),
                    ("APPLE_PRIVATE_KEY_PATH", self.APPLE_PRIVATE_KEY_PATH),
                    ("APPLE_BUNDLE_ID", self.APPLE_BUNDLE_ID),
                    ("APPLE_ROOT_CERTS_PATH", self.APPLE_ROOT_CERTS_PATH),
                    ("APPLE_PRODUCT_PRO_MONTHLY", self.APPLE_PRODUCT_PRO_MONTHLY),
                    ("APPLE_PRODUCT_PRO_YEARLY", self.APPLE_PRODUCT_PRO_YEARLY),
                )
                if not str(value or "").strip()
            ]
            if self.APPLE_API_ENVIRONMENT not in ("production", "sandbox"):
                missing.append("APPLE_API_ENVIRONMENT")
            if missing:
                problems.append(
                    "APPLE_STORE_ENABLED is true but required Apple bootstrap "
                    f"settings are missing: {', '.join(missing)}"
                )
        if self.GOOGLE_PLAY_ENABLED:
            missing = [
                name
                for name, value in (
                    (
                        "GOOGLE_APPLICATION_CREDENTIALS_PATH",
                        self.GOOGLE_APPLICATION_CREDENTIALS_PATH,
                    ),
                    ("GOOGLE_PACKAGE_NAME", self.GOOGLE_PACKAGE_NAME),
                    ("GOOGLE_PRODUCT_PRO_MONTHLY", self.GOOGLE_PRODUCT_PRO_MONTHLY),
                    ("GOOGLE_PRODUCT_PRO_YEARLY", self.GOOGLE_PRODUCT_PRO_YEARLY),
                )
                if not str(value or "").strip()
            ]
            if missing:
                problems.append(
                    "GOOGLE_PLAY_ENABLED is true but required Google bootstrap "
                    f"settings are missing: {', '.join(missing)}"
                )
        if self.APPLE_WEBHOOKS_ENABLED and not self.APPLE_STORE_ENABLED:
            problems.append(
                "APPLE_WEBHOOKS_ENABLED is true but APPLE_STORE_ENABLED is "
                "false — an App Store webhook can only be trusted when the "
                "adapter is configured"
            )
        if self.GOOGLE_WEBHOOKS_ENABLED and not self.GOOGLE_PLAY_ENABLED:
            problems.append(
                "GOOGLE_WEBHOOKS_ENABLED is true but GOOGLE_PLAY_ENABLED is "
                "false — a Play RTDN webhook can only be trusted when the "
                "adapter is configured"
            )
        if self.GOOGLE_WEBHOOKS_ENABLED and self.GOOGLE_RTDN_AUTH_MODE != "oidc":
            problems.append(
                "GOOGLE_WEBHOOKS_ENABLED is true but GOOGLE_RTDN_AUTH_MODE is "
                "not 'oidc' — unauthenticated push callbacks must not be "
                "accepted in production"
            )
        if self.GOOGLE_RTDN_AUTH_MODE == "oidc" and not self.GOOGLE_RTDN_OIDC_AUDIENCE.strip():
            problems.append(
                "GOOGLE_RTDN_AUTH_MODE is 'oidc' but GOOGLE_RTDN_OIDC_AUDIENCE "
                "is empty — the push token has no audience to require"
            )

        # 11. Keyless demo routing/geocoding endpoints are development-grade:
        # their terms discourage production-app traffic, so a production
        # deployment must point at self-hosted or contracted endpoints (or
        # disable the providers). Each is listed by name only.
        if self.ROUTING_PROVIDER == "osrm" and self._is_demo_url(
            self.ROUTING_OSRM_BASE_URL, "router.project-osrm.org"
        ):
            problems.append(
                "ROUTING_PROVIDER is 'osrm' but ROUTING_OSRM_BASE_URL is the "
                "public demo server — point it at a self-hosted OSRM instance "
                "for production traffic"
            )
        if self.GEOCODING_PROVIDER == "nominatim" and self._is_demo_url(
            self.GEOCODING_NOMINATIM_BASE_URL, "nominatim.openstreetmap.org"
        ):
            problems.append(
                "GEOCODING_PROVIDER is 'nominatim' but "
                "GEOCODING_NOMINATIM_BASE_URL is the public demo server — "
                "point it at a self-hosted Nominatim instance for production "
                "traffic"
            )

        return problems

    def _redis_is_remote_without_auth(self) -> bool:
        try:
            parsed = urlparse(self.REDIS_URL)
        except ValueError:
            return False
        host = parsed.hostname or ""
        if not host or host in _LOOPBACK_HOSTS:
            return False
        return not parsed.password

    @staticmethod
    def _is_demo_url(url: str, demo_host: str) -> bool:
        """Whether a provider base URL is the public demo deployment."""
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        return host == demo_host or host.endswith(f".{demo_host}")

    def validate_production(self) -> None:
        """Fail closed before serving traffic.

        A no-op outside production, so tests and local development need no
        ceremony. Raises [ProductionConfigError] listing EVERY problem found.
        """
        problems = self.production_config_problems()
        if problems:
            raise ProductionConfigError(problems)


settings = Settings()
