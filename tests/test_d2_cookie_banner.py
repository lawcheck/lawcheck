from lawcheck.checks.base import Severity
from lawcheck.checks.cookies.banner import CookieBannerCheck
from lawcheck.crawler.snapshot import CookieBanner, NetworkRequest, PageSnapshot, SiteSnapshot


def _page(*, banner: CookieBanner | None = None, trackers: list[str] | None = None) -> PageSnapshot:
    reqs = [NetworkRequest(url=u, domain=u.split("/")[2], resource_type="script") for u in (trackers or [])]
    return PageSnapshot(url="https://example.com/", status=200, cookie_banner=banner, network=reqs)


def _snap(page: PageSnapshot) -> SiteSnapshot:
    return SiteSnapshot(start_url="https://example.com/", pages=[page])


def test_no_banner_no_pd_trackers_is_ok():
    [f] = CookieBannerCheck().run(_snap(_page()))
    assert f.severity == Severity.OK


def test_no_banner_but_pd_trackers_is_critical():
    [f] = CookieBannerCheck().run(_snap(_page(trackers=["https://www.google-analytics.com/g/collect"])))
    assert f.severity == Severity.CRITICAL
    assert "Cookie-баннер" in f.evidence


def test_banner_without_decline_and_pd_trackers_is_critical():
    banner = CookieBanner(text="Мы используем cookies", buttons=["Принять", "Подробнее"], has_decline_option=False)
    [f] = CookieBannerCheck().run(_snap(_page(banner=banner, trackers=["https://www.google-analytics.com/g/collect"])))
    assert f.severity == Severity.CRITICAL
    assert "нет варианта отказа" in f.evidence


def test_banner_with_decline_but_trackers_fire_early_is_warning():
    banner = CookieBanner(text="Мы используем cookies", buttons=["Принять", "Отклонить"], has_decline_option=True)
    [f] = CookieBannerCheck().run(_snap(_page(banner=banner, trackers=["https://www.google-analytics.com/g/collect"])))
    assert f.severity == Severity.WARNING


def test_banner_with_decline_and_no_pd_trackers_is_ok():
    banner = CookieBanner(text="cookies", buttons=["Принять", "Отклонить"], has_decline_option=True)
    [f] = CookieBannerCheck().run(_snap(_page(banner=banner)))
    assert f.severity == Severity.OK


def test_only_ru_trackers_still_require_banner():
    # Яндекс.Метрика ставит идентификаторы → нужен баннер
    [f] = CookieBannerCheck().run(_snap(_page(trackers=["https://mc.yandex.ru/metrika/tag.js"])))
    assert f.severity == Severity.CRITICAL
