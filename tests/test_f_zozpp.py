from lawcheck.checks.base import Severity
from lawcheck.checks.zozpp._ecommerce import is_ecommerce_site
from lawcheck.checks.zozpp.delivery import DeliveryCheck
from lawcheck.checks.zozpp.oferta import OfertaCheck
from lawcheck.checks.zozpp.returns import ReturnsCheck
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot


def _snap(*, text: str = "", links: list[tuple[str, str]] | None = None) -> SiteSnapshot:
    page = PageSnapshot(
        url="https://shop.example/", status=200, text=text,
        links=[Link(url=u, text=t) for u, t in (links or [])],
    )
    return SiteSnapshot(start_url="https://shop.example/", pages=[page])


# === классификатор ===

def test_ecommerce_strong_marker_detected():
    snap = _snap(text="Добавьте товар в корзину, оформите заказ онлайн")
    ok, _ = is_ecommerce_site(snap)
    assert ok is True


def test_pure_blog_not_ecommerce():
    snap = _snap(text="Наш блог о технологиях. Читайте статьи, оставляйте комментарии.")
    ok, _ = is_ecommerce_site(snap)
    assert ok is False


def test_ecommerce_by_weak_markers_and_cart_link():
    snap = SiteSnapshot(start_url="https://x/", pages=[
        PageSnapshot(url="https://x/", status=200,
                     text="Категория. Товары. Цена. Доставка. Купить. Корзина. Скидка.",
                     links=[Link(url="https://x/cart", text="Корзина")]),
    ])
    ok, _ = is_ecommerce_site(snap)
    assert ok is True


# === F1 оферта ===

def test_f1_no_finding_on_non_ecommerce():
    assert OfertaCheck().run(_snap(text="блог")) == []


def test_f1_missing_oferta_critical():
    snap = _snap(
        text="Добавить в корзину. Оформить заказ.",
        links=[("https://shop.example/about", "О нас")],
    )
    [f] = OfertaCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_f1_oferta_link_found_ok():
    snap = _snap(
        text="Оформить заказ. Корзина",
        links=[("https://shop.example/oferta", "Договор оферты")],
    )
    [f] = OfertaCheck().run(snap)
    assert f.severity == Severity.OK


def test_f1_terms_url_also_matches():
    snap = _snap(
        text="Купить сейчас",
        links=[("https://shop.example/terms", "Подробнее")],  # текст невнятный, но URL ловит
    )
    [f] = OfertaCheck().run(snap)
    assert f.severity == Severity.OK


# === F2 доставка ===

def test_f2_no_delivery_warning_for_ecommerce():
    snap = _snap(
        text="Оформить заказ. Корзина",
        links=[("https://shop.example/about", "О нас")],
    )
    [f] = DeliveryCheck().run(snap)
    assert f.severity == Severity.WARNING


def test_f2_delivery_link_ok():
    snap = _snap(
        text="Оформить заказ",
        links=[("https://shop.example/delivery", "Условия доставки")],
    )
    [f] = DeliveryCheck().run(snap)
    assert f.severity == Severity.OK


# === F3 возврат ===

def test_f3_no_returns_warning():
    snap = _snap(
        text="Оформить заказ",
        links=[("https://shop.example/", "Главная")],
    )
    [f] = ReturnsCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert "26.1" in f.law_reference


def test_f3_returns_link_ok():
    snap = _snap(
        text="Оформить заказ",
        links=[("https://shop.example/return", "Возврат товаров")],
    )
    [f] = ReturnsCheck().run(snap)
    assert f.severity == Severity.OK
