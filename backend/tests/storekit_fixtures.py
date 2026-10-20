"""Deterministic App Store Server API fixtures (WS-RP Phase 3/15).

Generates a throwaway Apple trust root, issues a leaf certificate under it,
signs realistic JWSTransactionDecodedPayload / JWSSignedRenewalInfoStatus
claims, and assembles Get-All-Subscription-Statuses responses. Everything is
ephemeral and self-contained so provider contract tests exercise real
cryptography (ES256 + x5c chain-to-root) without contacting any store.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

BUNDLE_ID = "com.cyclecoach.app"
PROVIDER_PRODUCT_ID = "com.example.cyclecoach.pro.monthly"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _make_root(
    *, common_name: str = "CycleCoach Test Root CA"
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
                crl_sign=True,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert, key


def _issue_leaf(
    root_cert: x509.Certificate,
    root_key: ec.EllipticCurvePrivateKey,
    *,
    common_name: str = "transactional",
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(root_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(root_key, hashes.SHA256())
    )
    return key, cert


class AppleSigner:
    """Builds and JWS-signs Apple-shaped payloads under a fresh test root."""

    def __init__(self) -> None:
        self.root_cert, self.root_key = _make_root()
        self.leaf_key, self.leaf_cert = _issue_leaf(self.root_cert, self.root_key)

    def root_pem(self) -> bytes:
        return self.root_cert.public_bytes(serialization.Encoding.PEM)

    def leaf_private_key_pem(self) -> bytes:
        return self.leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def sign(self, payload: dict) -> str:
        header = {
            "alg": "ES256",
            "kid": "TESTKID",
            "typ": "JWT",
            "x5c": [
                base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode("ascii")
                for c in (self.leaf_cert, self.root_cert)
            ],
        }
        head = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{head}.{body}".encode()
        der_signature = self.leaf_key.sign(
            signing_input, signature_algorithm=ec.ECDSA(hashes.SHA256())
        )
        # JWS ES256 (RFC 7518) uses raw r||s, not DER — exactly what PyJWT's
        # own encode produces, so decode() and our fixtures agree.
        raw_signature = jwt.utils.der_to_raw_signature(der_signature, self.leaf_key.curve)
        return f"{head}.{body}.{_b64url(raw_signature)}"


def transaction_payload(
    *,
    transaction_id: str = "tx-discarded-0001",
    original_transaction_id: str = "orig-tx-0001",
    product_id: str = PROVIDER_PRODUCT_ID,
    bundle_id: str = BUNDLE_ID,
    environment: str = "Production",
    app_account_token: str | None = None,
    purchase_date: datetime | None = None,
    expires_date: datetime | None = None,
    revocation_date: datetime | None = None,
    type_: str = "Auto-RenewableSubscription",
) -> dict:
    now = datetime.now(UTC)
    purchase = purchase_date or (now - timedelta(days=10))
    expires = expires_date or (now + timedelta(days=20))
    claims: dict = {
        "transactionId": transaction_id,
        "originalTransactionId": original_transaction_id,
        "bundleId": bundle_id,
        "productId": product_id,
        "environment": environment,
        "type": type_,
        "inAppOwnershipType": "PURCHASED",
        "subscriptionGroupIdentifier": "11111111",
        "purchaseDate": ms(purchase),
        "originalPurchaseDate": ms(purchase),
        "expiresDate": ms(expires),
        "signedDate": ms(expires - timedelta(days=2)),
        "webOrderLineItemId": "900000000000001",
        "transactionReason": "PURCHASE",
    }
    if app_account_token is not None:
        claims["appAccountToken"] = app_account_token
    if revocation_date is not None:
        claims["revocationDate"] = ms(revocation_date)
    return claims


def renewal_payload(
    *,
    original_transaction_id: str = "orig-tx-0001",
    auto_renew_status: int = 1,
    product_id: str = PROVIDER_PRODUCT_ID,
) -> dict:
    now = datetime.now(UTC)
    return {
        "originalTransactionId": original_transaction_id,
        "autoRenewProductId": product_id,
        "autoRenewStatus": auto_renew_status,
        "signedDate": ms(now),
        "productId": product_id,
    }


def status_response(
    signer: AppleSigner,
    *,
    transaction: dict,
    renewal: dict | None = None,
    status: int = 1,
    environment: str | None = None,
) -> dict:
    item: dict = {
        "status": status,
        "signedTransactionInfo": signer.sign(transaction),
    }
    if renewal is not None:
        item["signedRenewalInfo"] = signer.sign(renewal)
    return {
        "appAppleId": 1495235348,
        "bundleId": BUNDLE_ID,
        "environment": environment or transaction.get("environment", "Production"),
        "status": status,
        "data": [
            {
                "subscriptionGroupIdentifier": "11111111",
                "lastTransactions": [item],
            }
        ],
    }


def rider_token() -> str:
    return str(uuid.uuid4())


def notification_payload(
    signer: AppleSigner,
    *,
    notification_type: str,
    transaction: dict,
    renewal: dict | None = None,
    subtype: str | None = None,
    signed_date: datetime | None = None,
) -> dict:
    """An App Store Server Notification v2 body (``POST /webhooks/apple``).

    ``signedPayload`` is the JWS that authenticates the whole notification; the
    server's webhook adapter verifies its chain and signature before trusting
    any field. ``signed_date`` is the notification's ``signedDate`` — the event
    time the pipeline orders on — so tests can build deterministic in-order and
    out-of-order deliveries.
    """
    body: dict = {
        "notificationType": notification_type,
        "signedDate": ms(signed_date or datetime.now(UTC)),
        "data": {
            "signedTransactionInfo": signer.sign(transaction),
        },
    }
    if renewal is not None:
        body["data"]["signedRenewalInfo"] = signer.sign(renewal)
    if subtype is not None:
        body["subtype"] = subtype
    return {"signedPayload": signer.sign(body)}
