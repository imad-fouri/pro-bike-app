"""Bike image storage. Reference-only; mirrors the avatar approach."""

import abc


class BikeImageStorage(abc.ABC):
    @abc.abstractmethod
    def ref_for(self, bike_id: str, filename: str) -> str:
        raise NotImplementedError


class LocalBikeImageStorage(BikeImageStorage):
    """Dev implementation — clearly separated from production object storage."""

    def __init__(self, base_url: str = "/static/bikes") -> None:
        self.base_url = base_url.rstrip("/")

    def ref_for(self, bike_id: str, filename: str) -> str:
        safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:80]
        return f"{self.base_url}/{bike_id}/{safe or 'bike'}"


bike_image_storage: BikeImageStorage = LocalBikeImageStorage()
