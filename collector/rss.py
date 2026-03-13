"""Google News RSS collector.

Fetches news articles from Google News RSS feeds by keyword,
filters by relevance, deduplicates, and resolves real article URLs.

URL resolution uses googlenewsdecoder for the opaque article IDs
used by Google News since 2024.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


@dataclass
class NewsArticle:
    keyword: str
    title: str
    source: str
    link: str
    date: str  # "YYYY-MM-DD HH:MM" in KST


def _split_title_and_source(raw_title: str) -> tuple[str, str]:
    """Split 'Title - Source' into (title, source).

    Uses the last ' - ' separator to handle titles containing hyphens.
    """
    sep = " - "
    idx = raw_title.rfind(sep)
    if idx != -1:
        title = raw_title[:idx].strip()
        source = raw_title[idx + len(sep):].strip()
    else:
        title = raw_title.strip()
        source = "출처 없음"
    return title, source


def _format_date_kst(date_str: str) -> str:
    """Parse RSS pubDate and format as 'YYYY-MM-DD HH:MM' in KST."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        kst_dt = dt.astimezone(KST)
        return kst_dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def resolve_google_urls(articles: list[NewsArticle]) -> None:
    """Resolve Google News redirect URLs to actual article URLs in-place.

    Uses googlenewsdecoder for the opaque article IDs in newer Google News URLs.
    Only processes articles whose links contain 'news.google.com'.
    Retries once on failure (total 2 attempts per URL).
    """
    from googlenewsdecoder import gnewsdecoder

    for article in articles:
        if "news.google.com" not in article.link:
            continue

        resolved = False
        for attempt in range(2):
            try:
                result = gnewsdecoder(article.link, interval=0.5)
                if result.get("status"):
                    article.link = result["decoded_url"]
                    logger.info("URL 해석 성공: %s -> %s", article.title[:30], article.link[:80])
                    resolved = True
                    break
                else:
                    logger.warning(
                        "URL 해석 실패 (시도 %d/2): %s (%s)",
                        attempt + 1,
                        article.title[:30],
                        result.get("message", "unknown"),
                    )
            except Exception:
                logger.warning("URL 해석 오류 (시도 %d/2): %s", attempt + 1, article.title[:30])

            if attempt == 0:
                time.sleep(0.5)

        if not resolved:
            logger.warning("URL 해석 최종 실패 (2회 시도): %s", article.title[:30])


def collect_news(config: dict) -> list[NewsArticle]:
    """Collect news articles from Google News RSS.

    NOTE: Returned articles may still have Google News redirect URLs.
    Call resolve_google_urls() on selected articles before scraping.

    Args:
        config: The full config dict (uses 'rss' section).

    Returns:
        Deduplicated list of NewsArticle sorted by keyword asc, date desc.
    """
    rss_config = config.get("rss", {})
    keywords = rss_config.get("keywords", [])
    filter_words = rss_config.get("filter_words", [])
    max_age_days = rss_config.get("max_age_days", 7)

    # Build date filter
    cutoff = datetime.now(KST) - timedelta(days=max_age_days)
    after_date = cutoff.strftime("%Y-%m-%d")

    base_url = "https://news.google.com/rss/search?q="
    articles: list[NewsArticle] = []
    seen_titles: set[str] = set()

    for keyword in keywords:
        query = quote(f"{keyword} after:{after_date}")
        url = f"{base_url}{query}&hl=ko&gl=KR&ceid=KR:ko"

        try:
            feed = feedparser.parse(url)

            for entry in feed.entries:
                raw_title = entry.get("title", "")
                google_link = entry.get("link", "")
                pub_date = entry.get("published", "")

                title, source = _split_title_and_source(raw_title)
                formatted_date = _format_date_kst(pub_date)

                # Filter: must contain at least one filter word
                if not any(word in title for word in filter_words):
                    continue

                # Deduplicate by title
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                articles.append(NewsArticle(
                    keyword=keyword,
                    title=title,
                    source=source,
                    link=google_link,
                    date=formatted_date,
                ))

        except Exception as e:
            logger.warning("키워드 '%s' 처리 중 오류: %s", keyword, e)

    # Sort: keyword asc, date desc
    articles.sort(key=lambda a: (a.keyword, a.date), reverse=False)
    articles.sort(key=lambda a: a.date, reverse=True)
    articles.sort(key=lambda a: a.keyword)

    logger.info("RSS 수집 완료: %d개 기사 (키워드 %d개)", len(articles), len(keywords))
    return articles


if __name__ == "__main__":
    import yaml

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    results = collect_news(config)
    for a in results[:20]:
        print(f"[{a.keyword}] {a.title} ({a.source}) - {a.date}")
        print(f"  {a.link}")
