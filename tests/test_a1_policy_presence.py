from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152.policy_presence import PolicyPresenceCheck
from lawcheck.crawler.snapshot import Link, PageSnapshot, SiteSnapshot


def _page(url: str, links: list[tuple[str, str]]) -> PageSnapshot:
    return PageSnapshot(
        url=url, status=200, links=[Link(url=u, text=t) for u, t in links]
    )


def test_policy_link_found_everywhere():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy", "Политика конфиденциальности")]),
        _page("https://example.com/about", [("https://example.com/privacy", "Политика конфиденциальности")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_policy_link_missing_on_some_pages():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy", "Политика обработки персональных данных")]),
        _page("https://example.com/contacts", [("https://example.com/about", "О нас")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.WARNING
    assert "contacts" in f.evidence


def test_policy_link_completely_missing():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/about", "О нас")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_policy_detected_by_url_when_text_is_useless():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page("https://example.com/", [("https://example.com/privacy-policy", "Подробнее")]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_short_form_konfidencialnost_detected():
    snap = SiteSnapshot(start_url="https://habr.com/", pages=[
        _page("https://habr.com/", [
            ("https://account.habr.com/info/confidential/", "Конфиденциальность"),
        ]),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK


def test_no_pages_means_critical():
    snap = SiteSnapshot(start_url="https://example.com/", pages=[])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


_POLICY_BODY = (
    "ПОЛОЖЕНИЕ ОБ ОБРАБОТКЕ ПЕРСОНАЛЬНЫХ ДАННЫХ ООО «РОМАШКА». Настоящее "
    "Положение подготовлено в соответствии с п. 2 ч. 1 ст. 18.1 Федерального "
    "закона «О персональных данных» №152-ФЗ. Цели обработки персональных "
    "данных: исполнение договора с клиентом и информирование о статусе заказа. "
    "Правовым основанием обработки является согласие субъекта персональных "
    "данных, а также заключённый с ним договор. Оператор обеспечивает защиту "
    "прав субъектов персональных данных и прекращает обработку с последующим "
    "уничтожением персональных данных по достижении целей обработки. " * 4
)


def _page_with_text(url: str, links: list[tuple[str, str]], text: str = "",
                    title: str = "") -> PageSnapshot:
    return PageSnapshot(
        url=url, status=200, text=text, title=title,
        links=[Link(url=u, text=t) for u, t in links],
    )


def test_policy_found_by_body_when_link_text_is_unrecognizable():
    """«Политика безопасности» — документ на месте, ссылку по названию не опознать."""
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page_with_text("https://example.com/", [("https://example.com/info/security", "Политика безопасности")]),
        _page_with_text("https://example.com/info/security", [], _POLICY_BODY),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.OK
    assert f.extra["policy_url"] == "https://example.com/info/security"


def test_random_mention_of_pdn_is_not_a_policy():
    """Упоминание ПДн в статье блога Политикой не считается."""
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page_with_text("https://example.com/", [("https://example.com/blog", "Блог")]),
        _page_with_text("https://example.com/blog", [], "Мы бережно относимся к обработке персональных данных."),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


_BLOG_ARTICLE = (
    "Как выбрать ответственного за обработку персональных данных в компании. "
    "Оператор обязан назначить ответственного по п. 1 ч. 1 ст. 18.1 закона "
    "о персональных данных. Субъект персональных данных вправе обратиться "
    "к нему с запросом. Разбираем на примерах, кого назначают и как оформить "
    "приказ, если согласие на обработку берётся при оформлении заказа. " * 8
)

_TELECOM_TARIFFS = (
    "Тарифы на связь. Оператор связи ООО «Телеком» предлагает выгодные тарифы. "
    "Абонентская плата 18.10 руб в сутки. Подключение бесплатно, зона покрытия "
    "по всей области, скорость до 100 Мбит. Оформить заявку можно онлайн. "
    "Мы обеспечиваем защиту при обработке персональных данных абонентов. " * 8
)


def test_long_blog_article_about_pdn_is_not_a_policy():
    """Статья про обработку ПДн набирает маркеры не хуже документа, но
    называется иначе — без самоназвания в Политику не записываем."""
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page_with_text("https://example.com/", [("https://example.com/blog/otvetstvennyj", "Читать")]),
        _page_with_text("https://example.com/blog/otvetstvennyj", [], _BLOG_ARTICLE,
                        title="Ответственный за обработку персональных данных"),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_tariff_page_is_not_a_policy():
    """«Оператор связи» и цена «18.10» — не признаки Политики."""
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page_with_text("https://example.com/", [("https://example.com/tarify", "Тарифы")]),
        _page_with_text("https://example.com/tarify", [], _TELECOM_TARIFFS, title="Тарифы на связь"),
    ])
    [f] = PolicyPresenceCheck().run(snap)
    assert f.severity == Severity.CRITICAL


def test_dogadka_po_telu_ne_uezzhaet_v_proverki_soderzhaniya():
    """A2/A3 читают find_policy_links и должны молчать, когда ссылки нет:
    судить о разделах и дате документа, найденного догадкой, нельзя."""
    from lawcheck.checks.pd_152._policy_finder import find_policy_links

    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        _page_with_text("https://example.com/", [("https://example.com/info/security", "Политика безопасности")]),
        _page_with_text("https://example.com/info/security", [], _POLICY_BODY),
    ])
    assert find_policy_links(snap) == []
    assert PolicyPresenceCheck().run(snap)[0].severity == Severity.OK
