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
    Optimistic strategy: fast by default, backoff only on 429.
    """
    from googlenewsdecoder import gnewsdecoder

    targets = [a for a in articles if "news.google.com" in a.link]
    if not targets:
        return

    logger.info("Google URL 해석 시작: %d건", len(targets))
    consecutive_fails = 0

    for article in targets:
        # 연속 실패 시 점진적 대기 (429 누적 방지)
        if consecutive_fails >= 3:
            cooldown = min(consecutive_fails * 2.0, 15.0)
            logger.info("연속 실패 %d회, %.1fs 쿨다운...", consecutive_fails, cooldown)
            time.sleep(cooldown)

        resolved = False
        for attempt in range(3):
            try:
                result = gnewsdecoder(article.link, interval=0.3)
                if result.get("status"):
                    article.link = result["decoded_url"]
                    logger.info("URL 해석 성공: %s -> %s", article.title[:30], article.link[:80])
                    resolved = True
                    consecutive_fails = 0
                    break
                else:
                    msg = result.get("message", "unknown")
                    logger.warning("URL 해석 실패 (시도 %d/3): %s (%s)", attempt + 1, article.title[:30], msg)
                    if "429" in str(msg):
                        backoff = 3.0 * (2 ** attempt)
                        logger.warning("Rate limit 감지, %.1fs 대기...", backoff)
                        time.sleep(backoff)
                    else:
                        time.sleep(0.5)
            except Exception as e:
                logger.warning("URL 해석 오류 (시도 %d/3): %s - %s", attempt + 1, article.title[:30], e)
                if "429" in str(e):
                    backoff = 3.0 * (2 ** attempt)
                    logger.warning("Rate limit 감지, %.1fs 대기...", backoff)
                    time.sleep(backoff)
                else:
                    time.sleep(0.5)

        if not resolved:
            consecutive_fails += 1
            logger.warning("URL 해석 최종 실패 (3회 시도): %s", article.title[:30])


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
