"""
scraper.py — HTTP-клиент и парсеры каталога и страниц аниме.

Экспортирует:
    fetch(url)          → html | None
    iter_catalog(...)   → Iterator[CatalogItem]
    parse_detail(...)   → AnimeDetail | None
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import sync_playwright
from tenacity import (
    before_sleep_log, retry, retry_if_exception_type,
    stop_after_attempt, wait_exponential,
)

log = logging.getLogger(__name__)

BASE_URL      = os.getenv("ANIMEGO_BASE_URL", "https://animego.me").rstrip("/")
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))
MAX_RETRIES   = int(os.getenv("MAX_RETRIES", "3"))


# ═══════════════════════════════════════════════════════════════════════════════
#  HTTP-клиент
# ═══════════════════════════════════════════════════════════════════════════════

class _TransientError(Exception):
    pass


@retry(
    retry=retry_if_exception_type(_TransientError),
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def fetch(url: str) -> Optional[str]:
    """Загружает страницу через Playwright (обходит JS + Cloudflare)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=60_000)
            page.wait_for_timeout(3_000)   # ждём JS-рендеринг
            html = page.content()
            browser.close()
    except Exception as e:
        raise _TransientError(f"Playwright: {e}") from e

    log.debug("Fetched %s (%d bytes)", url, len(html))
    time.sleep(REQUEST_DELAY)
    return html


# ═══════════════════════════════════════════════════════════════════════════════
#  Парсер каталога
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CatalogItem:
    url:   str
    slug:  str
    title: str


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _is_valid_anime_url(href: str) -> bool:
    if not href or "/anime/" not in href:
        return False
    bad = ("/anime/page/", "/anime/status/", "/anime/type/",
           "/anime/genre/", "/anime/studio/", "/anime/year/", "/anime/season/")
    return not any(x in href for x in bad)


def _parse_catalog_page(html: str) -> tuple[list[CatalogItem], Optional[str]]:
    soup  = BeautifulSoup(html, "lxml")
    items: list[CatalogItem] = []
    seen:  set[str] = set()

    for link in soup.select("a[href*='/anime/']"):
        href = link.get("href", "")
        if not _is_valid_anime_url(href):
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        title = link.get("title") or link.get_text(" ", strip=True) or _slug_from_url(full_url)
        if len(title) < 2:
            continue
        items.append(CatalogItem(url=full_url, slug=_slug_from_url(full_url), title=title))

    next_el  = soup.select_one("ul.pagination li.page-item:last-child a.page-link")
    next_url = urljoin(BASE_URL, next_el["href"]) if next_el and next_el.get("href") else None

    return items, next_url


def iter_catalog(start_page: int = 1, max_pages: Optional[int] = None) -> Iterator[CatalogItem]:
    """Листает весь каталог и выдаёт CatalogItem один за другим."""
    seen: set[str] = set()
    url: Optional[str] = f"{BASE_URL}/anime?page={start_page}"
    page_num = start_page

    while url:
        log.info("Catalog page %d: %s", page_num, url)
        html = fetch(url)
        if not html:
            break

        items, next_url = _parse_catalog_page(html)
        if not items:
            log.warning("Пустая страница %s — останавливаем пагинацию", url)
            break

        for item in items:
            if item.url not in seen:
                seen.add(item.url)
                yield item

        if max_pages and page_num >= start_page + max_pages - 1:
            break

        url = next_url
        page_num += 1

    log.info("Каталог: всего %d уникальных тайтлов", len(seen))


# ═══════════════════════════════════════════════════════════════════════════════
#  Парсер страницы аниме
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EpisodeData:
    number:   int
    title:    Optional[str] = None
    aired_on: Optional[str] = None


@dataclass
class CastEntry:
    character_slug:          str
    character_name:          str
    character_name_original: Optional[str] = None
    character_image_url:     Optional[str] = None
    voice_actor_slug:        Optional[str] = None
    voice_actor_name:        Optional[str] = None
    voice_actor_name_original: Optional[str] = None
    voice_actor_language:    Optional[str] = None
    voice_actor_image_url:   Optional[str] = None
    role:                    Optional[str] = None


@dataclass
class AnimeDetail:
    slug:             str
    url:              str
    title:            str
    title_original:   Optional[str]       = None
    title_english:    Optional[str]       = None
    type:             Optional[str]       = None
    status:           Optional[str]       = None
    year:             Optional[int]       = None
    season:           Optional[str]       = None
    episodes_total:   Optional[int]       = None
    episodes_aired:   Optional[int]       = None
    duration_minutes: Optional[int]       = None
    rating:           Optional[float]     = None
    rating_count:     Optional[int]       = None
    age_rating:       Optional[str]       = None
    description:      Optional[str]       = None
    poster_url:       Optional[str]       = None
    genres:           list[str]           = field(default_factory=list)
    studios:          list[str]           = field(default_factory=list)
    episodes:         list[EpisodeData]   = field(default_factory=list)
    cast:             list[CastEntry]     = field(default_factory=list)

    def content_hash(self) -> str:
        payload = json.dumps({
            "title": self.title, "status": self.status,
            "episodes_aired": self.episodes_aired, "rating": self.rating,
            "description": self.description,
            "genres": sorted(self.genres), "studios": sorted(self.studios),
            "episodes": [(e.number, e.title) for e in self.episodes],
            "cast": sorted((c.character_slug, c.voice_actor_slug or "") for c in self.cast),
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


# Перевод русских меток → ключи
_LABEL_MAP = {
    "тип": "type", "эпизоды": "episodes", "статус": "status",
    "жанр": "genres", "жанры": "genres", "студия": "studios",
    "возрастные ограничения": "age_rating", "рейтинг": "rating",
    "длительность": "duration", "сезон": "season", "год": "year",
    "выпуск": "aired", "другие названия": "title_alt",
}

# Селекторы каста (проверить на живом сайте!)
_CAST = {
    "block":        ".characters-list, .anime-cast, #characters",
    "row":          ".character-row, .characters-list-item, .person-list-item",
    "char_card":    ".character, .person-card.character, .char-info",
    "actor_card":   ".voice-actor, .person-card.actor, .seiyuu",
    "name":         ".person-name, .character-name, .name",
    "name_orig":    ".person-name-original, .name-original",
    "image":        "img",
    "lang":         ".lang, .language, .person-lang",
    "role":         ".character-role, .role",
}


def _text(el: Optional[Tag]) -> Optional[str]:
    return el.get_text(strip=True) if el else None


def _int(s: Optional[str]) -> Optional[int]:
    m = re.search(r"\d+", s.replace(" ", "")) if s else None
    return int(m.group()) if m else None


def _float(s: Optional[str]) -> Optional[float]:
    m = re.search(r"\d+(?:[.,]\d+)?", s) if s else None
    return float(m.group().replace(",", ".")) if m else None


def _abs(href: Optional[str]) -> Optional[str]:
    return urljoin(BASE_URL, href) if href else None


def _extract_metadata(soup: BeautifulSoup) -> dict:
    meta: dict = {}

    # Вариант 1: <dl><dt>…</dt><dd>…</dd></dl>
    for dl in soup.select("dl, .anime-info dl"):
        for dt, dd in zip(dl.select("dt"), dl.select("dd")):
            key = _LABEL_MAP.get(dt.get_text(strip=True).lower().rstrip(":"))
            if not key or key in meta:
                continue
            if key in {"genres", "studios"}:
                meta[key] = [a.get_text(strip=True) for a in dd.select("a")] or [dd.get_text(strip=True)]
            else:
                meta[key] = dd.get_text(" ", strip=True)

    # Вариант 2: строки с двумя колонками
    for row in soup.select(".description-list .row, .anime-info .row"):
        cells = row.select(".col, .col-md-4, .col-md-8")
        if len(cells) < 2:
            continue
        key = _LABEL_MAP.get(cells[0].get_text(strip=True).lower().rstrip(":"))
        if not key or key in meta:
            continue
        if key in {"genres", "studios"}:
            meta[key] = [a.get_text(strip=True) for a in cells[1].select("a")] or [cells[1].get_text(strip=True)]
        else:
            meta[key] = cells[1].get_text(" ", strip=True)

    return meta


def _extract_episodes(soup: BeautifulSoup) -> list[EpisodeData]:
    episodes = []
    for row in soup.select(".episodes-list .episodes-list-item, .episodes-row"):
        num = _int(_text(row.select_one(".episodes-list-item-number, .episode-number")))
        if num is None:
            continue
        episodes.append(EpisodeData(
            number=num,
            title=_text(row.select_one(".episodes-list-item-name, .episode-title")),
            aired_on=_text(row.select_one(".episodes-list-item-date, .episode-date")),
        ))
    return episodes


def _parse_person(card: Optional[Tag]):
    """Возвращает (slug, name, name_orig, image_url, language)."""
    if not card:
        return None, None, None, None, None
    link = card.select_one("a")
    href = link.get("href") if link else None
    name = _text(card.select_one(_CAST["name"])) or (link.get("title") if link else None)
    if not name:
        return None, None, None, None, None
    slug = _slug_from_url(href) if href else "name-" + hashlib.md5(name.encode()).hexdigest()[:16]
    img_el = card.select_one(_CAST["image"])
    image  = _abs(img_el.get("data-src") or img_el.get("src") or img_el.get("data-original")) if img_el else None
    return slug, name, _text(card.select_one(_CAST["name_orig"])), image, _text(card.select_one(_CAST["lang"]))


def _extract_cast(soup: BeautifulSoup) -> list[CastEntry]:
    cast  = []
    block = soup.select_one(_CAST["block"])
    if not block:
        return cast
    for row in block.select(_CAST["row"]):
        c_slug, c_name, c_orig, c_img, _ = _parse_person(row.select_one(_CAST["char_card"]))
        if not c_slug:
            continue
        a_slug, a_name, a_orig, a_img, a_lang = _parse_person(row.select_one(_CAST["actor_card"]))
        cast.append(CastEntry(
            character_slug=c_slug, character_name=c_name,
            character_name_original=c_orig, character_image_url=c_img,
            voice_actor_slug=a_slug, voice_actor_name=a_name,
            voice_actor_name_original=a_orig, voice_actor_language=a_lang,
            voice_actor_image_url=a_img,
            role=_text(row.select_one(_CAST["role"])),
        ))
    return cast


def parse_detail(html: str, url: str) -> Optional[AnimeDetail]:
    """Парсит страницу аниме, возвращает AnimeDetail или None."""
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1, .anime-title")
    if not title_el:
        log.warning("Нет <h1> на %s", url)
        return None

    d = AnimeDetail(slug=_slug_from_url(url), url=url, title=title_el.get_text(strip=True))

    alt = soup.select_one(".anime-original, .original-title")
    if alt:
        d.title_original = alt.get_text(strip=True)

    desc = soup.select_one(".description, .anime-description, [itemprop='description']")
    if desc:
        d.description = desc.get_text("\n", strip=True)

    poster = soup.select_one(".anime-poster img, .poster img")
    if poster:
        d.poster_url = _abs(poster.get("data-src") or poster.get("src"))

    d.rating       = _float(_text(soup.select_one("[itemprop='ratingValue'], .rating-value")))
    d.rating_count = _int(_text(soup.select_one("[itemprop='ratingCount'], .rating-count")))

    meta = _extract_metadata(soup)

    d.type       = meta.get("type") if isinstance(meta.get("type"), str) else None
    d.status     = meta.get("status") if isinstance(meta.get("status"), str) else None
    d.season     = meta.get("season") if isinstance(meta.get("season"), str) else None
    d.age_rating = meta.get("age_rating") if isinstance(meta.get("age_rating"), str) else None

    if isinstance(meta.get("genres"),  list): d.genres  = list(meta["genres"])
    if isinstance(meta.get("studios"), list): d.studios = list(meta["studios"])

    eps_text = meta.get("episodes")
    if isinstance(eps_text, str):
        nums = re.findall(r"\d+", eps_text)
        if len(nums) >= 2:
            d.episodes_aired = int(nums[0]); d.episodes_total = int(nums[1])
        elif len(nums) == 1:
            d.episodes_total = int(nums[0])

    year_text = meta.get("year") or meta.get("aired")
    if isinstance(year_text, str):
        y = re.search(r"(19|20)\d{2}", year_text)
        if y:
            d.year = int(y.group())

    if isinstance(meta.get("duration"), str):
        d.duration_minutes = _int(meta["duration"])

    d.episodes = _extract_episodes(soup)
    d.cast     = _extract_cast(soup)

    return d
