"""Экспорт полного отчёта (без замков) в PDF или HTML.

Для отправки платящему клиенту вручную: собирает диагноз + ВСЕ рекомендации
«Как исправить» (в бесплатном отчёте открыты только первые несколько), считает
риск штрафа и складывает всё в один файл со встроенными стилями.

Формат по расширению output: .pdf → рендер через headless Chromium (Playwright,
предустановлен в образе), иначе — .html. По умолчанию — PDF.

Запуск в контейнере web (там есть зависимости, БД и Chromium):

    docker compose exec web python -m lawcheck.tools.export_report <scan_id> [output]

Пример (готовый PDF для клиента):

    docker compose exec web python -m lawcheck.tools.export_report \
        ab048c4315344898ada258415ce4017f /tmp/report.pdf
    docker compose cp web:/tmp/report.pdf ./report-client.pdf
"""
from __future__ import annotations

import html
import sys
from collections import defaultdict

from lawcheck.db import repo
from lawcheck.reporting import fines

# Секции и порядок — держим в согласии с web/routes.py (_BLOCK_DEFS, _SEVERITY_ORDER).
_BLOCK_DEFS = [
    ("Политика обработки ПДн", ["A1", "A2", "A3"]),
    ("Формы и согласия", ["B1", "B2"]),
    ("Cookies и трекеры", ["D1", "D2"]),
    ("Реквизиты владельца", ["E1", "E2"]),
    ("Реестр операторов РКН", ["C2"]),
    ("ЗОЗПП и Правила продажи", ["F1", "F2", "F3"]),
    ("ФЗ «О рекламе»", ["G1", "G2", "G3"]),
    ("Защита детей (436-ФЗ)", ["H1"]),
]
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
_SEVERITY_RU = {"critical": "критично", "warning": "внимание", "info": "инфо", "ok": "ок"}


def _money(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _finding_html(f) -> str:
    parts = [f'<div class="finding {_esc(f.severity)}">']
    parts.append(
        f'<div class="head"><span class="pill {_esc(f.severity)}">'
        f'{_SEVERITY_RU.get(f.severity, _esc(f.severity))}</span>'
        f'<span class="title">{_esc(f.title)}</span></div>'
    )
    if f.law_reference or f.check_id:
        law = " · ".join(p for p in (_esc(f.law_reference), _esc(f.check_id)) if p)
        parts.append(f'<div class="law">{law}</div>')
    if f.severity != "ok":
        g = fines.group_for(f.check_id)
        if g:
            parts.append(
                f'<div class="fine">риск штрафа: <strong>{_money(g["min"])}–'
                f'{_money(g["max"])} ₽</strong> <span class="koap">({_esc(g["koap"])})</span></div>'
            )
    if f.location:
        parts.append(f'<div class="where">где: {_esc(f.location)}</div>')
    if f.evidence:
        parts.append(f'<div class="quote">{_esc(f.evidence)}</div>')
    if f.recommendation:
        parts.append(
            f'<div class="rec"><strong>Как исправить:</strong> {_esc(f.recommendation)}</div>'
        )
    parts.append("</div>")
    return "".join(parts)


def render(scan_id: str) -> str:
    scan = repo.get_scan(scan_id)
    if scan is None:
        raise SystemExit(f"scan {scan_id} not found")

    by_prefix: dict[str, list] = defaultdict(list)
    counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    for f in scan.findings:
        by_prefix[f.check_id.split(".")[0]].append(f)
        counts[f.severity] = counts.get(f.severity, 0) + 1

    total = sum(counts.values())
    compliance = round(counts["ok"] / total * 100) if total else 0
    risk = fines.risk_total(scan.findings)

    blocks_html = []
    for title, prefixes in _BLOCK_DEFS:
        items = [f for p in prefixes for f in by_prefix.get(p, [])]
        if not items:
            continue
        items.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.check_id))
        cards = "".join(_finding_html(f) for f in items)
        blocks_html.append(f'<section class="block"><h2>{_esc(title)}</h2>{cards}</section>')

    risk_line = ""
    if risk:
        risk_line = (
            f'<div class="risk">Суммарный риск штрафа: <strong>до {_money(risk["max"])} ₽</strong> '
            f'(ориентировочно по {len(risk["groups"])} стат. КоАП РФ, от {_money(risk["min"])} ₽). '
            f'Оценка порядка риска, не юридическая консультация.</div>'
        )

    created = scan.created_at.strftime("%d.%m.%Y %H:%M") if scan.created_at else ""
    return _PAGE.format(
        url=_esc(scan.url),
        scan_short=_esc(scan.id[:8]),
        created=_esc(created),
        compliance=compliance,
        crit=counts["critical"],
        warn=counts["warning"],
        info=counts["info"],
        ok=counts["ok"],
        risk_line=risk_line,
        blocks="".join(blocks_html),
    )


_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Отчёт LawCheck · {url}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #1a1a1a;
    max-width: 780px; margin: 0 auto; padding: 32px 20px; line-height: 1.5; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .stamp {{ color: #777; font-size: 13px; margin-bottom: 16px; }}
  .summary {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 16px 0; }}
  .summary span {{ font-size: 14px; }}
  .risk {{ background: #fff4f4; border: 1px solid #f2c9c9; border-radius: 8px;
    padding: 12px 14px; font-size: 14px; margin: 14px 0 24px; }}
  h2 {{ font-size: 16px; margin: 28px 0 10px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
  .finding {{ border: 1px solid #e6e6e6; border-left: 4px solid #ccc; border-radius: 8px;
    padding: 12px 14px; margin: 10px 0; page-break-inside: avoid; }}
  .finding.critical {{ border-left-color: #d33; }}
  .finding.warning {{ border-left-color: #e69500; }}
  .finding.info {{ border-left-color: #3b82f6; }}
  .finding.ok {{ border-left-color: #22a06b; }}
  .head {{ display: flex; align-items: baseline; gap: 8px; }}
  .pill {{ font-size: 11px; text-transform: uppercase; padding: 2px 7px; border-radius: 10px;
    background: #eee; color: #444; white-space: nowrap; }}
  .pill.critical {{ background: #fde2e2; color: #a01313; }}
  .pill.warning {{ background: #fdefd2; color: #8a5a00; }}
  .pill.info {{ background: #e2ecfd; color: #1a4aa0; }}
  .pill.ok {{ background: #dcf5ea; color: #10704a; }}
  .title {{ font-weight: 600; }}
  .law {{ color: #777; font-size: 12px; margin: 4px 0; }}
  .fine {{ font-size: 13px; margin: 4px 0; }}
  .koap {{ color: #999; }}
  .where {{ font-size: 12px; color: #666; margin: 4px 0; word-break: break-all; }}
  .quote {{ background: #f7f7f7; border-radius: 6px; padding: 8px 10px; font-size: 13px;
    margin: 8px 0; white-space: pre-wrap; }}
  .rec {{ background: #f0f7f2; border-radius: 6px; padding: 8px 10px; font-size: 14px; margin: 8px 0 0; }}
  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #eee;
    color: #999; font-size: 12px; }}
</style></head><body>
<h1>Отчёт о соответствии · {url}</h1>
<div class="stamp">скан {scan_short} · {created} · подготовлено сервисом LawCheck</div>
<div class="summary">
  <span>Соответствие требованиям: <strong>{compliance}%</strong></span>
  <span>Критично: <strong>{crit}</strong></span>
  <span>Внимание: <strong>{warn}</strong></span>
  <span>Инфо: <strong>{info}</strong></span>
  <span>В норме: <strong>{ok}</strong></span>
</div>
{risk_line}
{blocks}
<footer>Отчёт подготовлен автоматически сервисом LawCheck и не является юридической
консультацией. Оценка риска штрафа ориентировочна. По вопросам — juristlawer@gmail.com</footer>
</body></html>"""


def _html_to_pdf(page: str, out: str) -> None:
    """HTML → PDF через headless Chromium (Playwright; Chromium есть в образе)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(page, wait_until="load")
            pg.pdf(path=out, format="A4", print_background=True,
                   margin={"top": "12mm", "bottom": "14mm", "left": "10mm", "right": "10mm"})
        finally:
            browser.close()


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    scan_id = argv[0]
    out = argv[1] if len(argv) > 1 else f"report-{scan_id[:8]}.pdf"
    page = render(scan_id)
    if out.lower().endswith(".pdf"):
        _html_to_pdf(page, out)
    else:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(page)
    print(f"Отчёт сохранён: {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
