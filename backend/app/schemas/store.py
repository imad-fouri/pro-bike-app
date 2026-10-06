"""Store purchase verification schemas.

The request carries the minimum a verification needs: which provider, which
product, and the opaque purchase credential. There is deliberately no field
for plan, status, expiration, or entitlement — anything the client asserts
about the outcome is ignored by construction, because the schema cannot
express it.

`extra="forbid"` is load-bearing here exactly as it is on coach requests: a
client sending `is_pro` gets a 422 rather than a silently-ignored claim.
"""

from pydantic import BaseModel, ConfigDict, Field


class VerifyPurchaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    product_id: str = Field(min_length=1, max_length=128)
    # Opaque credential, passed through to the provider adapter exactly once.
    # Bounded at 4 KiB (signed store transactions fit comfortably); anything
    # larger is a malformed request, not a receipt. Never echoed: the shared
    # validation handler strips `input` from every 422.
    purchase_token: str = Field(min_length=1, max_length=4096)
