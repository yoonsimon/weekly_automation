"""Article generation service.

Wraps existing collector/scorer/scraper modules into a session-based
workflow for the web dashboard.
"""

import asyncio
import copy
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from collector.rss import NewsArticle, collect_news, resolve_google_urls
from processor.markdown import build_reframe_table
from scorer.ranking import ScoredArticle, score_article, select_weekly_picks, _is_similar_title
from scraper.article_scraper import ScrapedArticle, scrape_article
from scraper.notice_scraper import get_week_range
from web.models import ArticleCard

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MAX_REPLACEMENT_PER_SLOT = 3


# ------------------------------------------------------------------
# GenerationSession
# ------------------------------------------------------------------

@dataclass
class GenerationSession:
    session_id: str
    status: str = "collecting"  # collecting | scoring | scraping | ready | error
    config: dict = field(default_factory=dict)

    # Pipeline outputs
    picks: dict[str, list[ScoredArticle]] = field(default_factory=dict)
    scraped_texts: dict[str, str] = field(default_factory=dict)
    image_refs: dict[str, str] = field(default_factory=dict)
    scraped_articles: dict[str, ScrapedArticle] = field(default_factory=dict)

    # Replacement tracking
    excluded_keywords: dict[int, list[str]] = field(default_factory=dict)
    replacement_counts: dict[int, int] = field(default_factory=dict)
    pending_replacements: dict[int, dict] = field(default_factory=dict)

    # Progress events (asyncio.Queue is created per session)
    progress_queue: asyncio.Queue | None = None

    error_message: str = ""
    created_at: float = field(default_factory=time.time)

    def _flat_picks(self) -> list[ScoredArticle]:
        """Return all picks in a flat list preserving slot order."""
        result: list[ScoredArticle] = []
        for key in ("main", "market", "other"):
            result.extend(self.picks.get(key, []))
        return result

    def _slot_for_index(self, index: int) -> str:
        """Return slot key for global index."""
        offset = 0
        for key in ("main", "market", "other"):
            articles = self.picks.get(key, [])
            if index < offset + len(articles):
                return key
            offset += len(articles)
        return "other"


# ------------------------------------------------------------------
# SessionManager
# ------------------------------------------------------------------

class SessionManager:
    """Manages generation sessions. At most 1 active session at a time."""

    def __init__(self):
        self._sessions: dict[str, GenerationSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> GenerationSession | None:
        return self._sessions.get(session_id)

    def create(self, config: dict) -> GenerationSession:
        with self._lock:
            # Cleanup old finished sessions (keep last 5)
            self._cleanup()

            # Check for active session
            for s in self._sessions.values():
                if s.status in ("collecting", "scoring", "scraping"):
                    raise RuntimeError("이미 진행 중인 세션이 있습니다")

            session_id = datetime.now(KST).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
            session = GenerationSession(
                session_id=session_id,
                config=copy.deepcopy(config),
                progress_queue=asyncio.Queue(),
            )
            self._sessions[session_id] = session
            return session

    def _cleanup(self) -> None:
        """Remove old completed/error sessions beyond the most recent 5."""
        finished = [
            (s.created_at, sid)
            for sid, s in self._sessions.items()
            if s.status in ("ready", "error")
        ]
        finished.sort()
        while len(finished) > 5:
            _, sid = finished.pop(0)
            del self._sessions[sid]


# Module-level singleton
session_manager = SessionManager()


# ------------------------------------------------------------------
# Pipeline helpers
# ------------------------------------------------------------------

def _emit_progress(session: GenerationSession, step: str, current: int, total: int, message: str) -> None:
    """Put a progress event onto the session's asyncio queue."""
    if session.progress_queue is None:
        return
    event = {"step": step, "current": current, "total": total, "message": message}
    try:
        session.progress_queue.put_nowait(event)
    except Exception:
        pass


def _output_dir() -> str:
    from web.app import BASE_DIR
    d = os.path.join(BASE_DIR, "output")
    os.makedirs(d, exist_ok=True)
    return d


def _images_dir() -> str:
    d = os.path.join(_output_dir(), "images")
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------
# Background generation pipeline
# ------------------------------------------------------------------

def _run_pipeline(session: GenerationSession) -> None:
    """Full pipeline: collect -> score -> resolve urls -> scrape. Runs in a thread."""
    try:
        config = session.config

        # Step 1: Collect
        session.status = "collecting"
        _emit_progress(session, "collecting", 0, 1, "RSS 뉴스를 수집하고 있습니다...")
        all_articles = collect_news(config)
        _emit_progress(session, "collecting", 1, 1, f"{len(all_articles)}개 기사 수집 완료")

        if not all_articles:
            session.status = "error"
            session.error_message = "수집된 기사가 없습니다"
            _emit_progress(session, "error", 0, 0, session.error_message)
            return

        # Step 2: Score & select
        session.status = "scoring"
        _emit_progress(session, "scoring", 0, 1, "기사를 분석하고 선별하고 있습니다...")
        picks = select_weekly_picks(all_articles, config)
        session.picks = picks
        _emit_progress(session, "scoring", 1, 1, "기사 선별 완료")

        all_picks = picks.get("main", []) + picks.get("market", []) + picks.get("other", [])
        if not all_picks:
            session.status = "error"
            session.error_message = "선정된 기사가 없습니다"
            _emit_progress(session, "error", 0, 0, session.error_message)
            return

        # Step 3: Resolve Google News URLs
        _emit_progress(session, "scraping", 0, len(all_picks), "URL을 해석하고 있습니다...")
        temp_articles = [
            NewsArticle(keyword=p.keyword, title=p.title, source=p.source, link=p.link, date=p.date)
            for p in all_picks
        ]
        resolve_google_urls(temp_articles)
        for pick, resolved in zip(all_picks, temp_articles):
            pick.link = resolved.link

        # Step 4: Scrape each article
        session.status = "scraping"
        images_dir = _images_dir()
        total = len(all_picks)

        for i, article in enumerate(all_picks):
            _emit_progress(
                session, "scraping", i, total,
                f"기사 스크래핑 중: {article.title[:30]}..."
            )
            try:
                scraped = scrape_article(
                    article.link, article.title,
                    download_images=True, image_output_dir=images_dir,
                )
                session.scraped_texts[article.link] = scraped.text
                session.scraped_articles[article.link] = scraped

                if scraped.image_paths:
                    filename = os.path.basename(scraped.image_paths[0])
                    session.image_refs[article.link] = f"![img](images/{filename})"

            except Exception:
                logger.exception("스크래핑 실패: %s", article.link)
                session.scraped_texts[article.link] = "(본문을 추출할 수 없습니다)"

        _emit_progress(session, "scraping", total, total, "모든 기사 스크래핑 완료")

        session.status = "ready"
        _emit_progress(session, "ready", total, total, "기사 생성 준비 완료")

    except Exception as exc:
        logger.exception("파이프라인 오류")
        session.status = "error"
        session.error_message = str(exc)
        _emit_progress(session, "error", 0, 0, f"오류 발생: {exc}")


def start_generation(config: dict) -> GenerationSession:
    """Create a session and start the pipeline in a background thread."""
    session = session_manager.create(config)
    thread = threading.Thread(target=_run_pipeline, args=(session,), daemon=True)
    thread.start()
    return session


# ------------------------------------------------------------------
# Article cards
# ------------------------------------------------------------------

def get_articles(session_id: str) -> dict[str, list[ArticleCard]] | None:
    """Return categorised article cards for a ready session."""
    session = session_manager.get(session_id)
    if session is None:
        return None

    result: dict[str, list[ArticleCard]] = {}
    global_index = 0

    for slot in ("main", "market", "other"):
        cards: list[ArticleCard] = []
        for article in session.picks.get(slot, []):
            body = session.scraped_texts.get(article.link, "")
            scraped = session.scraped_articles.get(article.link)
            image_url = scraped.image_url if scraped else ""
            image_local = ""
            if scraped and scraped.image_paths:
                image_local = f"/output/images/{os.path.basename(scraped.image_paths[0])}"

            card = ArticleCard(
                index=global_index,
                title=article.title,
                source=article.source,
                score=article.score,
                category=article.category,
                keyword=article.keyword,
                link=article.link,
                date=article.date,
                body_preview=body[:200] if body else "",
                body_full=body,
                image_url=image_url,
                image_local=image_local,
                replacement_count=session.replacement_counts.get(global_index, 0),
                max_replacements=MAX_REPLACEMENT_PER_SLOT,
            )
            cards.append(card)
            global_index += 1
        result[slot] = cards

    return result


# ------------------------------------------------------------------
# Replacement logic
# ------------------------------------------------------------------

def _find_article_by_index(session: GenerationSession, index: int) -> tuple[str, int, ScoredArticle | None]:
    """Return (slot_key, local_index, article) for a global index."""
    offset = 0
    for slot in ("main", "market", "other"):
        articles = session.picks.get(slot, [])
        if index < offset + len(articles):
            local_idx = index - offset
            return slot, local_idx, articles[local_idx]
        offset += len(articles)
    return "", -1, None


def replace_articles(session_id: str, article_indices: list[int]) -> list[dict] | None:
    """Replace specified articles. Returns list of replacement detail dicts."""
    session = session_manager.get(session_id)
    if session is None or session.status != "ready":
        return None

    results: list[dict] = []

    for idx in article_indices:
        count = session.replacement_counts.get(idx, 0)
        if count >= MAX_REPLACEMENT_PER_SLOT:
            logger.warning("교체 횟수 초과: index=%d, count=%d", idx, count)
            continue

        slot, local_idx, old_article = _find_article_by_index(session, idx)
        if old_article is None:
            continue

        # Build the "before" card
        before_card = _article_to_card(session, idx, old_article)

        # Exclude this article's keyword and re-collect
        excluded = session.excluded_keywords.setdefault(idx, [])
        excluded.append(old_article.keyword)

        replacement = _find_replacement(session, old_article, excluded, slot)
        if replacement is None:
            logger.warning("대체 기사를 찾을 수 없음: index=%d", idx)
            continue

        # Scrape the replacement
        images_dir = _images_dir()
        try:
            scraped = scrape_article(
                replacement.link, replacement.title,
                download_images=True, image_output_dir=images_dir,
            )
            session.scraped_texts[replacement.link] = scraped.text
            session.scraped_articles[replacement.link] = scraped
            if scraped.image_paths:
                filename = os.path.basename(scraped.image_paths[0])
                session.image_refs[replacement.link] = f"![img](images/{filename})"
        except Exception:
            logger.exception("대체 기사 스크래핑 실패: %s", replacement.link)
            session.scraped_texts[replacement.link] = "(본문을 추출할 수 없습니다)"

        new_count = count + 1
        session.replacement_counts[idx] = new_count

        after_card = _article_to_card(session, idx, replacement)
        after_card.replacement_count = new_count

        # Store as pending replacement
        session.pending_replacements[idx] = {
            "slot": slot,
            "local_idx": local_idx,
            "old_article": old_article,
            "new_article": replacement,
            "before": before_card,
            "after": after_card,
        }

        results.append({
            "index": idx,
            "before": before_card.model_dump(),
            "after": after_card.model_dump(),
            "excluded_keyword": old_article.keyword,
            "replacement_count": new_count,
        })

    return results


def _find_replacement(
    session: GenerationSession,
    old_article: ScoredArticle,
    excluded_keywords: list[str],
    target_slot: str,
) -> ScoredArticle | None:
    """Re-collect, re-score, filter, and pick the best replacement."""
    config = copy.deepcopy(session.config)

    # Remove excluded keywords from config
    rss_keywords: list[str] = list(config.get("rss", {}).get("keywords", []))
    for ek in excluded_keywords:
        if ek in rss_keywords:
            rss_keywords.remove(ek)
    if not rss_keywords:
        return None
    config.setdefault("rss", {})["keywords"] = rss_keywords

    # Re-collect
    try:
        new_articles = collect_news(config)
    except Exception:
        logger.exception("대체 기사 수집 실패")
        return None

    if not new_articles:
        return None

    # Resolve URLs
    resolve_google_urls(new_articles)

    # Score
    scored = [score_article(a, session.config) for a in new_articles]

    # Filter: same category as old article
    scored = [a for a in scored if a.category == old_article.category]

    # Filter: not similar to any already-selected article
    all_existing = session._flat_picks()
    filtered: list[ScoredArticle] = []
    for candidate in scored:
        is_dup = False
        for existing in all_existing:
            if candidate.title == existing.title or _is_similar_title(candidate.title, existing.title):
                is_dup = True
                break
        if not is_dup:
            filtered.append(candidate)

    if not filtered:
        return None

    # Sort by score descending, pick best
    filtered.sort(key=lambda a: (a.score, a.date), reverse=True)
    return filtered[0]


def _article_to_card(session: GenerationSession, index: int, article: ScoredArticle) -> ArticleCard:
    """Convert a ScoredArticle to an ArticleCard."""
    body = session.scraped_texts.get(article.link, "")
    scraped = session.scraped_articles.get(article.link)
    image_url = scraped.image_url if scraped else ""
    image_local = ""
    if scraped and scraped.image_paths:
        image_local = f"/output/images/{os.path.basename(scraped.image_paths[0])}"

    return ArticleCard(
        index=index,
        title=article.title,
        source=article.source,
        score=article.score,
        category=article.category,
        keyword=article.keyword,
        link=article.link,
        date=article.date,
        body_preview=body[:200] if body else "",
        body_full=body,
        image_url=image_url,
        image_local=image_local,
        replacement_count=session.replacement_counts.get(index, 0),
        max_replacements=MAX_REPLACEMENT_PER_SLOT,
    )


# ------------------------------------------------------------------
# Approve / Cancel replacements
# ------------------------------------------------------------------

def approve_replacement(session_id: str, indices: list[int], action: str) -> bool:
    """Handle replacement approval. action: 'approve' | 'retry' | 'cancel'."""
    session = session_manager.get(session_id)
    if session is None:
        return False

    for idx in indices:
        pending = session.pending_replacements.get(idx)
        if pending is None:
            continue

        if action == "approve":
            # Replace the article in picks
            slot = pending["slot"]
            local_idx = pending["local_idx"]
            session.picks[slot][local_idx] = pending["new_article"]
            del session.pending_replacements[idx]

        elif action == "cancel":
            # Revert the replacement count
            count = session.replacement_counts.get(idx, 1)
            session.replacement_counts[idx] = max(0, count - 1)
            del session.pending_replacements[idx]

        elif action == "retry":
            # Keep the current replacement count, remove pending (will re-run replace)
            del session.pending_replacements[idx]

    return True


# ------------------------------------------------------------------
# Preview & Confirm
# ------------------------------------------------------------------

def generate_preview(session_id: str) -> str | None:
    """Generate markdown preview from current session picks."""
    session = session_manager.get(session_id)
    if session is None or session.status != "ready":
        return None

    return build_reframe_table(session.picks, session.scraped_texts, session.image_refs)


def confirm(session_id: str) -> tuple[str, str] | None:
    """Save the final markdown file. Returns (history_id, md_filename) or None."""
    session = session_manager.get(session_id)
    if session is None or session.status != "ready":
        return None

    markdown = build_reframe_table(session.picks, session.scraped_texts, session.image_refs)

    monday, sunday = get_week_range()
    md_filename = f"주간_기사_모음_{monday.isoformat()}_{sunday.isoformat()}.md"

    # Ensure unique filename
    output_dir = _output_dir()
    md_path = os.path.join(output_dir, md_filename)
    counter = 1
    while os.path.exists(md_path):
        md_filename = f"주간_기사_모음_{monday.isoformat()}_{sunday.isoformat()}_{counter}.md"
        md_path = os.path.join(output_dir, md_filename)
        counter += 1

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    history_id = datetime.now(KST).strftime("%Y%m%d%H%M%S")
    logger.info("마크다운 저장 완료: %s", md_path)

    return history_id, md_filename
