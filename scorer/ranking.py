"""Article scoring and selection.

Ports the Google Apps Script scoring logic to Python.
Scores articles by keyword weight, headline impact, source reliability,
and freshness, then selects 7 articles with keyword diversity.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from collector.rss import NewsArticle

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# Headline keyword patterns with scores (2026 AI trend updated)
TITLE_WEIGHTS: list[tuple[re.Pattern, int]] = [
    # 실적/성과 (고임팩트)
    (re.compile(r"월\s*매출|역대\s?최대|사상\s?최대|최대\s?매출|흑자\s*전환|적자\s*전환|영업이익", re.IGNORECASE), 6),
    # 투자/M&A
    (re.compile(r"IPO|상장|투자\s*유치|시리즈\s*[A-D]|M&A|인수|합병", re.IGNORECASE), 5),
    # AI 핵심 (2026 트렌드)
    (re.compile(r"AI\s?에이전트|에이전틱|멀티\s?에이전트|생성형\s?AI|생성AI", re.IGNORECASE), 5),
    (re.compile(r"AI\s?검색|AI\s?오버뷰|AI\s?쇼핑|AI\s?추천|초개인화", re.IGNORECASE), 4),
    (re.compile(r"클로드|GPT|제미나이|코파일럿|오픈AI|LLM", re.IGNORECASE), 3),
    (re.compile(r"온디바이스|엣지\s?AI|sLLM", re.IGNORECASE), 3),
    # 이커머스 트렌드
    (re.compile(r"퀵커머스|즉시배송|새벽배송|풀필먼트", re.IGNORECASE), 4),
    (re.compile(r"틱톡\s?샵|숏폼\s?커머스|라이브\s?커머스", re.IGNORECASE), 4),
    (re.compile(r"크로스보더|역직구|해외직구", re.IGNORECASE), 3),
    (re.compile(r"구독\s?경제|멤버십|락인", re.IGNORECASE), 3),
    # 사업 변동
    (re.compile(r"출시|공개|발표|론칭|도입|확장", re.IGNORECASE), 3),
    (re.compile(r"채용\s?중단|구조조정|희망퇴직|철수|사업\s*종료", re.IGNORECASE), 4),
    # 규제/정책
    (re.compile(r"규제|공정위|GDPR|보안|개인정보|AI\s?규제|AI\s?저작권", re.IGNORECASE), 2),
]


@dataclass
class ScoredArticle:
    keyword: str
    title: str
    source: str
    link: str
    date: str
    category: str  # "오픈마켓/소셜커머스" or "기타 커머스/IT 동향"
    score: int


def _classify_category(keyword: str, market_keywords: list[str]) -> str:
    """Classify article category based on keyword."""
    for mk in market_keywords:
        if mk in keyword:
            return "오픈마켓/소셜커머스"
    return "기타 커머스/IT 동향"


def _parse_date(date_str: str) -> datetime | None:
    """Parse 'YYYY-MM-DD HH:MM' to datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    except ValueError:
        return None


def score_article(article: NewsArticle, config: dict) -> ScoredArticle:
    """Score a single article based on config weights."""
    scoring = config.get("scoring", {})
    keyword_weights = scoring.get("keyword_weights", {})
    keyword_soft_penalty = scoring.get("keyword_soft_penalty", {})
    source_weights = scoring.get("source_weights", {})
    market_keywords = scoring.get("market_keywords", [])

    score = 0

    # Keyword weight
    for kw, weight in keyword_weights.items():
        if kw in article.keyword:
            score += weight

    # Keyword soft penalty
    for kw, penalty in keyword_soft_penalty.items():
        if kw in article.keyword:
            score -= penalty

    # Title impact weight
    for pattern, weight in TITLE_WEIGHTS:
        if pattern.search(article.title):
            score += weight

    # Source weight
    for src, weight in source_weights.items():
        if src.lower() in article.source.lower():
            score += weight

    # Freshness bonus
    dt = _parse_date(article.date)
    if dt:
        days = (datetime.now(KST) - dt).total_seconds() / (60 * 60 * 24)
        if days <= 1:
            score += 3
        elif days <= 3:
            score += 2
        elif days <= 7:
            score += 1

    category = _classify_category(article.keyword, market_keywords)

    return ScoredArticle(
        keyword=article.keyword,
        title=article.title,
        source=article.source,
        link=article.link,
        date=article.date,
        category=category,
        score=score,
    )


def _title_words(title: str) -> set[str]:
    """Extract content words from title for similarity check."""
    # Remove punctuation and split
    cleaned = re.sub(r"[^\w\s]", " ", title)
    return {w for w in cleaned.split() if len(w) >= 2}


def _is_similar_title(title_a: str, title_b: str, threshold: float = 0.4) -> bool:
    """Check if two titles cover the same news story."""
    words_a = _title_words(title_a)
    words_b = _title_words(title_b)
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return overlap / smaller >= threshold


def _dedupe_by_title(articles: list[ScoredArticle]) -> list[ScoredArticle]:
    """Keep first (highest-scoring) article per similar title group.

    IMPORTANT: Input must be pre-sorted by score descending.
    We never replace — the first match is always the best.
    Replacing caused a transitivity bug: A replaces B, then C (similar to B
    but not A) passes through as non-duplicate.
    """
    kept: list[ScoredArticle] = []
    for a in articles:
        is_dup = False
        for existing in kept:
            if a.title == existing.title or _is_similar_title(a.title, existing.title):
                is_dup = True
                break
        if not is_dup:
            kept.append(a)
    return kept


def select_weekly_picks(
    articles: list[NewsArticle],
    config: dict,
) -> dict[str, list[ScoredArticle]]:
    """Score, rank, and select 7 articles (1 main + 3 market + 3 other).

    Returns:
        Dict with keys 'main', 'market', 'other', each containing lists of ScoredArticle.
    """
    scoring_config = config.get("scoring", {})
    max_age_days = scoring_config.get("max_age_days", 14)
    max_per_keyword = scoring_config.get("max_per_keyword", 1)
    fill_dup = scoring_config.get("fill_with_dup_if_not_enough", True)
    pick_counts = scoring_config.get("pick_counts", {})
    main_count = pick_counts.get("main", 1)
    market_count = pick_counts.get("market", 3)
    other_count = pick_counts.get("other", 3)

    now = datetime.now(KST)

    # Score all articles
    scored = [score_article(a, config) for a in articles]

    # Freshness filter
    fresh = []
    for a in scored:
        dt = _parse_date(a.date)
        if dt is None or (now - dt).total_seconds() / (60 * 60 * 24) <= max_age_days:
            fresh.append(a)

    # Dedupe and sort by score desc, date desc
    unique = _dedupe_by_title(fresh)
    unique.sort(key=lambda a: (a.score, a.date), reverse=True)

    # Selection with keyword diversity
    used_titles: set[str] = set()
    used_keywords: dict[str, int] = {}

    def can_use_keyword(kw: str) -> bool:
        return used_keywords.get(kw, 0) < max_per_keyword

    def mark_use(article: ScoredArticle) -> None:
        used_titles.add(article.title)
        used_keywords[article.keyword] = used_keywords.get(article.keyword, 0) + 1

    def pick_top_distinct(
        pool: list[ScoredArticle], need: int
    ) -> list[ScoredArticle]:
        picked: list[ScoredArticle] = []
        # Pass 1: distinct keywords
        for item in pool:
            if len(picked) >= need:
                break
            if item.title not in used_titles and can_use_keyword(item.keyword):
                picked.append(item)
                mark_use(item)
        # Pass 2: fill with duplicates if allowed
        if fill_dup and len(picked) < need:
            for item in pool:
                if len(picked) >= need:
                    break
                if item.title not in used_titles:
                    picked.append(item)
                    mark_use(item)
        return picked

    # Main top pick
    pick_main: list[ScoredArticle] = []
    for item in unique:
        if item.title and item.link and can_use_keyword(item.keyword):
            pick_main.append(item)
            mark_use(item)
            break
    if not pick_main and unique:
        pick_main.append(unique[0])
        mark_use(unique[0])

    # Category pools (exclude already picked)
    market_pool = [a for a in unique if a.category == "오픈마켓/소셜커머스" and a.title not in used_titles]
    other_pool = [a for a in unique if a.category == "기타 커머스/IT 동향" and a.title not in used_titles]

    market = pick_top_distinct(market_pool, market_count)
    other = pick_top_distinct(other_pool, other_count)

    logger.info(
        "선별 완료: 메인 %d, 오픈마켓 %d, 기타 %d",
        len(pick_main), len(market), len(other),
    )

    return {
        "main": pick_main,
        "market": market,
        "other": other,
    }
