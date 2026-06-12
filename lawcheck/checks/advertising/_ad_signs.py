"""Гейт применимости ФЗ «О рекламе» — детектор признаков рекламы на сайте.

Используется проверками блока G: прежде чем вменять требования 38-ФЗ,
нужно понять, есть ли на сайте реклама вообще.

Признаки, что сайт РАЗМЕЩАЕТ рекламу (закон применим):
- найдены токены ОРД (erid=...) в ссылках или сетевых запросах;
- подключены сети, отдающие рекламные креативы (serves_creatives в trackers.yaml:
  Яндекс.Директ/РСЯ, Google AdSense/DoubleClick и т.п.).

НЕ признак размещения рекламы:
- пиксели рекламодателя (VK/Meta/TikTok Pixel) — сайт продвигает себя
  в чужих сетях; обязанность маркировки у владельца сайта не возникает.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from lawcheck.checks.cookies._tracker_matcher import TrackerHit, match_trackers
from lawcheck.crawler.snapshot import SiteSnapshot

# erid состоит из букв и цифр, обычно >=6 символов
_ERID_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{6,}$")


def erid_from_url(url: str) -> str | None:
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return None
    for value in qs.get("erid", []):
        if value and _ERID_VALUE_RE.match(value):
            return value
    return None


@dataclass
class AdSigns:
    erids: set[str] = field(default_factory=set)
    serving_networks: list[TrackerHit] = field(default_factory=list)  # отдают креативы
    advertiser_pixels: list[TrackerHit] = field(default_factory=list)  # пиксели самопродвижения

    @property
    def site_places_ads(self) -> bool:
        """Сайт размещает рекламу → требования 38-ФЗ применимы."""
        return bool(self.erids or self.serving_networks)


def detect_ad_signs(snapshot: SiteSnapshot) -> AdSigns:
    signs = AdSigns()
    for page in snapshot.pages:
        if page.error or page.status >= 400:
            continue
        for link in page.links:
            v = erid_from_url(link.url)
            if v:
                signs.erids.add(v)
        for req in page.network:
            v = erid_from_url(req.url)
            if v:
                signs.erids.add(v)

    for hit in match_trackers(snapshot):
        if hit.category != "ads":
            continue
        if hit.serves_creatives:
            signs.serving_networks.append(hit)
        else:
            signs.advertiser_pixels.append(hit)
    return signs
