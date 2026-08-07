"""Письмо-догонялка: дожимаем узкое место воронки scan_submit → оплата.

Лид оставил email на странице отчёта, но не оплатил Pro. Через 1–3 дня шлём
одно письмо про ЕГО отчёт: что нашли на его сайте, статьи закона, сколько
готовых текстов «Как исправить» ждёт в Pro. Тон — помощь, не давление.

Рамка по ст. 18 ФЗ «О рекламе»: тема и первый экран — про отчёт (сервисное,
ожидаемое письмо), Pro — вторым CTA; в футере обязательна ссылка отписки.

Запуск батча — через CLI-инструмент `lawcheck.tools.send_followups`.
"""
from __future__ import annotations

import html
import logging
from urllib.parse import urlencode, urlparse

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.db.models import Lead, Scan
from lawcheck.notify import mailer
from lawcheck.reporting import fines
from lawcheck.utils.contact import mask_contact

log = logging.getLogger(__name__)

# Держать в согласии с web/routes.py: сколько рецептов «Как исправить» открыто
# бесплатно. Импортировать оттуда нельзя — web.routes тянет reporting (цикл).
_FREE_RECIPES = 2
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение существительного по числу: 1 находка, 2 находки, 5 находок."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _host(url: str) -> str:
    """https://site.ru/page → site.ru (для заголовка письма)."""
    netloc = urlparse(url).netloc or url
    return netloc[4:] if netloc.startswith("www.") else netloc


def _with_utm(url: str, campaign: str = "followup") -> str:
    """UTM-метки на ссылки письма — чтобы в Метрике видеть воронку
    письмо → отчёт → оплата отдельно от рекламы и органики."""
    params = urlencode({"utm_source": "email", "utm_medium": "email",
                        "utm_campaign": campaign})
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{params}"


def _unique_laws(findings) -> str:
    """Уникальные ссылки на нормы из находок — «ст. 18.1 152-ФЗ, ст. 5 О рекламе»."""
    seen: list[str] = []
    for f in findings:
        ref = (f.law_reference or "").strip()
        if ref and ref not in seen:
            seen.append(ref)
    return ", ".join(seen[:4])


def build_context(lead: Lead, scan: Scan) -> dict:
    """Плейсхолдеры письма из данных лида и его скана (см. followup-email-kit.md)."""
    problems = sorted(
        (f for f in scan.findings if f.severity != "ok"),
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id),
    )
    critical = [f for f in problems if f.severity == "critical"]
    with_recipe = [f for f in problems if f.recommendation]
    locked = max(0, len(with_recipe) - _FREE_RECIPES)
    base = settings.site_base_url.rstrip("/")
    return {
        "site": _host(lead.url or scan.url),
        "problems": len(problems),
        "critical": len(critical),
        "top3": [f.title for f in problems[:3]],
        "laws": _unique_laws(problems),
        "locked": locked,
        # UTM только на «рабочие» ссылки; отписка — служебная, метки не нужны.
        "report_url": _with_utm(f"{base}/report/{scan.id}"),
        "unsub_url": f"{base}/unsubscribe/{lead.unsub_token}",
        "risk": fines.risk_total(scan.findings),
    }


def render(ctx: dict) -> tuple[str, str, str]:
    """(subject, html_body, text_body) из контекста. Тема — «польза» (вариант B
    из кита): транзакционная рамка, безопаснее по рекламе, выше open rate."""
    site = ctx["site"]
    n_prob = ctx["problems"]
    n_find = _plural(n_prob, "находка", "находки", "находок")
    subject = f"Ваш отчёт по {site}: {n_prob} {n_find} и как их закрыть"

    top3 = ctx["top3"]
    laws = ctx["laws"]
    locked = ctx["locked"]
    risk = ctx["risk"]
    prob_word = _plural(n_prob, "нарушение", "нарушения", "нарушений")
    n_crit = ctx["critical"]
    crit_word = _plural(n_crit, "критичное", "критичных", "критичных")
    crit_line = (f", из них {n_crit} {crit_word}" if n_crit else "")
    locked_word = _plural(locked, "готовый текст", "готовых текста", "готовых текстов")
    risk_line = ""
    if risk and risk.get("max"):
        risk_line = (f"Суммарный риск штрафа по найденным нарушениям — "
                     f"до {int(risk['max']):,} ₽.".replace(",", " "))

    # --- text/plain ---
    text_lines = [
        "Здравствуйте!",
        "",
        f"Вы запускали проверку сайта {site} на LawCheck. Собрали для вас итог,",
        "чтобы не потерялся.",
        "",
        f"Что нашли: {n_prob} {prob_word}{crit_line}. Самое важное:",
    ]
    text_lines += [f"  — {t}" for t in top3]
    text_lines.append("")
    if laws:
        text_lines.append(
            f"Это зона {laws} — по ней Роскомнадзор штрафует бизнес. "
            "Проверка сама по себе штраф не убирает — нарушения надо закрыть.")
    if risk_line:
        text_lines.append(risk_line)
    text_lines.append("")
    if locked:
        # Цену тут не называем — её держит лестница тарифов ниже, иначе
        # «990 ₽» звучит в письме дважды подряд.
        text_lines.append(
            f"В бесплатном отчёте открыты первые рекомендации. Ещё {locked} "
            f"{locked_word} «Как исправить» (политика ПДн, тексты согласий, "
            f"cookie-баннер под {site}) откроются на Pro.")
    text_lines += [
        "",
        # Лестница Pro → персональный аудит: две ступени, потому что аудит и
        # прежнее «сопровождение от основателя» — одна и та же работа руками,
        # у неё теперь есть прайс. Состав держать в согласии с pricing.html,
        # обещание про подпись юриста — с п. 2.1 оферты.
        "Как закрыть найденное — два варианта.",
        "",
        f"Pro, 990 ₽/мес — готовые тексты исправлений под {site},",
        "шаблоны Политики, согласий и уведомления в РКН, еженедельный мониторинг",
        "и PDF-заключение с подписью юриста.",
        "",
        "Персональный аудит, 35 000 ₽ разово — беру проект руками: разбираю формы,",
        "метрики и сторонние скрипты, готовлю документы под ваши процессы,",
        "уведомление в РКН и час консультации. Ответьте на это письмо,",
        f"и я расскажу, что войдёт в аудит {site}.",
        "",
        f"Открыть отчёт: {ctx['report_url']}",
        "",
        "Не готовы платить — тоже ответьте: подскажу, с чего начать, бесплатно.",
        "",
        "— Максим Подольский, LawCheck · проверка сайтов на 152-ФЗ и смежные законы",
        f"Вы получили письмо, потому что оставили email для отчёта по {site}.",
        f"Отписаться: {ctx['unsub_url']}",
    ]
    text_body = "\n".join(text_lines)

    # --- text/html ---
    e = html.escape
    top3_html = "".join(f"<li>{e(t)}</li>" for t in top3)
    parts = [
        "<p>Здравствуйте!</p>",
        f"<p>Вы запускали проверку сайта <b>{e(site)}</b> на LawCheck. "
        "Собрали для вас итог, чтобы не потерялся.</p>",
        f"<p><b>Что нашли: {n_prob} {prob_word}{e(crit_line)}.</b> "
        "Самое важное:</p>",
        f"<ul>{top3_html}</ul>",
    ]
    if laws:
        parts.append(
            f"<p>Это зона {e(laws)} — по ней Роскомнадзор штрафует бизнес. "
            "Проверка сама по себе штраф не убирает — нарушения надо закрыть.</p>")
    if risk_line:
        parts.append(f"<p>{e(risk_line)}</p>")
    if locked:
        parts.append(
            f"<p>В бесплатном отчёте открыты первые рекомендации. Ещё "
            f"<b>{locked}</b> {locked_word} «Как исправить» (политика ПДн, "
            f"тексты согласий, cookie-баннер под {e(site)}) откроются на Pro.</p>")
    parts += [
        # Лестница тарифов — см. комментарий в text/plain выше. Врезкой, чтобы
        # не слипалась с перечнем находок: здесь голос меняется с «мы» на «я».
        "<p><b>Как закрыть найденное — два варианта.</b></p>"
        '<div style="border-left:3px solid #d0d0d0;padding:2px 0 2px 14px;'
        'margin:20px 0">'
        f"<p><b>Pro, 990 ₽/мес</b> — готовые тексты исправлений "
        f"под {e(site)}, шаблоны Политики, согласий и уведомления в РКН, "
        "еженедельный мониторинг и PDF-заключение с подписью юриста.</p>"
        "<p><b>Персональный аудит, 35 000 ₽ разово</b> — беру проект руками: "
        "разбираю формы, метрики и сторонние скрипты, готовлю документы "
        "под ваши процессы, уведомление в РКН и час консультации. Ответьте "
        f"на это письмо, и я расскажу, что войдёт в аудит {e(site)}.</p></div>",
        f'<p><a href="{e(ctx["report_url"])}">Открыть отчёт →</a></p>',
        "<p>Не готовы платить — тоже ответьте: подскажу, с чего начать, "
        "бесплатно.</p>",
        '<p style="color:#888;font-size:13px">— Максим Подольский, LawCheck · '
        "проверка сайтов на 152-ФЗ и смежные законы<br>"
        f"Вы получили письмо, потому что оставили email для отчёта по {e(site)}. "
        f'<a href="{e(ctx["unsub_url"])}">Отписаться</a>.</p>',
    ]
    return subject, "".join(parts), text_body


def send_one(lead: Lead, scan: Scan) -> bool:
    """Собрать и отправить письмо одному лиду; при успехе — отметить `mailed_at`."""
    subject, html_body, text_body = render(build_context(lead, scan))
    ok = mailer.send_email(lead.email, subject, html_body, text_body)
    if ok:
        repo.mark_lead_mailed(lead.id)
    else:
        log.warning("followup: письмо лиду %s не ушло — mailed_at не ставим",
                    mask_contact(lead.email))
    return ok


def run(limit: int = 50, delay_hours: int = 20, max_age_days: int = 14,
        dry_run: bool = False) -> dict:
    """Батч: разослать письма-догонялки подходящим лидам. dry_run — только
    показать, кому и что, не отправляя и не отмечая. Возвращает сводку."""
    leads = repo.leads_to_followup(delay_hours, max_age_days, limit)
    sent = skipped = 0
    for lead in leads:
        scan = repo.get_scan(lead.scan_id)
        if scan is None:
            skipped += 1
            continue
        if dry_run:
            subject, _, _ = render(build_context(lead, scan))
            log.info("followup[dry] → %s | %s", mask_contact(lead.email), subject)
            continue
        if send_one(lead, scan):
            sent += 1
        else:
            skipped += 1
    return {"candidates": len(leads), "sent": sent, "skipped": skipped,
            "dry_run": dry_run}
