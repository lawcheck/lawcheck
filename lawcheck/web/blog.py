"""Блог: SEO-статьи из markdown-файлов в lawcheck/content/blog/.

Каждая статья — отдельный .md с YAML-фронтматтером (title, description, date).
Добавить статью = положить новый .md в папку, перезапуск не требуется
(файлы перечитываются на каждый запрос — их немного).
"""
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import markdown as md
import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

router = APIRouter()
_CONTENT_DIR = Path(__file__).parent.parent / "content" / "blog"

# templates задаётся из routes.py при подключении, чтобы переиспользовать
# единый экземпляр Jinja2Templates с общими globals (operator, metrika_id).
templates: Jinja2Templates = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    description: str
    date: date
    html: str  # отрендеренное тело (только для страницы статьи)


def _parse_file(path: Path) -> Article | None:
    """Разобрать один .md: YAML-фронтматтер между --- и тело в markdown."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        log.warning("blog: %s без фронтматтера — пропущен", path.name)
        return None
    _, fm, body = raw.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    title = meta.get("title")
    if not title:
        log.warning("blog: %s без title — пропущен", path.name)
        return None
    raw_date = meta.get("date")
    parsed_date = raw_date if isinstance(raw_date, date) else date.min
    html = md.markdown(body.strip(), extensions=["extra", "sane_lists"])
    return Article(
        slug=path.stem,
        title=title,
        description=meta.get("description", ""),
        date=parsed_date,
        html=html,
    )


def list_articles() -> list[Article]:
    """Все статьи, по убыванию даты (новые сверху)."""
    if not _CONTENT_DIR.is_dir():
        return []
    items = [a for p in _CONTENT_DIR.glob("*.md") if (a := _parse_file(p)) is not None]
    return sorted(items, key=lambda a: a.date, reverse=True)


# Слаг приходит из URL и подставляется в путь к файлу. Сейчас обход каталога
# невозможен (Starlette не пускает `%2F` в path-параметр — проверено), но
# полагаться на поведение роутера в вопросе чтения файлов не стоит: список
# разрешённых символов дешевле, чем разбор инцидента после смены версии.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def get_article(slug: str) -> Article | None:
    if not _SLUG_RE.match(slug):
        return None
    path = _CONTENT_DIR / f"{slug}.md"
    # Вторая линия: файл обязан лежать внутри каталога статей.
    if not path.resolve().is_relative_to(_CONTENT_DIR.resolve()):
        return None
    if not path.is_file():
        return None
    return _parse_file(path)


@router.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request):
    return templates.TemplateResponse(
        request, "blog_index.html", {"articles": list_articles()}
    )


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_article(request: Request, slug: str):
    article = get_article(slug)
    if article is None:
        raise HTTPException(status_code=404, detail="article not found")
    return templates.TemplateResponse(
        request, "blog_article.html", {"article": article}
    )
