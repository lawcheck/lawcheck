from lawcheck.checks.base import Severity
from lawcheck.checks.media.age_marking import AgeMarkingCheck, _is_media_site
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot


def _media_snap(*, text: str = "", labels_in_text: str = "") -> SiteSnapshot:
    """Снапшот с 15 article-ссылками + опционально возрастной маркировкой."""
    return SiteSnapshot(start_url="https://media.ru/", pages=[
        PageSnapshot(
            url="https://media.ru/", status=200,
            text=text + " " + labels_in_text,
            links=[Link(url=f"https://media.ru/news/{i}", text=f"Новость {i}") for i in range(15)],
        ),
    ])


def _non_media_snap() -> SiteSnapshot:
    return SiteSnapshot(start_url="https://shop.ru/", pages=[
        PageSnapshot(url="https://shop.ru/", status=200, text="магазин товаров",
                     links=[Link(url="https://shop.ru/products", text="каталог")]),
    ])


# === классификатор ===

def test_classifier_detects_media_by_article_links():
    ok, _ = _is_media_site(_media_snap())
    assert ok is True


def test_classifier_skips_shop():
    ok, _ = _is_media_site(_non_media_snap())
    assert ok is False


# === H1 ===

def test_h1_no_finding_for_non_media():
    assert AgeMarkingCheck().run(_non_media_snap()) == []


def test_h1_media_without_age_marking_warning():
    [f] = AgeMarkingCheck().run(_media_snap(text="редакция, журналисты, опубликовано"))
    assert f.severity == Severity.WARNING
    assert "маркировка" in f.evidence.lower()


def test_h1_media_with_age_label_ok():
    [f] = AgeMarkingCheck().run(_media_snap(labels_in_text="Материал 16+ для взрослой аудитории"))
    assert f.severity == Severity.OK
    assert "16+" in f.evidence


def test_h1_multiple_labels_extracted():
    [f] = AgeMarkingCheck().run(_media_snap(labels_in_text="разделы 0+, 12+, 18+ есть"))
    assert f.severity == Severity.OK
    assert "0+" in f.evidence and "12+" in f.evidence and "18+" in f.evidence
