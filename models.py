"""
models.py — таблицы базы данных (SQLAlchemy 2.x + PostgreSQL).

Запуск: python models.py  → создаёт таблицы (если их ещё нет).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Table, Text,
    UniqueConstraint, create_engine, func,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

load_dotenv()


class Base(DeclarativeBase):
    pass


# ── Many-to-many ─────────────────────────────────────────────────────────────

anime_genres = Table(
    "anime_genres", Base.metadata,
    Column("anime_id",  ForeignKey("anime.id",   ondelete="CASCADE"), primary_key=True),
    Column("genre_id",  ForeignKey("genres.id",  ondelete="CASCADE"), primary_key=True),
)

anime_studios = Table(
    "anime_studios", Base.metadata,
    Column("anime_id",  ForeignKey("anime.id",   ondelete="CASCADE"), primary_key=True),
    Column("studio_id", ForeignKey("studios.id", ondelete="CASCADE"), primary_key=True),
)


# ── Справочники ───────────────────────────────────────────────────────────────

class Genre(Base):
    __tablename__ = "genres"
    id:   Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(128), unique=True)


class Studio(Base):
    __tablename__ = "studios"
    id:   Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)


class Character(Base):
    __tablename__ = "characters"
    id:            Mapped[int] = mapped_column(primary_key=True)
    slug:          Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name:          Mapped[str] = mapped_column(String(255), nullable=False)
    name_original: Mapped[Optional[str]] = mapped_column(String(255))
    image_url:     Mapped[Optional[str]] = mapped_column(String(1024))


class VoiceActor(Base):
    __tablename__ = "voice_actors"
    id:            Mapped[int] = mapped_column(primary_key=True)
    slug:          Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name:          Mapped[str] = mapped_column(String(255), nullable=False)
    name_original: Mapped[Optional[str]] = mapped_column(String(255))
    language:      Mapped[Optional[str]] = mapped_column(String(32))
    image_url:     Mapped[Optional[str]] = mapped_column(String(1024))


# ── Главная таблица ───────────────────────────────────────────────────────────

class Anime(Base):
    __tablename__ = "anime"

    id:    Mapped[int] = mapped_column(primary_key=True)
    slug:  Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    url:   Mapped[str] = mapped_column(String(512),  nullable=False)
    title: Mapped[str] = mapped_column(String(512),  nullable=False)

    title_original: Mapped[Optional[str]] = mapped_column(String(512))
    title_english:  Mapped[Optional[str]] = mapped_column(String(512))
    type:           Mapped[Optional[str]] = mapped_column(String(64))
    status:         Mapped[Optional[str]] = mapped_column(String(64))
    year:           Mapped[Optional[int]] = mapped_column(Integer)
    season:         Mapped[Optional[str]] = mapped_column(String(32))
    episodes_total: Mapped[Optional[int]] = mapped_column(Integer)
    episodes_aired: Mapped[Optional[int]] = mapped_column(Integer)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    rating:         Mapped[Optional[float]] = mapped_column()
    rating_count:   Mapped[Optional[int]]  = mapped_column(Integer)
    age_rating:     Mapped[Optional[str]]  = mapped_column(String(32))
    description:    Mapped[Optional[str]]  = mapped_column(Text)
    poster_url:     Mapped[Optional[str]]  = mapped_column(String(1024))

    first_seen_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    content_hash:   Mapped[Optional[str]] = mapped_column(String(64))

    genres:   Mapped[list[Genre]]   = relationship(secondary=anime_genres,  lazy="selectin")
    studios:  Mapped[list[Studio]]  = relationship(secondary=anime_studios, lazy="selectin")
    episodes: Mapped[list["Episode"]]   = relationship(back_populates="anime", cascade="all, delete-orphan", lazy="selectin")
    cast:     Mapped[list["AnimeCast"]] = relationship(back_populates="anime", cascade="all, delete-orphan", lazy="selectin")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("anime_id", "number", name="uq_anime_episode"),)

    id:       Mapped[int] = mapped_column(primary_key=True)
    anime_id: Mapped[int] = mapped_column(ForeignKey("anime.id", ondelete="CASCADE"), nullable=False, index=True)
    number:   Mapped[int] = mapped_column(Integer, nullable=False)
    title:    Mapped[Optional[str]] = mapped_column(String(512))
    aired_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    anime: Mapped[Anime] = relationship(back_populates="episodes")


class AnimeCast(Base):
    __tablename__ = "anime_cast"
    __table_args__ = (
        UniqueConstraint("anime_id", "character_id", "voice_actor_id", name="uq_anime_character_actor"),
    )

    id:             Mapped[int] = mapped_column(primary_key=True)
    anime_id:       Mapped[int] = mapped_column(ForeignKey("anime.id",        ondelete="CASCADE"),  nullable=False, index=True)
    character_id:   Mapped[int] = mapped_column(ForeignKey("characters.id",   ondelete="CASCADE"),  nullable=False, index=True)
    voice_actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("voice_actors.id", ondelete="SET NULL"), index=True)
    role:           Mapped[Optional[str]] = mapped_column(String(64))

    anime:       Mapped[Anime]              = relationship(back_populates="cast")
    character:   Mapped[Character]          = relationship(lazy="selectin")
    voice_actor: Mapped[Optional[VoiceActor]] = relationship(lazy="selectin")


# ── Подключение ───────────────────────────────────────────────────────────────

DATABASE_URL = os.environ["DATABASE_URL"]
engine       = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init_db()
    print("Таблицы созданы.")
