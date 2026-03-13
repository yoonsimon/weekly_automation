"""
E-commerce platform notice scraper.

Collects notices from imweb, cafe24, and makeshop,
then filters to the current week's posts.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

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
    lookahead_days: int = 0  # 날짜 필터를 N일 앞까지 확장 (예정일 기반 플랫폼용)


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
        name="카페24-업데이트",
        url="https://shopnotice.cafe24.com/?bbs_no=12",
        encoding="utf-8",
    ),
    NoticeTarget(
        name="카페24-개발자센터",
        url="https://developers.cafe24.com/api/changelog/rest/list?",
        encoding="utf-8",
    ),
    NoticeTarget(
        name="메이크샵",
        url="https://www.makeshop.co.kr/newmakeshop/home/notice_list.html",
        encoding="cp949",
        lookahead_days=7,
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


def get_week_label(reference_date: Optional[date] = None) -> str:
    """Return 'n월 m주차' label based on the Monday of the week."""
    monday, _ = get_week_range(reference_date)
    week_of_month = (monday.day - 1) // 7 + 1
    return f"{monday.month}월 {week_of_month}주차"


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
    """Scrape notices from imweb.me.

    Each notice is an <a class="list-group-item"> containing:
      <p class="list-group-item-heading"><span class="heading">TITLE</span></p>
      <p class="list-group-item-text"><span>DATE</span>SOURCE</p>
    """
    logger.info("Scraping %s: %s", target.name, target.url)
    soup = _fetch(target.url, target.encoding)
    notices: list[Notice] = []

    for a_tag in soup.find_all("a", class_="list-group-item", href=True):
        href: str = a_tag["href"]
        if "/notice?view" not in href:
            continue

        # Extract title from heading element
        heading = a_tag.find("span", class_="heading")
        if heading is None:
            heading = a_tag.find("p", class_="list-group-item-heading")
        title = heading.get_text(strip=True) if heading else ""
        if not title:
            continue

        full_url = urljoin("https://imweb.me/", href)

        # Extract date from list-group-item-text paragraph
        notice_date: Optional[date] = None
        date_p = a_tag.find("p", class_="list-group-item-text")
        if date_p:
            notice_date = _parse_date(date_p.get_text(strip=True))

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

    # Extract bbs_no from target URL to inject into view links
    target_parsed = urlparse(target.url)
    target_bbs_no = parse_qs(target_parsed.query).get("bbs_no", [""])[0]

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        if "/view?" not in href:
            continue

        full_url = urljoin(target.url, href)

        # Fill in empty bbs_no with the value from target URL
        if target_bbs_no:
            parsed = urlparse(full_url)
            qs = parse_qs(parsed.query, keep_blank_values=True)
            if not qs.get("bbs_no", [""])[0]:
                qs["bbs_no"] = [target_bbs_no]
                full_url = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

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


_MAKESHOP_TABLE_IDS = {
    "notice": "notice_board_list",
    "event": "event_board_list",
    "note": "note_board_list",
}


def scrape_makeshop(target: NoticeTarget) -> list[Notice]:
    """Scrape notices from Makeshop (all tabs in a single page).

    The page contains three separate <table> elements identified by id:
      notice_board_list, event_board_list, note_board_list.
    A single fetch retrieves all three.
    """
    logger.info("Scraping %s: %s", target.name, target.url)
    notices: list[Notice] = []
    seen_urls: set[str] = set()
    base_url = "https://www.makeshop.co.kr/newmakeshop/home/"

    try:
        soup = _fetch(target.url, target.encoding)
    except Exception:
        logger.exception("Failed to fetch %s", target.name)
        return notices

    for tab_name, table_id in _MAKESHOP_TABLE_IDS.items():
        table = soup.find("table", id=table_id)
        if table is None:
            logger.warning("Table #%s not found for %s", table_id, target.name)
            continue

        for row in table.find_all("tr"):
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

            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            notices.append(Notice(
                title=title,
                url=full_url,
                date=notice_date,
                source=target.name,
            ))

        logger.info("  %s [%s]: %d rows parsed", target.name, tab_name,
                     sum(1 for n in notices if n.url not in seen_urls or True))

    logger.info("  Found %d notices from %s (all tabs)", len(notices), target.name)
    return notices


def scrape_cafe24_devs(target: NoticeTarget) -> list[Notice]:
    """Scrape API changelog from Cafe24 Developers.

    The REST endpoint returns HTML fragments with changelog entries.
    Each entry is a <div class="mBoardContent"> containing:
      <h3 class="title"><a href="...">TITLE</a></h3>
      <span class="date">YYYY-MM-DD 배포</span>
    """
    logger.info("Scraping %s: %s", target.name, target.url)
    resp = _SESSION.get(
        target.url,
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=15,
    )
    resp.encoding = target.encoding
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    notices: list[Notice] = []

    for entry in soup.find_all("div", class_="mBoardContent"):
        title_tag = entry.find("h3", class_="title")
        if title_tag is None:
            continue
        link = title_tag.find("a", href=True)
        if link is None:
            continue

        title = link.get_text(strip=True)
        if not title:
            continue

        href: str = link["href"]
        full_url = urljoin("https://developers.cafe24.com/", href)

        notice_date: Optional[date] = None
        date_span = entry.find("span", class_="date")
        if date_span:
            notice_date = _parse_date(date_span.get_text(strip=True))

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
    "카페24-업데이트": scrape_cafe24,
    "카페24-개발자센터": scrape_cafe24_devs,
    "메이크샵": scrape_makeshop,
}


# ---------------------------------------------------------------------------
# Collection and formatting
# ---------------------------------------------------------------------------

def collect_all_notices(
    targets: list[NoticeTarget] | None = None,
    week_range: tuple[date, date] | None = None,
    on_progress: "Callable[[str, str], None] | None" = None,
) -> tuple[dict[str, list[Notice]], dict[str, str | None]]:
    """Scrape all targets, filter to current week, return by target name."""
    if targets is None:
        targets = TARGETS
    if week_range is None:
        week_range = get_week_range()

    results: dict[str, list[Notice]] = {}
    errors: dict[str, str | None] = {}

    for target in targets:
        scraper = SCRAPER_MAP.get(target.name)
        if scraper is None:
            logger.warning("No scraper registered for target: %s", target.name)
            results[target.name] = []
            continue

        if on_progress:
            on_progress(target.name, "collecting")
        try:
            all_notices = scraper(target)
            filter_range = week_range
            if target.lookahead_days > 0:
                filter_range = (week_range[0], week_range[1] + timedelta(days=target.lookahead_days))
            filtered = [n for n in all_notices if is_this_week(n.date, filter_range)]
            results[target.name] = filtered
            errors[target.name] = None
            logger.info(
                "  %s: %d/%d notices in week range",
                target.name,
                len(filtered),
                len(all_notices),
            )
            if on_progress:
                on_progress(target.name, "done")
        except Exception as exc:
            logger.exception("Failed to scrape %s", target.name)
            results[target.name] = []
            errors[target.name] = str(exc)
            if on_progress:
                on_progress(target.name, "error")

    return results, errors


# ---------------------------------------------------------------------------
# Source name → (경쟁사명, 구분명) mapping
# ---------------------------------------------------------------------------

_SOURCE_TO_COLUMNS: dict[str, tuple[str, str]] = {
    "아임웹": ("아임웹", "공지사항"),
    "카페24-쇼핑몰": ("카페24", "공지사항"),
    "카페24-기능": ("카페24", "기능"),
    "카페24-업데이트": ("카페24", "업데이트"),
    "카페24-개발자센터": ("카페24", "개발자센터"),
    "메이크샵": ("메이크샵", "공지사항"),
}


def format_notices_markdown(
    notices_by_target: dict[str, list[Notice]],
    week_range: tuple[date, date],
) -> str:
    """Generate a markdown table of the collected notices.

    Table columns: (날짜) | 경쟁사명 | 구분명 | 내용 | 주간리포트
    One row per notice, sorted by date ascending.
    """
    # Collect all notices into a flat list
    all_notices: list[Notice] = []
    for target in TARGETS:
        all_notices.extend(notices_by_target.get(target.name, []))

    # Sort by date (None dates go last)
    all_notices.sort(key=lambda n: n.date or date.max)

    # Build table
    lines: list[str] = [
        "| | 경쟁사명 | 구분명 | 내용 | 주간리포트 |",
        "|---|---|---|---|---|",
    ]

    for n in all_notices:
        date_str = f"{n.date.month}/{n.date.day}" if n.date else ""
        competitor, category = _SOURCE_TO_COLUMNS.get(n.source, (n.source, "공지사항"))
        content = f"[{n.title}]({n.url})"
        lines.append(f"| {date_str} | {competitor} | {category} | {content} | |")

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

    notices_by_target, _ = collect_all_notices(week_range=week_range)
    markdown = format_notices_markdown(notices_by_target, week_range)

    print()
    print(markdown)
