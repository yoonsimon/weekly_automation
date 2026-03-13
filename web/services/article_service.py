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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from collector.rss import NewsArticle, collect_news
from processor.markdown import build_reframe_table
from scorer.ranking import ScoredArticle, score_article, select_weekly_picks, _is_similar_title
from scraper.article_scraper import ScrapedArticle, scrape_article
from scraper.notice_scraper import get_week_label, get_week_range
from web.models import ArticleCard

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MAX_REPLACEMENT_PER_SLOT = 5
POOL_TTL_SECONDS = 1800  # 후보 풀 캐시 유효 시간 (30분)

_ERROR_CODE_LABELS: dict[str, str] = {
    "paywall": "유료 콘텐츠",
    "blocked": "접근 차단",
    "not_found": "페이지 없음",
    "server_error": "서버 오류",
    "timeout": "시간 초과",
    "network": "네트워크 오류",
    "parse_failed": "파싱 실패",
}


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
    scrape_errors: dict[str, str] = field(default_factory=dict)  # link -> error_code

    # Replacement tracking
    excluded_keywords: dict[int, list[str]] = field(default_factory=dict)
    replacement_counts: dict[int, int] = field(default_factory=dict)
    pending_replacements: dict[int, dict] = field(default_factory=dict)

    # Candidate pool cache (avoids re-collecting RSS on every replacement)
    candidate_pool: list[ScoredArticle] = field(default_factory=list)
    pool_created_at: float = 0.0

    # Prefetched replacement candidates (ready to serve instantly)
    prefetched: dict[int, dict] = field(default_factory=dict)
    _prefetch_lock: threading.Lock = field(default_factory=threading.Lock)

    # Progress events (asyncio.Queue is created per session)
    progress_queue: asyncio.Queue | None = None

    error_message: str = ""
    created_at: float = field(default_factory=time.time)

    def _flat_picks(self) -> list[ScoredArticle]:
        """Return all picks in a flat list preserving slot order."""
        result: list[ScoredArticle] = []
        for key in ("main", "other", "market"):
            result.extend(self.picks.get(key, []))
        return result

    def _slot_for_index(self, index: int) -> str:
        """Return slot key for global index."""
        offset = 0
        for key in ("main", "other", "market"):
            articles = self.picks.get(key, [])
            if index < offset + len(articles):
                return key
            offset += len(articles)
        return "market"


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

    def cancel_active(self) -> str | None:
        """Cancel any active session. Returns cancelled session_id or None."""
        with self._lock:
            for s in self._sessions.values():
                if s.status in ("collecting", "scoring", "scraping"):
                    s.status = "error"
                    s.error_message = "새 세션 시작으로 취소됨"
                    return s.session_id
            return None

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
        current_step = "뉴스 수집"

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

        current_step = "분석/선별"
        # Step 2: Score & select
        session.status = "scoring"
        _emit_progress(session, "scoring", 0, 1, "기사를 분석하고 선별하고 있습니다...")

        # Cache scored pool for later replacement reuse
        scored_all = [score_article(a, config) for a in all_articles]
        scored_all.sort(key=lambda a: (a.score, a.date), reverse=True)
        session.candidate_pool = scored_all
        session.pool_created_at = time.time()

        picks = select_weekly_picks(all_articles, config)
        session.picks = picks
        _emit_progress(session, "scoring", 1, 1, "기사 선별 완료")

        all_picks = picks.get("main", []) + picks.get("other", []) + picks.get("market", [])
        if not all_picks:
            session.status = "error"
            session.error_message = "선정된 기사가 없습니다"
            _emit_progress(session, "error", 0, 0, session.error_message)
            return

        current_step = "본문 스크래핑"
        # Step 3: Scrape articles in parallel (URL resolution is now inline in _fetch_page)
        session.status = "scraping"
        images_dir = _images_dir()
        total = len(all_picks)
        progress_lock = threading.Lock()
        completed_count = 0

        def _scrape_one(idx_article):
            idx, article = idx_article
            try:
                scraped = scrape_article(
                    article.link, article.title,
                    download_images=True, image_output_dir=images_dir,
                )
                return idx, article, scraped, None
            except Exception as e:
                logger.exception("스크래핑 실패: %s", article.link)
                return idx, article, None, e

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_scrape_one, (i, a)): i
                for i, a in enumerate(all_picks)
            }
            for future in as_completed(futures):
                idx, article, scraped, err = future.result()
                if scraped:
                    session.scraped_texts[article.link] = scraped.text
                    session.scraped_articles[article.link] = scraped
                    if scraped.error_code:
                        session.scrape_errors[article.link] = scraped.error_code
                    if scraped.image_paths:
                        filename = os.path.basename(scraped.image_paths[0])
                        session.image_refs[article.link] = f"![img](images/{filename})"
                else:
                    session.scraped_texts[article.link] = "(본문을 추출할 수 없습니다)"
                    session.scrape_errors[article.link] = "network"

                with progress_lock:
                    completed_count += 1
                    _emit_progress(
                        session, "scraping", completed_count, total,
                        f"스크래핑 완료: {article.title[:30]}... ({completed_count}/{total})"
                    )

        _emit_progress(session, "scraping", total, total, "모든 기사 스크래핑 완료")

        session.status = "ready"
        _emit_progress(session, "ready", total, total, "기사 생성 준비 완료")

        # Start background prefetch for all slots
        threading.Thread(target=_prefetch_all_slots, args=(session,), daemon=True).start()

    except Exception as exc:
        logger.exception("파이프라인 오류")
        session.status = "error"
        session.error_message = f"[{current_step}] {exc}"
        _emit_progress(session, "error", 0, 0, f"[{current_step}] 오류 발생: {exc}")


def _prefetch_single_slot(session: GenerationSession, idx: int) -> None:
    """Prefetch a single replacement candidate for the given slot index."""
    try:
        if not session.candidate_pool:
            return

        _, _, current_article = _find_article_by_index(session, idx)
        if current_article is None:
            return

        excluded = session.excluded_keywords.get(idx, [])
        with session._prefetch_lock:
            batch_chosen = [
                session.prefetched[i]["article"].title
                for i in session.prefetched
                if i != idx
            ]

        slot = session._slot_for_index(idx)
        candidate = _pick_from_pool(
            session, session.candidate_pool, current_article, excluded, batch_chosen, slot=slot,
        )
        if candidate is None:
            return

        images_dir = _images_dir()
        scraped = scrape_article(
            candidate.link, candidate.title,
            download_images=True, image_output_dir=images_dir,
        )

        with session._prefetch_lock:
            session.prefetched[idx] = {"article": candidate, "scraped": scraped}
        logger.info("프리페치 완료: slot %d → %s", idx, candidate.title[:30])

    except Exception:
        logger.exception("프리페치 실패: slot %d", idx)


def _prefetch_all_slots(session: GenerationSession) -> None:
    """Prefetch replacement candidates for all 7 slots in parallel."""
    all_picks = session._flat_picks()
    with ThreadPoolExecutor(max_workers=4) as pool:
        pool.map(
            lambda idx: _prefetch_single_slot(session, idx),
            range(len(all_picks)),
        )
    logger.info("전체 프리페치 완료: %d개 슬롯 준비됨", len(session.prefetched))


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

    for slot in ("main", "other", "market"):
        cards: list[ArticleCard] = []
        for article in session.picks.get(slot, []):
            body = session.scraped_texts.get(article.link, "")
            scraped = session.scraped_articles.get(article.link)
            image_url = scraped.image_url if scraped else ""
            image_local = ""
            if scraped and scraped.image_paths:
                image_local = f"images/{os.path.basename(scraped.image_paths[0])}"

            error_code = session.scrape_errors.get(article.link, "")
            scrape_error = _ERROR_CODE_LABELS.get(error_code, "") if error_code else ""

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
                scrape_status=_determine_scrape_status(body),
                scrape_error=scrape_error,
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
    for slot in ("main", "other", "market"):
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

    # Reuse cached candidate pool if still valid, otherwise collect fresh
    pool_age = time.time() - session.pool_created_at
    if session.candidate_pool and pool_age < POOL_TTL_SECONDS:
        scored_pool = session.candidate_pool
        logger.info("후보 풀 캐시 사용 (경과 %.0f초)", pool_age)
    else:
        try:
            new_articles = collect_news(session.config)
        except Exception:
            logger.exception("대체 기사 수집 실패")
            return None

        if not new_articles:
            return None

        scored_pool = [score_article(a, session.config) for a in new_articles]
        scored_pool.sort(key=lambda a: (a.score, a.date), reverse=True)
        session.candidate_pool = scored_pool
        session.pool_created_at = time.time()
        logger.info("후보 풀 새로 수집: %d개 후보", len(scored_pool))

    results: list[dict] = []
    # Track titles chosen in THIS batch to prevent duplicate replacements
    batch_chosen_titles: list[str] = []

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

        # Exclude this article's keyword
        excluded = session.excluded_keywords.setdefault(idx, [])
        excluded.append(old_article.keyword)

        # Check if a prefetched candidate is available for instant replacement
        prefetched = None
        with session._prefetch_lock:
            prefetched = session.prefetched.pop(idx, None)

        # Validate prefetched candidate is not a duplicate of another replacement in this batch
        if prefetched is not None:
            pf_title = prefetched["article"].title
            is_batch_dup = any(
                pf_title == t or _is_similar_title(pf_title, t)
                for t in batch_chosen_titles
            )
            if is_batch_dup:
                logger.info("프리페치 캐시 중복 → 풀에서 재선택: slot %d, title=%s", idx, pf_title[:30])
                prefetched = None  # fall through to pool-based picking

        if prefetched is not None:
            replacement = prefetched["article"]
            scraped = prefetched["scraped"]
            logger.info("프리페치 캐시 히트: slot %d → %s", idx, replacement.title[:30])

            session.scraped_texts[replacement.link] = scraped.text
            session.scraped_articles[replacement.link] = scraped
            if scraped.image_paths:
                filename = os.path.basename(scraped.image_paths[0])
                session.image_refs[replacement.link] = f"![img](images/{filename})"
        else:
            # Fallback: pick from pool and scrape on-demand
            replacement = _pick_from_pool(
                session, scored_pool, old_article, excluded, batch_chosen_titles, slot=slot,
            )
            if replacement is None:
                logger.warning("대체 기사를 찾을 수 없음: index=%d", idx)
                continue

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

        batch_chosen_titles.append(replacement.title)

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

        # Re-prefetch next candidate for this slot in background
        if new_count < MAX_REPLACEMENT_PER_SLOT:
            threading.Thread(
                target=_prefetch_single_slot, args=(session, idx), daemon=True,
            ).start()

    return results


def _pick_from_pool(
    session: GenerationSession,
    scored_pool: list[ScoredArticle],
    old_article: ScoredArticle,
    excluded_keywords: list[str],
    batch_chosen_titles: list[str],
    slot: str = "",
) -> ScoredArticle | None:
    """Pick the best non-duplicate replacement from the pre-scored pool."""
    # Existing picks + pending replacements from earlier batches
    all_existing = session._flat_picks()
    for pending in session.pending_replacements.values():
        all_existing.append(pending["new_article"])

    for candidate in scored_pool:
        # Must match category (main slot allows any category)
        if slot != "main" and candidate.category != old_article.category:
            continue

        # Skip excluded keywords
        if candidate.keyword in excluded_keywords:
            continue

        # Skip if similar to any existing or pending article
        is_dup = False
        for existing in all_existing:
            if candidate.title == existing.title or _is_similar_title(candidate.title, existing.title):
                is_dup = True
                break
        if is_dup:
            continue

        # Skip if already chosen in this batch
        batch_dup = False
        for chosen_title in batch_chosen_titles:
            if candidate.title == chosen_title or _is_similar_title(candidate.title, chosen_title):
                batch_dup = True
                break
        if batch_dup:
            continue

        # URL resolution is now inline in scraper's _fetch_page
        return candidate

    return None


def _determine_scrape_status(body: str) -> str:
    """Determine scrape status based on body content."""
    if not body or "(본문을 추출할 수 없습니다)" in body:
        return "failed"
    if len(body) < 200:
        return "partial"
    return "ok"


def _article_to_card(session: GenerationSession, index: int, article: ScoredArticle) -> ArticleCard:
    """Convert a ScoredArticle to an ArticleCard."""
    body = session.scraped_texts.get(article.link, "")
    scraped = session.scraped_articles.get(article.link)
    image_url = scraped.image_url if scraped else ""
    image_local = ""
    if scraped and scraped.image_paths:
        image_local = f"images/{os.path.basename(scraped.image_paths[0])}"

    error_code = session.scrape_errors.get(article.link, "")
    scrape_error = _ERROR_CODE_LABELS.get(error_code, "") if error_code else ""

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
        scrape_status=_determine_scrape_status(body),
        scrape_error=scrape_error,
    )


# ------------------------------------------------------------------
# Update article body
# ------------------------------------------------------------------

def update_article_body(session_id: str, index: int, new_body: str) -> bool:
    """Update the body text for an article by index."""
    session = session_manager.get(session_id)
    if session is None or session.status != "ready":
        return False

    _, _, article = _find_article_by_index(session, index)
    if article is None:
        return False

    session.scraped_texts[article.link] = new_body
    return True


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
    week_label = get_week_label()
    md_filename = f"주간_기사_모음_{week_label}.md"

    # Ensure unique filename
    output_dir = _output_dir()
    md_path = os.path.join(output_dir, md_filename)
    counter = 1
    while os.path.exists(md_path):
        md_filename = f"주간_기사_모음_{week_label}_{counter}.md"
        md_path = os.path.join(output_dir, md_filename)
        counter += 1

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    history_id = datetime.now(KST).strftime("%Y%m%d%H%M%S")
    logger.info("마크다운 저장 완료: %s", md_path)

    return history_id, md_filename
