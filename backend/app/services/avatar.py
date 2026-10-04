"""Avatar storage interface. Phase 2: local dev impl; prod = object storage + CDN."""

import abc


class AvatarStorage(abc.ABC):
    @abc.abstractmethod
    def ref_for(self, user_id: str, filename: str) -> str:
        """Return the stored reference (key/URL) for an uploaded avatar."""
        raise NotImplementedError


class LocalAvatarStorage(AvatarStorage):
    """Dev implementation — references stay local, clearly separated from prod."""

    def __init__(self, base_url: str = "/static/avatars") -> None:
        self.base_url = base_url.rstrip("/")

    def ref_for(self, user_id: str, filename: str) -> str:
        safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:80]
        return f"{self.base_url}/{user_id}/{safe or 'avatar'}"


avatar_storage: AvatarStorage = LocalAvatarStorage()
