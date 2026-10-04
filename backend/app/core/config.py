"""CycleCoach settings — typed, env-driven, no secrets committed."""

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The shipped development placeholder. Production must never use it.
_DEV_SECRET_KEY = "change-me-in-production-min-32-chars"
#: Minimum accepted production secret length (security policy).
_MIN_SECRET_LENGTH = 32


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
    ROUTING_PROVIDER: str = "none"  # none | osrm | graphhopper | mapbox | valhalla
    GEOCODING_PROVIDER: str = "none"  # none | nominatim | photon | mapbox
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
                "SECRET_KEY is missing: set a unique production secret " f"({self._secret_policy})."
            )
        if key == _DEV_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY is the shipped development default: set a unique "
                f"production secret ({self._secret_policy})."
            )
        if len(key) < _MIN_SECRET_LENGTH:
            raise RuntimeError(
                "SECRET_KEY is too short: set a unique production secret "
                f"({self._secret_policy})."
            )

    @property
    def _secret_policy(self) -> str:
        return f"at least {_MIN_SECRET_LENGTH} characters, unique per environment"


settings = Settings()
