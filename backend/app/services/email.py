"""Email abstraction: explicit provider selection, no silent production fake.

Phase 8.5 remediation: `DevEmailService` used to be wired unconditionally, so
password-reset tokens were never delivered in any environment while every raw
token accumulated forever in process memory. The binding below fails closed —
production refuses to boot without an explicitly configured real provider —
and the dev double keeps a bounded outbox so it cannot retain credentials
indefinitely.
"""

from dataclasses import dataclass

from app.core.config import settings


@dataclass
class OutboxMessage:
    to: str
    subject: str
    body: str


class EmailService:
    """Production implementation sends via provider (not configured)."""

    def send(self, msg: OutboxMessage) -> None:
        raise NotImplementedError("No email provider configured.")


class DevEmailService(EmailService):
    """Development/test double — keeps messages in memory for assertions.

    The outbox is deliberately bounded: once full, the oldest message is
    evicted. Tests assert on messages immediately after sending, so eviction
    only discards messages nothing will ever read — while guaranteeing the
    process cannot accumulate credentials without bound.
    """

    MAX_OUTBOX_MESSAGES = 100

    def __init__(self) -> None:
        self.outbox: list[OutboxMessage] = []

    def send(self, msg: OutboxMessage) -> None:
        if len(self.outbox) >= self.MAX_OUTBOX_MESSAGES:
            self.outbox.pop(0)
        self.outbox.append(msg)


def build_email_service(*, environment: str, provider: str) -> EmailService:
    """Select the email implementation explicitly.

    Production has no real provider in this phase, so it refuses to boot
    rather than silently swallowing password-reset delivery. Development and
    test may use the bounded dev double. Unknown names fail loudly so a typo
    is a startup error, not a silent misconfiguration.
    """
    if environment == "production":
        raise RuntimeError(
            "No email provider is configured for production: set EMAIL_PROVIDER "
            "to an implemented real provider instead of running without delivery."
        )
    if provider != "dev":
        raise RuntimeError(f"Unknown email provider {provider!r}: only 'dev' is implemented.")
    return DevEmailService()


email_service: EmailService = build_email_service(
    environment=settings.ENVIRONMENT, provider=settings.EMAIL_PROVIDER
)
