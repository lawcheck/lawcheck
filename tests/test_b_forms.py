from lawcheck.checks.base import Severity
from lawcheck.checks.pd_152._form_classifier import (
    field_pd_categories, find_consent_checkbox, form_pd_categories, is_pd_form,
)
from lawcheck.checks.pd_152.form_consent import FormConsentCheck
from lawcheck.checks.pd_152.forms_inventory import FormsInventoryCheck
from lawcheck.crawler.snapshot import Form, FormField, PageSnapshot, SiteSnapshot


def _field(**kw) -> FormField:
    defaults = {"name": "", "type": "text", "placeholder": "", "label": "", "id": "", "checked": False, "required": False}
    defaults.update(kw)
    return FormField(**defaults)


def _form(fields: list[FormField], **kw) -> Form:
    defaults = {
        "action": "/submit", "method": "post", "fields": fields,
        "surrounding_text": "", "page_url": "https://example.com/contact",
        "has_policy_link": False,
    }
    defaults.update(kw)
    return Form(**defaults)


def _snap(forms: list[Form]) -> SiteSnapshot:
    return SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/contact", status=200, forms=forms),
    ])


# === field_pd_categories ===

def test_email_field_recognized_by_type():
    assert "email" in field_pd_categories(_field(type="email", name="x"))


def test_phone_field_recognized_by_name_ru():
    assert "phone" in field_pd_categories(_field(type="text", name="user_phone"))
    assert "phone" in field_pd_categories(_field(type="text", name="телефон"))


def test_full_name_field_recognized_by_label():
    assert "full_name" in field_pd_categories(_field(type="text", name="x", label="Ваше ФИО"))


def test_search_field_not_pd_even_with_email_type():
    assert field_pd_categories(_field(type="search", name="q")) == []


def test_hidden_csrf_field_not_pd():
    assert field_pd_categories(_field(type="hidden", name="csrf_token")) == []


# === is_pd_form ===

def test_search_form_is_not_pd_form():
    f = _form([_field(type="search", name="q")])
    assert is_pd_form(f) is False


def test_contact_form_is_pd_form():
    f = _form([
        _field(type="email", name="email"),
        _field(type="text", name="message"),
    ])
    assert form_pd_categories(f) == {"email"}


# === B1 inventory ===

def test_b1_no_pd_forms_emits_info():
    [finding] = FormsInventoryCheck().run(_snap([_form([_field(type="search", name="q")])]))
    assert finding.severity == Severity.INFO
    assert "не обнаружено" in finding.evidence


def test_b1_lists_each_pd_form():
    findings = FormsInventoryCheck().run(_snap([
        _form([_field(type="email", name="email")]),
        _form([_field(type="tel", name="phone"), _field(type="text", name="fio")]),
    ]))
    assert len(findings) == 2
    assert all(f.severity == Severity.INFO for f in findings)


# === B2 consent ===

def _pd_form_with_checkbox(*, label: str = "Согласен на обработку персональных данных",
                           checked: bool = False, has_policy_link: bool = True) -> Form:
    return _form(
        [_field(type="email", name="email"),
         _field(type="checkbox", name="consent", label=label, checked=checked)],
        has_policy_link=has_policy_link,
    )


def test_b2_no_consent_checkbox_critical():
    f = _form([_field(type="email", name="email")])
    [finding] = FormConsentCheck().run(_snap([f]))
    assert finding.severity == Severity.CRITICAL
    assert "нет чекбокса" in finding.evidence


def test_b2_consent_pre_checked_critical():
    [finding] = FormConsentCheck().run(_snap([_pd_form_with_checkbox(checked=True)]))
    assert finding.severity == Severity.CRITICAL
    assert "checked" in finding.evidence.lower() or "по умолчанию" in finding.evidence


def test_b2_consent_without_policy_link_warning():
    [finding] = FormConsentCheck().run(_snap([_pd_form_with_checkbox(has_policy_link=False)]))
    assert finding.severity == Severity.WARNING


def test_b2_proper_consent_ok():
    [finding] = FormConsentCheck().run(_snap([_pd_form_with_checkbox()]))
    assert finding.severity == Severity.OK


def test_b2_finds_consent_checkbox_by_marker():
    f = _form([
        _field(type="email", name="email"),
        _field(type="checkbox", name="agree", label="Я соглашаюсь с политикой обработки данных"),
    ], has_policy_link=True)
    cb = find_consent_checkbox(f)
    assert cb is not None and cb.name == "agree"


def test_b2_no_findings_when_no_pd_forms():
    assert FormConsentCheck().run(_snap([])) == []


# === Дедуп одинаковых форм на разных страницах (footer-форма и т.п.) ===

def test_same_form_across_pages_counted_once():
    """Структурно одинаковая форма на 3 страницах = одна находка B1/B2, не три."""
    def page(url):
        return PageSnapshot(url=url, status=200, forms=[
            _form([_field(type="email", name="email"), _field(type="text", name="phone")],
                  page_url=url)  # без чекбокса согласия → CRITICAL
        ])
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        page("https://example.com/"),
        page("https://example.com/about"),
        page("https://example.com/contact"),
    ])
    assert len(snap.unique_forms()) == 1
    b2_crit = [f for f in FormConsentCheck().run(snap) if f.severity == Severity.CRITICAL]
    assert len(b2_crit) == 1
    b1_info = [f for f in FormsInventoryCheck().run(snap) if f.severity == Severity.INFO]
    assert len(b1_info) == 1


def test_distinct_forms_not_merged():
    """Разные по структуре формы (contact vs newsletter) остаются раздельными."""
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200, forms=[
            _form([_field(type="email", name="email"), _field(type="text", name="phone")],
                  action="/contact"),
            _form([_field(type="email", name="subscriber")], action="/newsletter"),
        ]),
    ])
    assert len(snap.unique_forms()) == 2


def test_self_submitting_form_deduped_despite_varying_action():
    """Форма с пустым action резолвится в URL страницы → action скачет.
    Дедуп по видимым полям всё равно считает её один раз (регресс fistashki.org)."""
    fields = [_field(type="text", name="name"), _field(type="text", name="phone")]
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/", status=200,
                     forms=[_form(list(fields), action="https://example.com/")]),
        PageSnapshot(url="https://example.com/blog", status=200,
                     forms=[_form(list(fields), action="https://example.com/blog")]),
        PageSnapshot(url="https://example.com/blog", status=200,
                     forms=[_form(list(fields), action="https://example.com/blog#to_footer")]),
    ])
    assert len(snap.unique_forms()) == 1


def test_hidden_csrf_field_ignored_in_dedup():
    """Скрытое поле с per-page именем (CSRF) не должно ломать дедуп."""
    def form_with_token(tok):
        return _form([_field(type="text", name="phone"),
                      _field(type="hidden", name="csrf_" + tok)],
                     action="/")
    snap = SiteSnapshot(start_url="https://example.com/", pages=[
        PageSnapshot(url="https://example.com/a", status=200, forms=[form_with_token("a1")]),
        PageSnapshot(url="https://example.com/b", status=200, forms=[form_with_token("b2")]),
    ])
    assert len(snap.unique_forms()) == 1
