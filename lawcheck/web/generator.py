"""Бесплатный генератор Политики ПДн: /politika-obrabotki-pd.

Тот же шаблон, что у платного черновика в отчёте (reporting/policy_draft), но
факты вписывает сам владелец, а не скан. Разница с Pro – в этом: здесь то, что
человек помнит о своём сайте, в Pro – то, что на сайте нашёл скан. Под готовой
политикой – форма скана с подставленным доменом.

Подключается без гейта SEO_ENABLED, как и /reestr-rkn.
"""
import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from lawcheck.db import repo
from lawcheck.dictionaries import loader
from lawcheck.external import egrul
from lawcheck.notify import mailer, telegram
from lawcheck.reporting import policy_draft
from lawcheck.utils import consent
from lawcheck.utils.contact import mask_contact
from lawcheck.utils.email import valid_email
from lawcheck.utils.inn_ogrn import is_valid_inn
from lawcheck.web import ratelimit

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # type: ignore[assignment]  # задаётся из routes.py

PATH = "/politika-obrabotki-pd"

# Каждая генерация – запрос в ЕГРЮЛ, письмо и строка в consent_log.
_RL_GENERATOR = ratelimit.Limit(limit=10, window_sec=3600)

_DOMAIN_RE = re.compile(r"^[a-zа-яё0-9-]+(\.[a-zа-яё0-9-]+)+$", re.IGNORECASE)
_OWNER_FIELDS = ("purposes", "terms", "contacts", "resp")
_MAX_FIELD = 300

CATEGORIES = [(key, label) for key, label in policy_draft._CATEGORY_RU.items()]


def services() -> list[dict]:
    """Сервисы для галочек – справочник трекеров, по которому работает скан."""
    return [{"name": t["name"], "foreign": t["jurisdiction"] != "ru"}
            for t in loader.trackers()]


def normalize_domain(raw: str) -> str:
    """'https://www.Shop.ru/about' → 'shop.ru'; пустая строка, если не домен."""
    host = re.sub(r"^https?://", "", raw.strip(), flags=re.IGNORECASE)
    host = host.split("/", 1)[0].split("?", 1)[0].lower().removeprefix("www.")
    return host if _DOMAIN_RE.match(host) else ""


def build_facts(domain: str, name: str, inn: str, ogrn: str,
                categories: list[str], chosen: list[str]) -> dict:
    """Факты в формате policy_draft.extract_facts – из формы вместо скана."""
    known = {s["name"]: s["foreign"] for s in services()}
    picked = [n for n in chosen if n in known]
    return {
        "url": f"https://{domain}", "domain": domain,
        "operator_name": name or None, "inn": inn or None, "ogrn": ogrn or None,
        "categories": [c for c, _ in CATEGORIES if c in categories],
        "trackers_foreign": [n for n in picked if known[n]],
        "trackers_ru": [n for n in picked if not known[n]],
    }


def _render(request: Request, **ctx):
    base = {"categories": CATEGORIES, "services": services(), "form": {},
            "errors": [], "document": None}
    return templates.TemplateResponse(request, "generator.html", base | ctx)


@router.get(PATH, response_class=HTMLResponse)
async def generator_page(request: Request):
    return _render(request)


@router.post(PATH, response_class=HTMLResponse)
async def generator_submit(request: Request, bg: BackgroundTasks):
    ratelimit.enforce(request, "policy_generator", _RL_GENERATOR,
                      message="Слишком много запросов с этого адреса. Попробуйте через час.")
    data = await request.form()
    form: dict[str, Any] = {
        "domain": str(data.get("domain", "")).strip()[:200],
        "email": str(data.get("email", "")).strip().lower()[:200],
        "inn": re.sub(r"\D", "", str(data.get("inn", "")))[:12],
        "name": str(data.get("name", "")).strip()[:_MAX_FIELD],
        "categories": [str(v) for v in data.getlist("categories")],
        "services": [str(v) for v in data.getlist("services")],
    }
    for f in _OWNER_FIELDS:
        form[f] = str(data.get(f, "")).strip()[:_MAX_FIELD]

    domain = normalize_domain(form["domain"])
    errors = []
    if not domain:
        errors.append("Укажите адрес сайта, например mystore.ru.")
    if not valid_email(form["email"]):
        errors.append("Укажите почту – на неё придёт копия политики.")
    if form["inn"] and not is_valid_inn(form["inn"]):
        errors.append("ИНН не проходит проверку: у организации 10 цифр, у ИП – 12.")
    if not consent.checked(str(data.get("pd_consent", ""))):
        errors.append("Отметьте согласие на обработку данных – без него мы не можем "
                      "принять форму.")
    if errors:
        return _render(request, form=form, errors=errors)

    await asyncio.to_thread(repo.log_consent, "policy_generator", "",
                            ratelimit.client_ip(request))
    ogrn = ""
    if form["inn"]:
        found = await asyncio.to_thread(egrul.lookup_by_inn, form["inn"])
        if found.record is not None:
            ogrn = found.record.ogrn
            form["name"] = form["name"] or found.record.short_name or found.record.full_name

    facts = build_facts(domain, form["name"], form["inn"], ogrn,
                        form["categories"], form["services"])
    owner = {f: form[f] for f in _OWNER_FIELDS}
    document = policy_draft.render_document(facts, owner, from_scan=False)

    is_new = await asyncio.to_thread(repo.create_lead, "generator:politika",
                                     facts["url"], form["email"])
    bg.add_task(mailer.send_email, form["email"],
                f"Политика обработки персональных данных для {domain}",
                _email_body(domain, document))
    if is_new:
        log.info("generator: %s, сайт %s", mask_contact(form["email"]), domain)
        # Почту в Telegram не шлём – только домен.
        bg.add_task(telegram.notify_owner,
                    f"📄 Сгенерировали политику для <b>{telegram.esc(domain)}</b>")
    return _render(request, form=form, document=document, domain=domain)


def _email_body(domain: str, document: str) -> str:
    # В почтовых клиентах <style> режется – бланки подсвечиваем инлайном.
    doc = document.replace(
        'class="blank"',
        'style="background:#fdf1f0;color:#a11717;font-weight:600;padding:0 4px"')
    return (f"<p>Здравствуйте! Вот политика обработки персональных данных для "
            f"{domain}, которую вы собрали на lawchek.ru. Выделенные места впишите сами "
            f"и сверьте текст с юристом перед публикацией.</p>{doc}"
            f"<hr><p>Политика описывает то, что вы указали в форме. Совпадает ли она с "
            f"тем, что на сайте на самом деле, – поля форм, счётчики, согласия – "
            f"покажет бесплатная проверка за минуту: "
            f"https://lawchek.ru/?utm_source=generator&utm_medium=email</p>")
