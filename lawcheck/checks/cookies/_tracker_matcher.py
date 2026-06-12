"""Сопоставление сетевых запросов сайта со справочником трекеров.

Используется D1 (инвентаризация) и D2 (cookie-баннер).
"""
from dataclasses import dataclass
from urllib.parse import urlparse

from lawcheck.crawler.snapshot import SiteSnapshot
from lawcheck.dictionaries import loader


@dataclass
class TrackerHit:
    name: str
    category: str
    jurisdiction: str  # ru | foreign | mixed
    sets_pd_identifiers: bool
    cross_border_risk: str  # low | medium | high | "" (для RU)
    matched_urls: list[str]   # уникальные примеры сработавших URL
    serves_creatives: bool = False  # ads-сеть отдаёт креативы на сайт (не пиксель рекламодателя)


def _request_keys(snapshot: SiteSnapshot) -> list[tuple[str, str]]:
    """Список (key, full_url), где key = netloc+path в нижнем регистре."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for page in snapshot.pages:
        for req in page.network:
            parsed = urlparse(req.url)
            key = (parsed.netloc + parsed.path).lower()
            if not key or req.url in seen:
                continue
            seen.add(req.url)
            out.append((key, req.url))
    return out


def match_trackers(snapshot: SiteSnapshot) -> list[TrackerHit]:
    requests = _request_keys(snapshot)
    if not requests:
        return []
    hits: dict[str, TrackerHit] = {}
    for tracker in loader.trackers():
        domains = [d.lower() for d in tracker.get("domains") or []]
        matched: list[str] = []
        for key, full_url in requests:
            if any(d in key for d in domains):
                matched.append(full_url)
                if len(matched) >= 3:
                    break
        if not matched:
            continue
        hits[tracker["name"]] = TrackerHit(
            name=tracker["name"],
            category=tracker.get("category", ""),
            jurisdiction=tracker.get("jurisdiction", ""),
            sets_pd_identifiers=bool(tracker.get("sets_pd_identifiers")),
            cross_border_risk=tracker.get("cross_border_risk", "") or "",
            matched_urls=matched,
            serves_creatives=bool(tracker.get("serves_creatives")),
        )
    return list(hits.values())


def has_pd_identifier_trackers(hits: list[TrackerHit]) -> bool:
    return any(h.sets_pd_identifiers for h in hits)


def has_foreign_pd_trackers(hits: list[TrackerHit]) -> bool:
    return any(h.sets_pd_identifiers and h.jurisdiction == "foreign" for h in hits)
