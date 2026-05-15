import pytest

from lawcheck.crawler.crawler import _is_content_url


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "https://example.com/about",
    "https://example.com/privacy",
    "https://example.com/contacts/moscow",
    "https://example.com/blog/2026/article-slug",
])
def test_content_urls_pass(url):
    assert _is_content_url(url) is True


@pytest.mark.parametrize("url", [
    "https://example.com/auth/login",
    "https://example.com/login",
    "https://example.com/oauth/callback",
    "https://example.com/api/v1/users",
    "https://example.com/v2/items",
    "https://example.com/_next/static/chunk.js",
    "https://example.com/wp-admin/post.php",
    "https://example.com/sitemap.xml",
    "https://example.com/static/main.css",
    "https://example.com/assets/logo.png",
    "https://example.com/files/report.pdf",
    "https://example.com/feed",
    "https://habr.com/kek/v1/auth/habrahabr/",
])
def test_non_content_urls_filtered(url):
    assert _is_content_url(url) is False
