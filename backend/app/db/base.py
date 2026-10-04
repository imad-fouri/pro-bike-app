"""Declarative base. Domain models register via app.models (imported by env + app)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
