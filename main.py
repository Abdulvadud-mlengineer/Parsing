"""
main.py — пайплайн (логика сохранения) и CLI-команды.

Использование:
    python main.py init-db                        # создать таблицы
    python main.py full --max-pages 1             # тест на 1 странице
    python main.py full                           # полный обход (часы)
    python main.py incremental                    # быстрое обновление (cron)
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import click
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

load_dotenv()

# Настройка логов до первого импорта моделей
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

from models import (                           # noqa: E402
    Anime, AnimeCast, Character, Episode,
    Genre, SessionLocal, Studio, VoiceActor,
    init_db,
)
from scraper import (                          # noqa: E402
    AnimeDetail, CastEntry,
    iter_catalog, parse_detail, fetch,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Вспомогательные функции
# ═══════════════════════════════════════════════════════════════════════════════

def _get_or_create_genre(session: Session, name: str) -> Genre:
    obj = session.scalar(select(Genre).where(Genre.name == name))
    if not obj:
        obj = Genre(name=name)
        session.add(obj)
        session.flush()
    return obj


def _get_or_create_studio(session: Session, name: str) -> Studio:
    obj = session.scalar(select(Studio).where(Studio.name == name))
    if not obj:
        obj = Studio(name=name)
        session.add(obj)
        session.flush()
    return obj


def _get_or_create_character(session: Session, e: CastEntry) -> Character:
    obj = session.scalar(select(Character).where(Character.slug == e.character_slug))
    if not obj:
        obj = Character(
            slug=e.character_slug, name=e.character_name,
            name_original=e.character_name_original, image_url=e.character_image_url,
        )
        session.add(obj)
        session.flush()
    return obj


def _get_or_create_voice_actor(session: Session, e: CastEntry) -> Optional[VoiceActor]:
    if not e.voice_actor_slug:
        return None
    obj = session.scalar(select(VoiceActor).where(VoiceActor.slug == e.voice_actor_slug))
    if not obj:
        obj = VoiceActor(
            slug=e.voice_actor_slug, name=e.voice_actor_name,
            name_original=e.voice_actor_name_original,
            language=e.voice_actor_language, image_url=e.voice_actor_image_url,
        )
        session.add(obj)
        session.flush()
    return obj


def _sync_cast(session: Session, anime: Anime, entries: list[CastEntry]) -> None:
    for c in list(anime.cast):
        session.delete(c)
    session.flush()

    seen: set = set()
    for e in entries:
        ch = _get_or_create_character(session, e)
        va = _get_or_create_voice_actor(session, e)
        key = (ch.id, va.id if va else None)
        if key in seen:
            continue
        seen.add(key)
        anime.cast.append(AnimeCast(
            character_id=ch.id,
            voice_actor_id=va.id if va else None,
            role=e.role,
        ))


# ═══════════════════════════════════════════════════════════════════════════════
#  Upsert
# ═══════════════════════════════════════════════════════════════════════════════

def upsert_anime(session: Session, d: AnimeDetail) -> None:
    anime = session.scalar(select(Anime).where(Anime.slug == d.slug))
    if not anime:
        anime = Anime(slug=d.slug, url=d.url, title=d.title)
        session.add(anime)

    # Обновляем все поля
    for field in ("url", "title", "title_original", "title_english", "type", "status",
                  "year", "season", "episodes_total", "episodes_aired", "duration_minutes",
                  "rating", "rating_count", "age_rating", "description", "poster_url"):
        setattr(anime, field, getattr(d, field))

    anime.content_hash    = d.content_hash()
    anime.last_scraped_at = datetime.now(timezone.utc)
    anime.genres          = [_get_or_create_genre(session, g)  for g in d.genres]
    anime.studios         = [_get_or_create_studio(session, s) for s in d.studios]
    session.flush()

    # Эпизоды: upsert, никогда не удаляем
    existing = {e.number: e for e in anime.episodes}
    for ep in d.episodes:
        if ep.number in existing:
            existing[ep.number].title = ep.title
        else:
            anime.episodes.append(Episode(number=ep.number, title=ep.title))

    if d.cast:
        _sync_cast(session, anime, d.cast)


# ═══════════════════════════════════════════════════════════════════════════════
#  Пайплайн
# ═══════════════════════════════════════════════════════════════════════════════

def _scrape_one(session: Session, item) -> Optional[AnimeDetail]:
    html = fetch(item.url)
    if not html:
        return None
    detail = parse_detail(html, item.url)
    if not detail:
        log.warning("Не удалось распарсить: %s", item.url)
        return None
    log.info("OK | %s | жанры=%d эп=%d каст=%d",
             detail.title, len(detail.genres), len(detail.episodes), len(detail.cast))
    upsert_anime(session, detail)
    return detail


def full_scrape(max_pages: Optional[int] = None) -> None:
    """Полный обход каталога."""
    log.info("Полный скрап начат")
    stats = {"seen": 0, "scraped": 0, "errors": 0}

    with SessionLocal() as session:
        for item in iter_catalog(max_pages=max_pages):
            stats["seen"] += 1
            try:
                if _scrape_one(session, item):
                    stats["scraped"] += 1
                    if stats["scraped"] % 20 == 0:
                        session.commit()
                        log.info("Прогресс: %s", stats)
            except Exception as e:
                stats["errors"] += 1
                log.exception("Ошибка %s: %s", item.url, e)
                session.rollback()
        session.commit()

    log.info("Полный скрап завершён: %s", stats)


def incremental(max_pages: Optional[int] = None, refresh_older_than_days: int = 7) -> None:
    """Быстрое обновление — пропускает свежие записи."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=refresh_older_than_days)
    stats  = {"seen": 0, "scraped": 0, "skipped": 0, "errors": 0}

    with SessionLocal() as session:
        for item in iter_catalog(max_pages=max_pages):
            stats["seen"] += 1
            existing = session.scalar(select(Anime).where(Anime.slug == item.slug))
            if existing and existing.last_scraped_at and existing.last_scraped_at > cutoff:
                stats["skipped"] += 1
                continue
            try:
                if _scrape_one(session, item):
                    stats["scraped"] += 1
                    if stats["scraped"] % 20 == 0:
                        session.commit()
                        log.info("Прогресс: %s", stats)
            except Exception as e:
                stats["errors"] += 1
                log.exception("Ошибка %s: %s", item.url, e)
                session.rollback()
        session.commit()

    log.info("Инкрементальный скрап завершён: %s", stats)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

@click.group()
def cli() -> None:
    """animego.me scraper."""


@cli.command("init-db")
def cmd_init_db() -> None:
    """Создать таблицы в базе данных."""
    init_db()
    click.echo("База данных инициализирована.")


@cli.command("full")
@click.option("--max-pages", type=int, default=None, help="Остановиться после N страниц каталога (для теста).")
def cmd_full(max_pages: Optional[int]) -> None:
    """Полный обход каталога (медленно, запускать редко)."""
    full_scrape(max_pages=max_pages)


@cli.command("incremental")
@click.option("--max-pages",     type=int, default=None)
@click.option("--refresh-days",  type=int, default=7,  help="Перескрапить записи старше N дней.")
def cmd_incremental(max_pages: Optional[int], refresh_days: int) -> None:
    """Быстрое обновление (запускать по cron, например каждый час)."""
    incremental(max_pages=max_pages, refresh_older_than_days=refresh_days)


if __name__ == "__main__":
    cli()
