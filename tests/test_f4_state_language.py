from lawcheck.checks.base import Severity
from lawcheck.checks.zozpp.state_language import StateLanguageCheck
from lawcheck.crawler.snapshot import Form, FormField, Link, PageSnapshot, SiteSnapshot

_SHOP_TEXT = "Добавить в корзину. Оформить заказ."


def _snap(
    *,
    text: str = _SHOP_TEXT,
    title: str = "",
    html: str = "",
    links: list[tuple[str, str]] | None = None,
    forms: list[Form] | None = None,
    start_url: str = "https://magazin.example/",
) -> SiteSnapshot:
    page = PageSnapshot(
        url=start_url, status=200, title=title, text=text, html=html,
        links=[Link(url=u, text=t) for u, t in (links or [])],
        forms=forms or [],
    )
    return SiteSnapshot(start_url=start_url, pages=[page])


def _form(*fields: FormField) -> Form:
    return Form(action="/send", method="post", fields=list(fields),
                page_url="https://magazin.example/")


# === гейт применимости ===

def test_no_finding_without_shop_and_forms():
    assert StateLanguageCheck().run(_snap(text="Блог о технологиях")) == []


def test_shop_in_russian_is_ok():
    snap = _snap(links=[("/dostavka", "Доставка"), ("/katalog", "Каталог")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


# === слой 1: меню, кнопки, заголовки ===

def test_latin_menu_on_shop_is_warning():
    snap = _snap(links=[("/sale", "SALE"), ("/delivery", "Delivery")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert "sale" in f.evidence
    assert {e["russian"] for e in f.extra["examples"]} == {"распродажа", "доставка"}


def test_button_label_counted():
    snap = _snap(html='<button class="b">Buy now</button>')
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert f.extra["examples"][0]["place"] == "кнопка"


def test_submit_value_counted():
    snap = _snap(html='<input type="submit" value="Order">')
    [f] = StateLanguageCheck().run(snap)
    assert [e["word"] for e in f.extra["examples"]] == ["order"]


def test_page_title_counted():
    snap = _snap(title="Checkout")
    [f] = StateLanguageCheck().run(snap)
    assert f.extra["examples"][0]["place"] == "заголовок страницы"


def test_latin_menu_without_shop_signs_is_info():
    """Форма заявки есть, признаков продажи нет — вменять рано."""
    snap = _snap(text="Оставьте заявку, мы перезвоним",
                 links=[("/price", "Price")],
                 forms=[_form(FormField(name="phone", type="tel", label="Телефон"))])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.INFO


# === слой 2: поля формы ===

def test_only_form_fields_is_info():
    snap = _snap(links=[("/dostavka", "Доставка")],
                 forms=[_form(FormField(name="name", type="text", label="Name"),
                              FormField(name="email", type="email", placeholder="Email"))])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.INFO
    assert {e["word"] for e in f.extra["examples"]} == {"name", "email"}


# === исключения ===

def test_label_repeating_site_name_not_counted():
    """«GIFT HOUSE» на gifthouse.ru — фирменное наименование, не «подарок»."""
    snap = _snap(start_url="https://gifthouse.ru/", links=[("/", "Gift House")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_single_dictionary_word_still_counted_on_similar_domain():
    """А одиночный «SALE» на sale-shop.ru — распродажа, имя сайта он не повторяет."""
    snap = _snap(start_url="https://sale-shop.ru/", links=[("/sale", "SALE")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING


def test_trademark_sign_excludes_label():
    snap = _snap(links=[("/", "SALE™")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_email_address_not_counted():
    snap = _snap(links=[("mailto:order@magazin.example", "order@magazin.example")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_word_repeated_on_many_pages_reported_once():
    pages = [
        PageSnapshot(url=f"https://magazin.example/p{i}", status=200, text=_SHOP_TEXT,
                     links=[Link(url="/sale", text="SALE")])
        for i in range(5)
    ]
    snap = SiteSnapshot(start_url="https://magazin.example/", pages=pages)
    [f] = StateLanguageCheck().run(snap)
    assert len(f.extra["examples"]) == 1


# === товарные знаки: проверено на живых магазинах 10.09.2026 ===

def test_brand_in_title_case_pair_not_counted():
    """«New Balance», «Free Lance», «No Name» — бренды в меню, а не надписи."""
    snap = _snap(links=[("/catalog/nb", "New Balance"), ("/catalog/fl", "Free Lance")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_brand_section_link_ignored():
    snap = _snap(links=[("/catalog/brands/gift_box/", "GIFT BOX")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_uppercase_promo_still_counted():
    """А «SPECIAL OFFER SALE -80%» — распродажа, и её вменяем."""
    snap = _snap(links=[("/catalog/sale/", "SPECIAL OFFER SALE -80%")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert [e["word"] for e in f.extra["examples"]] == ["sale"]


def test_lowercase_second_word_still_counted():
    snap = _snap(html="<button>Buy now</button>")
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING


def test_field_vocabulary_not_applied_to_links():
    """Слово «name» в ссылке — это чей-то «No Name», а не подпись поля."""
    snap = _snap(links=[("/catalog/nn", "NO NAME")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK


def test_brand_in_domain_does_not_blind_check():
    """На brandshop.ru слово «brand» есть в каждом абсолютном href — но это хост,
    а не раздел брендов, и меню проверять всё равно надо."""
    snap = _snap(start_url="https://brandshop.ru/",
                 links=[("https://brandshop.ru/sale/", "SALE")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.WARNING


def test_brand_section_in_path_still_ignored():
    snap = _snap(start_url="https://brandshop.ru/",
                 links=[("https://brandshop.ru/catalog/brands/gift_box/", "GIFT BOX")])
    [f] = StateLanguageCheck().run(snap)
    assert f.severity == Severity.OK
