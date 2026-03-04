"""Article scraper module.

Scrapes article text and images from URLs using site-specific CSS selectors
(ported from reframe_server.py) with og:description fallback.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Site-specific body selectors (from reframe_server.py, order matters)
BODY_SELECTORS = [
    "#dic_area",                    # 네이버뉴스
    "#articleBodyContents",         # 다음뉴스
    "#article_body",
    ".article_body",
    ".story-news article",          # 연합뉴스
    "#articeBody",
    "#articleBody",
    ".art_body",
    ".art_txt",
    ".news-text",
    ".article-body",
    ".article-view",
    ".article_view",                # 디지털데일리
    ".article_content",             # 디지털데일리 inner
    "#newsEndContents",
    ".article_txt",
    ".view_con",
    ".article-content",
    ".news_view",
    ".news_cnt_detail_wrap",        # 전자신문
    '[itemprop="articleBody"]',
    "article",
    "#content .article",
    ".post-content",
    "#newsContent",
]

# Noise patterns to filter out of extracted text
NOISE_PATTERNS = [
    "기자 이메일", "무단전재", "저작권", "Copyright", "copyrightⓒ",
    "재배포금지", "▶", "☞", "※ ", "기사원문", "Copyrightⓒ",
    "기자입니다", "무단 전재", "재배포 금지", "All rights reserved",
    "공감은 , , , ,", "SNS , , , ,",
    "본문 보기를 권장합니다",
    "이 글자크기로 변경됩니다",
    "가장 빠른 뉴스가 있고 다양한 정보",
    "다음뉴스를 만나보세요",
    "포토", ", , , ,",
    # 페이월 / 유료 안내
    "유료 회원에게 제공하는",
    "프리미엄 서비스입니다",
    "회원 가입 후",
    "전자판 서비스를 무료로",
    "구독하시면 읽을 수 있습니다",
    "전문은 구독 후",
    # 광고 스크립트 잔여물
    "window.Criteo",
    "Criteo.events",
    "Criteo.Passback",
    "adUnits",
    "slotId",
    "MEDIA_795",
]


@dataclass
class ScrapedArticle:
    title: str
    url: str
    text: str
    image_url: str = ""
    image_paths: list[str] = field(default_factory=list)


def _extract_og(soup: BeautifulSoup, prop: str) -> str:
    """Extract Open Graph meta tag content."""
    tag = soup.find("meta", property=prop)
    if tag and tag.get("content"):
        return tag["content"].strip()
    tag = soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def _clean_body_text(element) -> str:
    """Extract and clean text from an HTML element, preserving paragraph structure.

    Converts HTML structure to plain text:
    - <br> tags -> \\n (line break within paragraph)
    - <p>, <div> boundaries -> \\n\\n (paragraph break)
    Then filters noise patterns.
    """
    # Convert HTML structure to text with preserved breaks
    html = str(element)

    # Replace <br> variants with single newline
    html = re.sub(r'<br\s*/?>', '\n', html)

    # Replace block-level closing tags with double newline
    html = re.sub(r'</(?:p|div|h[1-6]|li|blockquote|section|article)>', '\n\n', html)

    # Strip all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', html)

    # Decode HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')

    # Normalize whitespace within lines (but preserve newlines)
    lines = text.split('\n')
    lines = [' '.join(line.split()) for line in lines]

    # Rejoin and normalize paragraph breaks
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)  # max 2 consecutive newlines

    # Filter noise lines
    result_lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            result_lines.append('')  # preserve paragraph breaks
            continue
        if len(line) < 10:
            continue
        if any(noise in line for noise in NOISE_PATTERNS):
            continue
        result_lines.append(line)

    # Clean up: collapse multiple blank lines, strip edges
    result = '\n'.join(result_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _extract_body(soup: BeautifulSoup) -> str:
    """Extract article body using site-specific CSS selectors."""
    # Remove unwanted elements first
    for tag in soup.find_all(
        ["script", "style", "nav", "header", "footer", "aside",
         "iframe", "noscript", "figure", "figcaption", "button", "form"]
    ):
        tag.decompose()

    # Try selectors in order
    for selector in BODY_SELECTORS:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue
        if element:
            text = _clean_body_text(element)
            if len(text) > 100:
                return text

    # Fallback: og:description
    return _extract_og(soup, "og:description")


def _content_type_to_ext(content_type: str, url: str) -> str:
    """Determine file extension from Content-Type header or URL path."""
    mime_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in mime_map:
            return mime_map[mime]

    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return ".jpg" if ext == ".jpeg" else ext

    return ".jpg"


def _download_og_image(
    image_url: str,
    output_dir: str | None = None,
    min_size_bytes: int = 10000,
) -> str | None:
    """Download OG image and return file path, or None.

    Args:
        image_url: URL of the image to download.
        output_dir: If given, save to this directory (persistent).
                    If None, save to system temp directory.
        min_size_bytes: Minimum file size to keep.
    """
    if not image_url:
        return None
    try:
        resp = requests.get(image_url, headers=HEADERS, stream=True, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        ext = _content_type_to_ext(content_type, image_url)

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                suffix=ext, prefix="article_img_", dir=output_dir, delete=False
            )
        else:
            tmp = tempfile.NamedTemporaryFile(
                suffix=ext, prefix="article_img_", delete=False
            )
        try:
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp.close()

            actual_size = os.path.getsize(tmp.name)
            if actual_size < min_size_bytes:
                os.unlink(tmp.name)
                return None

            logger.info("Downloaded OG image: %s -> %s", image_url[:80], tmp.name)
            return tmp.name
        except Exception:
            tmp.close()
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
            raise
    except Exception:
        logger.warning("Failed to download OG image: %s", image_url[:80])
        return None


def _detect_encoding(resp: requests.Response) -> str:
    """Detect correct encoding for a response.

    Priority: Content-Type header charset > HTML meta charset > UTF-8.
    Avoids chardet (apparent_encoding) which often misdetects Korean UTF-8
    as Windows-1251 or similar.
    """
    # 1. Content-Type header charset (requests parses this into resp.encoding)
    ct = resp.headers.get("Content-Type", "")
    if "charset=" in ct.lower():
        return resp.encoding  # already parsed by requests

    # 2. Peek at raw bytes for HTML meta charset
    raw = resp.content[:4096]
    # <meta charset="utf-8"> or <meta charset='euc-kr'>
    m = re.search(rb'<meta[^>]+charset=["\']?([^"\'\s;>]+)', raw, re.IGNORECASE)
    if m:
        return m.group(1).decode("ascii", errors="ignore")

    # <meta http-equiv="Content-Type" content="text/html; charset=euc-kr">
    m = re.search(rb'content=["\'][^"\']*charset=([^"\'\s;>]+)', raw, re.IGNORECASE)
    if m:
        return m.group(1).decode("ascii", errors="ignore")

    # 3. Default to UTF-8 (most Korean news sites)
    return "utf-8"


def scrape_article(
    url: str, title: str, download_images: bool = True,
    image_output_dir: str | None = None,
) -> ScrapedArticle:
    """Scrape article text and OG image from a URL.

    Uses site-specific CSS selectors (BODY_SELECTORS) with noise filtering.
    Falls back to og:description when body selectors fail.
    """
    logger.info("Scraping article: %s", url)

    if "news.google.com/rss/articles/" in url:
        logger.warning(
            "Google News RSS URL detected: %s — 실제 기사 URL을 사용하세요.", url,
        )

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.encoding = _detect_encoding(resp)
        html = resp.text
    except Exception:
        logger.exception("페이지 다운로드 실패: %s", url)
        return ScrapedArticle(title=title, url=url, text="(본문을 추출할 수 없습니다)")

    soup = BeautifulSoup(html, "html.parser")

    # Portal sites (nate, daum) only show summaries — follow "원문" link
    if "news.nate.com" in url:
        origin_link = soup.find("a", string="원문")
        if origin_link and origin_link.get("href"):
            origin_url = origin_link["href"]
            logger.info("포털 원문 링크 추적: %s -> %s", url[:40], origin_url[:80])
            try:
                resp = requests.get(origin_url, headers=HEADERS, timeout=15, allow_redirects=True)
                resp.encoding = _detect_encoding(resp)
                soup = BeautifulSoup(resp.text, "html.parser")
                url = origin_url
            except Exception:
                logger.warning("원문 링크 접근 실패, 포털 페이지로 대체: %s", origin_url[:80])

    # Extract body with selectors
    text = _extract_body(soup)

    # Fallback if body is too short
    if len(text) < 50:
        desc = _extract_og(soup, "og:description")
        if desc and len(desc) > len(text):
            text = desc

    if not text:
        text = "(본문을 추출할 수 없습니다)"
        logger.warning("Could not extract text from: %s", url)

    # Extract OG image (resolve relative URLs)
    image_url = _extract_og(soup, "og:image") or ""
    if image_url and not image_url.startswith("http"):
        image_url = urljoin(url, image_url)
    image_paths: list[str] = []

    if download_images and image_url:
        path = _download_og_image(image_url, output_dir=image_output_dir)
        if path:
            image_paths.append(path)

    return ScrapedArticle(
        title=title,
        url=url,
        text=text,
        image_url=image_url,
        image_paths=image_paths,
    )


def cleanup_temp_images(articles: list[ScrapedArticle]) -> None:
    """Remove all temporary image files from scraped articles."""
    for article in articles:
        for path in article.image_paths:
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    logger.debug("Removed temp image: %s", path)
            except OSError:
                logger.warning("Failed to remove temp image: %s", path, exc_info=True)
        article.image_paths.clear()
