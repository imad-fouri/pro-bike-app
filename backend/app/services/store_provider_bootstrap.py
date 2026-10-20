"""Secure production bootstrap for Apple and Google store providers.

Live configuration alone must never look ready while the adapters are
absent: both webhook routes resolve their provider through
:func:`purchase_verification.provider_for`, so an enabled setting without a
registered adapter would serve 503s. This module is the one production path
that turns validated settings into registered adapters and server-owned
provider products.

The bootstrap is deliberately narrow:

* It is called once during application startup, after
  ``Settings.validate_production`` and before routes accept traffic.
* It either registers a completely valid provider stack or raises
  :class:`StoreBootstrapError` before the application serves requests.
* It reads the process-wide ``Settings`` object, matching the provider
  adapters and webhook routes. Tests configure that object with monkeypatched
  temporary values; production configures it through the environment.
* Disabled providers are an explicit no-op: neither an adapter nor products
  are registered for them.
* A successful store-bearing bootstrap latches process-wide. A repeated
  successful initialization is rejected rather than silently registering the
  same adapters and products twice. A failed bootstrap does not latch, so an
  operator can correct the configuration and restart cleanly.
* ``reset_store_bootstrap_state`` is test-only. Production never calls it;
  tests need it because registration registries are intentionally global.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.logging import redact
from app.models.subscription import Plan, SubscriptionProvider
from app.services.apple_store_provider import AppleStoreProvider
from app.services.google_play_provider import GoogleStoreProvider
from app.services.purchase_verification import register_provider
from app.services.store_products import (
    CYCLECOACH_PRO_MONTHLY,
    CYCLECOACH_PRO_YEARLY,
    ProductPlatform,
    ProductType,
    ProviderProduct,
    plan_for_product,
    provider_product_for,
    register_provider_product,
)

log = logging.getLogger("cyclecoach")

#: Only these App Store Server API environments may be selected by the server.
_APPLE_ENVIRONMENTS = ("production", "sandbox")

#: One PEM certificate block. A root bundle may contain several blocks, and
#: the loader preserves each block verbatim for the adapter.
_PEM_CERTIFICATE_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)

#: A repeated successful bootstrap is a configuration/lifecycle bug, not a
#: second registration. The flag is process-global because the provider and
#: product registries are process-global too.
_BOOTSTRAPPED = False


class StoreBootstrapError(RuntimeError):
    """Store bootstrap configuration is missing, malformed, or repeated.

    Every problem names a configuration field or lifecycle violation. Secret
    values, file contents, tokens, and credentials are never included.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        detail = "; ".join(self.problems)
        super().__init__(
            f"store provider bootstrap failed ({len(self.problems)} problem(s)): {detail}"
        )


@dataclass(frozen=True)
class StoreBootstrapResult:
    """What one successful bootstrap registered."""

    apple_provider_registered: bool = False
    google_provider_registered: bool = False
    apple_products: tuple[str, ...] = ()
    google_products: tuple[str, ...] = ()


def reset_store_bootstrap_state() -> None:
    """Clear the one-shot bootstrap latch. Test-only.

    Production processes boot once and never reset this flag. Tests need the
    reset because they exercise both the success and duplicate-initialization
    paths in one process. This intentionally does not clear the provider or
    product registries; the existing ``reset_providers`` and
    ``reset_provider_products`` hooks own those registries.
    """
    global _BOOTSTRAPPED
    _BOOTSTRAPPED = False


def bootstrap_store_providers() -> StoreBootstrapResult:
    """Validate process settings and register the configured store-provider stack."""
    global _BOOTSTRAPPED

    effective = default_settings
    apple_enabled = effective.APPLE_STORE_ENABLED
    google_enabled = effective.GOOGLE_PLAY_ENABLED
    if not apple_enabled and not google_enabled:
        # Disabled is an explicit, safe no-op. It does not engage the
        # one-shot latch, so ordinary application factories and test fixtures
        # can be constructed repeatedly without store state.
        return StoreBootstrapResult()
    if _BOOTSTRAPPED:
        raise StoreBootstrapError(["store provider bootstrap has already run"])

    apple_adapter, apple_products = _prepare_apple(effective) if apple_enabled else (None, ())
    google_adapter, google_products = _prepare_google(effective) if google_enabled else (None, ())
    _check_cross_provider_products(
        tuple(product.provider_product_id for product in apple_products),
        tuple(product.provider_product_id for product in google_products),
    )

    # Register only after every enabled provider validated successfully. A
    # failure therefore leaves no partially registered production stack.
    if apple_adapter is not None:
        register_provider(apple_adapter)
        for product in apple_products:
            register_provider_product(product)
    if google_adapter is not None:
        register_provider(google_adapter)
        for product in google_products:
            register_provider_product(product)

    result = StoreBootstrapResult(
        apple_provider_registered=apple_adapter is not None,
        google_provider_registered=google_adapter is not None,
        apple_products=tuple(product.provider_product_id for product in apple_products),
        google_products=tuple(product.provider_product_id for product in google_products),
    )
    _log_bootstrap(result)
    _BOOTSTRAPPED = True
    return result


def _problem(field: str, reason: str) -> StoreBootstrapError:
    return StoreBootstrapError([f"{field} {reason}"])


def _require_value(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise _problem(field, "is required when its provider is enabled")
    return text


def _prepare_apple(
    effective: Settings,
) -> tuple[AppleStoreProvider, tuple[ProviderProduct, ...]]:
    """Validate Apple settings, trust material, and product bindings."""
    issuer_id = _require_value(effective.APPLE_ISSUER_ID, "APPLE_ISSUER_ID")
    key_id = _require_value(effective.APPLE_KEY_ID, "APPLE_KEY_ID")
    bundle_id = _require_value(effective.APPLE_BUNDLE_ID, "APPLE_BUNDLE_ID")
    key_path = _require_value(effective.APPLE_PRIVATE_KEY_PATH, "APPLE_PRIVATE_KEY_PATH")
    root_path = _require_value(effective.APPLE_ROOT_CERTS_PATH, "APPLE_ROOT_CERTS_PATH")
    environment = _require_value(effective.APPLE_API_ENVIRONMENT, "APPLE_API_ENVIRONMENT")
    if environment not in _APPLE_ENVIRONMENTS:
        raise _problem("APPLE_API_ENVIRONMENT", "must be 'production' or 'sandbox'")

    # Validate the configured credential and trust material by parsing it.
    # The parsed bytes stay in memory with the adapter; failures identify the
    # setting without exposing key or certificate contents.
    roots = _read_apple_root_certificates(root_path)
    private_key = _read_apple_private_key(key_path)
    adapter = AppleStoreProvider(http=None, root_certs=roots, private_key_pem=private_key)

    products = _bind_provider_products(
        SubscriptionProvider.APP_STORE,
        ProductPlatform.IOS,
        (
            (
                _require_value(effective.APPLE_PRODUCT_PRO_MONTHLY, "APPLE_PRODUCT_PRO_MONTHLY"),
                CYCLECOACH_PRO_MONTHLY,
            ),
            (
                _require_value(effective.APPLE_PRODUCT_PRO_YEARLY, "APPLE_PRODUCT_PRO_YEARLY"),
                CYCLECOACH_PRO_YEARLY,
            ),
        ),
    )
    _ensure_provider_ids_are_distinct(
        [product.provider_product_id for product in products], "APPLE_PRODUCT_PRO_MONTHLY"
    )

    _log_bootstrap_provider(
        provider=SubscriptionProvider.APP_STORE.value,
        products=[product.provider_product_id for product in products],
        bundle_id=bundle_id,
        issuer_id=issuer_id,
        key_id=key_id,
        environment=environment,
    )
    return adapter, products


def _prepare_google(
    effective: Settings,
) -> tuple[GoogleStoreProvider, tuple[ProviderProduct, ...]]:
    """Validate Google settings, credentials, and product bindings."""
    credentials_path = _require_value(
        effective.GOOGLE_APPLICATION_CREDENTIALS_PATH,
        "GOOGLE_APPLICATION_CREDENTIALS_PATH",
    )
    package_name = _require_value(effective.GOOGLE_PACKAGE_NAME, "GOOGLE_PACKAGE_NAME")
    credentials = _read_google_service_account(credentials_path)
    adapter = GoogleStoreProvider(http=None, credentials=credentials)

    products = _bind_provider_products(
        SubscriptionProvider.PLAY_STORE,
        ProductPlatform.ANDROID,
        (
            (
                _require_value(effective.GOOGLE_PRODUCT_PRO_MONTHLY, "GOOGLE_PRODUCT_PRO_MONTHLY"),
                CYCLECOACH_PRO_MONTHLY,
            ),
            (
                _require_value(effective.GOOGLE_PRODUCT_PRO_YEARLY, "GOOGLE_PRODUCT_PRO_YEARLY"),
                CYCLECOACH_PRO_YEARLY,
            ),
        ),
    )
    _ensure_provider_ids_are_distinct(
        [product.provider_product_id for product in products], "GOOGLE_PRODUCT_PRO_MONTHLY"
    )
    _log_bootstrap_provider(
        provider=SubscriptionProvider.PLAY_STORE.value,
        products=[product.provider_product_id for product in products],
        package_name=package_name,
    )
    return adapter, products


def _bind_provider_products(
    provider: SubscriptionProvider,
    platform: ProductPlatform,
    bindings: tuple[tuple[str, str], ...],
) -> tuple[ProviderProduct, ...]:
    """Bind configured store IDs to the server-owned canonical products.

    Unknown or non-PRO internal products are rejected here rather than
    becoming grants. The same provider ID must not already resolve to an
    inactive product, and callers must additionally reject IDs shared with a
    different provider.
    """
    products: list[ProviderProduct] = []
    for provider_product_id, internal_product_id in bindings:
        provider_text = provider_product_id.strip()
        internal_text = internal_product_id.strip()
        if not provider_text:
            raise StoreBootstrapError(["a configured store product ID is empty"])
        if plan_for_product(internal_text) is not Plan.PRO:
            raise StoreBootstrapError([f"{internal_text} is not a canonical PRO product binding"])
        existing = provider_product_for(provider, provider_text)
        if existing is not None and not existing.active:
            raise StoreBootstrapError(
                [f"{provider_text} is present but inactive in the product catalog"]
            )
        products.append(
            ProviderProduct(
                provider=provider,
                provider_product_id=provider_text,
                product_id=internal_text,
                platform=platform,
                product_type=ProductType.SUBSCRIPTION,
                plan=Plan.PRO,
                active=True,
            )
        )
    return tuple(products)


def _ensure_provider_ids_are_distinct(provider_product_ids: list[str], field: str) -> None:
    """Reject duplicate provider IDs within one provider's configured stack.

    Cross-provider confusion is checked after both providers validate, because
    the duplicate may span Apple and Google settings.
    """
    if len(set(provider_product_ids)) != len(provider_product_ids):
        raise StoreBootstrapError([f"{field} contains a duplicated store product ID"])


def _check_cross_provider_products(
    apple_products: tuple[str, ...], google_products: tuple[str, ...]
) -> None:
    """Reject one store product ID claimed by both Apple and Google."""
    overlap = set(apple_products) & set(google_products)
    if overlap:
        raise StoreBootstrapError(
            [
                (
                    "APPLE_PRODUCT_PRO_MONTHLY/APPLE_PRODUCT_PRO_YEARLY overlaps "
                    "GOOGLE_PRODUCT_PRO_MONTHLY/GOOGLE_PRODUCT_PRO_YEARLY"
                )
            ]
        )


def _read_apple_root_certificates(path_value: str) -> list[bytes]:
    """Read and parse an Apple root-certificate PEM bundle."""
    path_text = _require_value(path_value, "APPLE_ROOT_CERTS_PATH")
    try:
        data = Path(path_text).read_bytes()
    except OSError:
        raise _problem("APPLE_ROOT_CERTS_PATH", "is missing or unreadable") from None
    if not data.strip():
        raise _problem("APPLE_ROOT_CERTS_PATH", "is empty")
    blocks = _PEM_CERTIFICATE_RE.findall(data)
    if not blocks:
        raise _problem("APPLE_ROOT_CERTS_PATH", "contains no PEM certificate")
    try:
        for block in blocks:
            x509.load_pem_x509_certificate(block)
    except ValueError:
        raise _problem("APPLE_ROOT_CERTS_PATH", "contains malformed trust material") from None
    return [bytes(block) for block in blocks]


def _read_apple_private_key(path_value: str) -> bytes:
    """Read and parse an App Store Connect ES256 private key."""
    path_text = _require_value(path_value, "APPLE_PRIVATE_KEY_PATH")
    try:
        data = Path(path_text).read_bytes()
    except OSError:
        raise _problem("APPLE_PRIVATE_KEY_PATH", "is missing or unreadable") from None
    if not data.strip():
        raise _problem("APPLE_PRIVATE_KEY_PATH", "is empty")
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except Exception:  # noqa: BLE001 - any parse failure is one opaque config error
        raise _problem("APPLE_PRIVATE_KEY_PATH", "is not a usable private key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise _problem("APPLE_PRIVATE_KEY_PATH", "is not an App Store ES256 private key")
    return data


def _read_google_service_account(path_value: str) -> dict[str, object]:
    """Read and minimally validate a Google service-account credential."""
    path_text = _require_value(path_value, "GOOGLE_APPLICATION_CREDENTIALS_PATH")
    try:
        raw = Path(path_text).read_text(encoding="utf-8")
    except OSError:
        raise _problem("GOOGLE_APPLICATION_CREDENTIALS_PATH", "is missing or unreadable") from None
    try:
        credentials = json.loads(raw)
    except ValueError:
        raise _problem(
            "GOOGLE_APPLICATION_CREDENTIALS_PATH", "is not valid service-account JSON"
        ) from None
    if not isinstance(credentials, dict):
        raise _problem("GOOGLE_APPLICATION_CREDENTIALS_PATH", "is not valid service-account JSON")
    credential_type = credentials.get("type")
    client_email = credentials.get("client_email")
    private_key = credentials.get("private_key")
    if (
        credential_type != "service_account"
        or not isinstance(client_email, str)
        or not client_email.strip()
        or not isinstance(private_key, str)
        or not private_key.strip()
    ):
        raise _problem(
            "GOOGLE_APPLICATION_CREDENTIALS_PATH",
            "is not a usable service-account credential",
        )
    try:
        key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
    except Exception:  # noqa: BLE001 - any parse failure is one opaque config error
        raise _problem(
            "GOOGLE_APPLICATION_CREDENTIALS_PATH",
            "is not a usable service-account credential",
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise _problem(
            "GOOGLE_APPLICATION_CREDENTIALS_PATH",
            "is not a usable service-account credential",
        )
    # Keep only the credential fields the Play client needs. Extra service-account
    # metadata never enters provider memory from this path.
    return {
        "type": "service_account",
        "client_email": client_email.strip(),
        "private_key": private_key,
    }


def _log_bootstrap(result: StoreBootstrapResult) -> None:
    log.info(
        "store_bootstrap.completed",
        extra=redact(
            {
                "apple_provider_registered": result.apple_provider_registered,
                "google_provider_registered": result.google_provider_registered,
                "apple_products": ",".join(result.apple_products),
                "google_products": ",".join(result.google_products),
            }
        ),
    )


def _log_bootstrap_provider(*, provider: str, products: list[str], **fields: str) -> None:
    """Log one provider's successful bootstrap with safe identifiers only."""
    log.info(
        "store_bootstrap.provider_registered",
        extra=redact({"provider": provider, "products": ",".join(products), **fields}),
    )
