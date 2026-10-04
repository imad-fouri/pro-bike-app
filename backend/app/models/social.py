"""Phase 8.1 domain: public social identity, friendships, blocks.

Design (ADR-12):
- `social_profiles` is the PUBLIC projection of identity. The private
  account profile stays in `user_profiles`; this table holds only what may
  be shown to other riders, plus the privacy settings governing it.
- `friend_relationships` holds PENDING/ACCEPTED only, one canonical row per
  unordered pair (`user_a_id < user_b_id`). Blocks live in their own table
  and annihilate relationship rows — a block is a wall, not a status.
- No location anywhere: city is a free-text label, never coordinates.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user import _uuid, _values


class FriendRequestsPolicy(str, enum.Enum):
    EVERYONE = "everyone"
    NOBODY = "nobody"


class SearchVisibility(str, enum.Enum):
    DISCOVERABLE = "discoverable"
    HIDDEN = "hidden"


class RelationshipStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"


class SocialProfile(Base):
    __tablename__ = "social_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    # Canonical username: lowercase, validated in service layer. Uniqueness is
    # exact-match on the canonical form, so "Imad_Fouri" and "imad_fouri"
    # collide by construction. NULL until the rider claims one (multiple
    # NULLs allowed by UNIQUE).
    username: Mapped[str | None] = mapped_column(String(30), unique=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    bio: Mapped[str | None] = mapped_column(String(500))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    cycling_category: Mapped[str | None] = mapped_column(String(32))
    country_code: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(120))
    profile_visibility: Mapped[str] = mapped_column(String(16), default="public", nullable=False)
    allow_friend_requests: Mapped[FriendRequestsPolicy] = mapped_column(
        Enum(FriendRequestsPolicy, name="friend_requests_policy", values_callable=_values),
        default=FriendRequestsPolicy.EVERYONE,
        nullable=False,
    )
    search_visibility: Mapped[SearchVisibility] = mapped_column(
        Enum(SearchVisibility, name="search_visibility", values_callable=_values),
        default=SearchVisibility.DISCOVERABLE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_social_profiles_username", "username"),
        Index("ix_social_profiles_user_id", "user_id"),
        CheckConstraint(
            "profile_visibility IN ('public', 'friends', 'private')",
            name="ck_social_profiles_visibility",
        ),
    )


class FriendRelationship(Base):
    __tablename__ = "friend_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Canonical unordered pair: user_a_id < user_b_id always, so A-B and B-A
    # can never exist as separate rows.
    user_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RelationshipStatus] = mapped_column(
        Enum(RelationshipStatus, name="relationship_status", values_callable=_values),
        default=RelationshipStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_friend_relationships_pair"),
        CheckConstraint("user_a_id != user_b_id", name="ck_friend_relationships_no_self"),
        CheckConstraint("user_a_id < user_b_id", name="ck_friend_relationships_canonical"),
        Index("ix_friend_relationships_user_a_status", "user_a_id", "status"),
        Index("ix_friend_relationships_user_b_status", "user_b_id", "status"),
        Index("ix_friend_relationships_requested_by", "requested_by_user_id", "status"),
    )


class UserBlock(Base):
    __tablename__ = "user_blocks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    blocker_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_user_blocks_pair"),
        CheckConstraint("blocker_user_id != blocked_user_id", name="ck_user_blocks_no_self"),
        Index("ix_user_blocks_blocker", "blocker_user_id"),
        Index("ix_user_blocks_blocked", "blocked_user_id"),
    )
