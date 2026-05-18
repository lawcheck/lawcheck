import io

import pytest
from pypdf import PdfWriter

from lawcheck.crawler.crawler import _is_content_url, _is_pdf, _is_priority_pdf
from lawcheck.crawler.pdf_fetcher import _extract_text


# === классификация PDF-URL ===

@pytest.mark.parametrize("url, expected", [
    ("https://x.ru/privacy/policy.pdf", True),
    ("https://x.ru/legal-docs/privacy_policy/version-1.pdf", True),
    ("https://x.ru/политика.pdf", True),
    ("https://x.ru/wp-content/uploads/2024/oferta.pdf", True),
    ("https://x.ru/files/report.pdf", False),  # не политика — не качаем
    ("https://x.ru/contact", False),
    ("https://x.ru/policy", False),  # не PDF, обрабатывается отдельно
])
def test_is_priority_pdf(url, expected):
    assert _is_priority_pdf(url) is expected


def test_is_pdf_basic():
    assert _is_pdf("https://x.ru/a.pdf") is True
    assert _is_pdf("https://x.ru/a.pdf?v=1") is True
    assert _is_pdf("https://x.ru/a.html") is False


def test_priority_pdf_passes_content_filter():
    # обычно _SKIP_EXT_RE срезает .pdf — но если он priority, пускаем
    assert _is_content_url("https://x.ru/privacy/policy.pdf") is True
    assert _is_content_url("https://x.ru/files/report.pdf") is False


# === извлечение текста ===

def _make_simple_pdf(text: str) -> bytes:
    """pypdf >= 5 умеет писать PDF, но текстовый слой собирать сложно.
    Для теста используем сторонний рендерер если есть, иначе пропускаем."""
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab не установлен — тест извлечения скипаем")
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_extract_text_from_corrupted_pdf_returns_empty():
    assert _extract_text(b"not a pdf at all") == ""


def test_extract_text_from_empty_pdf_returns_empty():
    # валидный пустой PDF
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    # нет текстового слоя — extract_text должен вернуть '' или почти ''
    result = _extract_text(buf.getvalue())
    assert isinstance(result, str)
    assert len(result.strip()) == 0
