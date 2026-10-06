"""Client-safe subscription and entitlement shapes.

The response contains the rider's plan, the capabilities included with Free,
and that rider's own entitlement rows with their effective timestamps. It
never contains provider identifiers, purchase credentials, provider secrets,
another rider's data, or raw subscription rows.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.subscription import EntitlementSource, EntitlementStatus, Feature, Plan


class EntitlementOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: Feature
    status: EntitlementStatus
    source: EntitlementSource
    starts_at: datetime
    expires_at: datetime
    effective: bool


class EntitlementStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Plan
    free_capabilities: list[str]
    entitlements: list[EntitlementOut]
    evaluated_at: datetime
