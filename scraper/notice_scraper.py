"""
E-commerce platform notice scraper.

Collects notices from imweb, cafe24, and makeshop,
then filters to the current week's posts.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Notice:
    title: str
    url: str
    date: Optional[date]
    source: str


@dataclass
class NoticeTarget:
    name: str
    url: str
    encoding: str


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

TARGETS: list[NoticeTarget] = [
    NoticeTarget(
        name="아임웹",
        url="https://imweb.me/notice?category=notice",
        encoding="utf-8",
    ),
    NoticeTarget(
        name="카페24-쇼핑몰",
        url="https://shopnotice.cafe24.com/?bbs_no=0",
        encoding="utf-8",
    ),
    NoticeTarget(
        name="카페24-기능",
        url="https://shopnotice.cafe24.com/?bbs_no=5",
        encoding="utf-8",
    ),
    NoticeTarget(
        name="메이크샵",
        url="https://www.makeshop.co.kr/newmakeshop/home/notice_list.html",
        encoding="cp949",
    ),
]

# ---------------------------------------------------------------------------
# Week range helpers
# ---------------------------------------------------------------------------

def get_week_range(reference_date: Optional[date] = None) -> tuple[date, date]:
    """Return (monday, sunday) of the week containing *reference_date*."""
    ref = reference_date or date.today()
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def is_this_week(notice_date: Optional[date], week_range: tuple[date, date]) -> bool:
    """Return True if *notice_date* falls within *week_range*.

    Notices without a parseable date are included (conservative approach).
    """
    if notice_date is None:
        return True
    monday, sunday = week_range
    return monday <= notice_date <= sunday


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
})


def _fetch(url: str, encoding: str, timeout: int = 15) -> BeautifulSoup:
    """Fetch a URL and return a BeautifulSoup tree."""
    resp = _SESSION.get(url, timeout=timeout)
    resp.encoding = encoding
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


# ---------------------------------------------------------------------------
# Date parsers
# ---------------------------------------------------------------------------

_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})"), "dot"),    # 2026.02.19 or 2026. 02. 19
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), "dash"),            # 2026-02-19
]


def _parse_date(text: str) -> Optional[date]:
    """Try to extract a date from *text*. Return None on failure."""
    for pattern, _tag in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Per-platform scrapers
# ---------------------------------------------------------------------------

def scrape_imweb(target: NoticeTarget) -> list[Notice]:
    """Scrape notices from imweb.me."""
    logger.info("Scraping %s: %s", target.name, target.url)
    soup = _fetch(target.url, target.encoding)
    notices: list[Notice] = []

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        if "/notice?view" not in href:
            continue

        title = a_tag.get_text(strip=True)
        if not title:
            continue

        # Build full URL
        full_url = urljoin("https://imweb.me/", href)

        # Try to find a date near this link
        notice_date: Optional[date] = None
        # Walk siblings / parent for date text
        parent = a_tag.find_parent()
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            notice_date = _parse_date(parent_text)
        if notice_date is None:
            # Try grandparent or containing row/item
            grandparent = parent.find_parent() if parent else None
            if grandparent:
                notice_date = _parse_date(grandparent.get_text(" ", strip=True))

        notices.append(Notice(
            title=title,
            url=full_url,
            date=notice_date,
            source=target.name,
        ))

    logger.info("  Found %d notices from %s", len(notices), target.name)
    return notices


def scrape_cafe24(target: NoticeTarget) -> list[Notice]:
    """Scrape notices from Cafe24 shop notice.

    Cafe24 renders with Material-UI. Each notice is an <a> with href
    containing '/view?'. Inside the <a>, child <p> elements hold the title,
    view count, and date separately.
    """
    logger.info("Scraping %s: %s", target.name, target.url)
    soup = _fetch(target.url, target.encoding)
    notices: list[Notice] = []

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        if "/view?" not in href:
            continue

        full_url = urljoin(target.url, href)

        # Extract title and date from child <p> elements
        paragraphs = a_tag.find_all("p")
        title = ""
        notice_date: date | None = None

        for p in paragraphs:
            text = p.get_text(strip=True)
            if not text:
                continue
            # Check if this <p> is a date (YYYY-MM-DD)
            parsed = _parse_date(text)
            if parsed and len(text) == 10:
                notice_date = parsed
            # Check if this is a view count (short pure digits)
            elif text.isdigit() and len(text) <= 6:
                continue
            # Otherwise it's the title (longest meaningful text)
            elif len(text) > len(title):
                title = text

        if not title:
            continue

        notices.append(Notice(
            title=title,
            url=full_url,
            date=notice_date,
            source=target.name,
        ))

    logger.info("  Found %d notices from %s", len(notices), target.name)
    return notices


def scrape_makeshop(target: NoticeTarget) -> list[Notice]:
    """Scrape notices from Makeshop."""
    logger.info("Scraping %s: %s", target.name, target.url)
    soup = _fetch(target.url, target.encoding)
    notices: list[Notice] = []

    base_url = "https://www.makeshop.co.kr/newmakeshop/home/"

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        # Column order: No, 분류, 제목(linked), view, 작성일시
        title_cell = cells[2]
        date_cell = cells[4]

        link_tag = title_cell.find("a", href=True)
        if not link_tag:
            continue

        title = link_tag.get_text(strip=True)
        if not title:
            continue

        href: str = link_tag["href"]
        # Normalise relative path: ./notice_view.html?... -> full URL
        if href.startswith("./"):
            href = href[2:]
        full_url = urljoin(base_url, href)

        notice_date = _parse_date(date_cell.get_text(strip=True))

        notices.append(Notice(
            title=title,
            url=full_url,
            date=notice_date,
            source=target.name,
        ))

    logger.info("  Found %d notices from %s", len(notices), target.name)
    return notices


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

SCRAPER_MAP: dict[str, callable] = {
    "아임웹": scrape_imweb,
    "카페24-쇼핑몰": scrape_cafe24,
    "카페24-기능": scrape_cafe24,
    "메이크샵": scrape_makeshop,
}


# ---------------------------------------------------------------------------
# Collection and formatting
# ---------------------------------------------------------------------------

def collect_all_notices(
    targets: list[NoticeTarget] | None = None,
    week_range: tuple[date, date] | None = None,
) -> dict[str, list[Notice]]:
    """Scrape all targets, filter to current week, return by target name."""
    if targets is None:
        targets = TARGETS
    if week_range is None:
        week_range = get_week_range()

    results: dict[str, list[Notice]] = {}

    for target in targets:
        scraper = SCRAPER_MAP.get(target.name)
        if scraper is None:
            logger.warning("No scraper registered for target: %s", target.name)
            results[target.name] = []
            continue

        try:
            all_notices = scraper(target)
            filtered = [n for n in all_notices if is_this_week(n.date, week_range)]
            results[target.name] = filtered
            logger.info(
                "  %s: %d/%d notices in week range",
                target.name,
                len(filtered),
                len(all_notices),
            )
        except Exception:
            logger.exception("Failed to scrape %s", target.name)
            results[target.name] = []

    return results


def format_notices_markdown(
    notices_by_target: dict[str, list[Notice]],
    week_range: tuple[date, date],
) -> str:
    """Generate a markdown summary of the collected notices."""
    monday, sunday = week_range
    lines: list[str] = [
        f"# 주간 공지사항 ({monday.isoformat()} ~ {sunday.isoformat()})",
        "",
    ]

    for target in TARGETS:
        name = target.name
        notices = notices_by_target.get(name, [])
        lines.append(f"## {name}")
        if notices:
            for n in notices:
                lines.append(f"- [{n.title}]({n.url})")
        else:
            lines.append("- (이번 주 새 공지 없음)")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Fix Windows console encoding for Unicode output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    week_range = get_week_range()
    monday, sunday = week_range
    logger.info("Collecting notices for week: %s ~ %s", monday, sunday)

    notices_by_target = collect_all_notices(week_range=week_range)
    markdown = format_notices_markdown(notices_by_target, week_range)

    print()
    print(markdown)
