"""Generation API router.

Handles the full article generation lifecycle:
start -> poll/stream status -> view articles -> replace -> preview -> confirm.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

import markdown as md
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from scraper.notice_scraper import get_week_range
from web.models import (
    ArticleSummary,
    ConfirmResponse,
    GenerationArticlesResponse,
    GenerationStartResponse,
    GenerationStatusResponse,
    HistoryEntry,
    PreviewResponse,
    ProgressInfo,
    ReplaceApproveRequest,
    ReplaceRequest,
    ReplaceResponse,
    ReplacementDetail,
    UpdateArticleBodyRequest,
)
from web.services import article_service, history_service

logger = logging.getLogger(__name__)

router = APIRouter()

KST = timezone(timedelta(hours=9))


# ------------------------------------------------------------------
# POST /start
# ------------------------------------------------------------------

@router.post("/cancel")
async def cancel_generation():
    """활성 세션을 취소합니다."""
    cancelled = article_service.session_manager.cancel_active()
    return {"cancelled_session_id": cancelled}


@router.post("/start", response_model=GenerationStartResponse)
async def start_generation():
    """새로운 기사 생성 세션을 시작합니다."""
    from web.app import get_config
    config = get_config()
    if not config:
        raise HTTPException(status_code=500, detail="설정이 로드되지 않았습니다")

    try:
        session = article_service.start_generation(config)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return GenerationStartResponse(
        session_id=session.session_id,
        status=session.status,
    )


# ------------------------------------------------------------------
# GET /{session_id}/status
# ------------------------------------------------------------------

@router.get("/{session_id}/status", response_model=GenerationStatusResponse)
async def get_status(session_id: str):
    """세션의 현재 상태를 폴링합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    progress = None
    # Drain queue to get latest progress
    if session.progress_queue is not None:
        latest = None
        while not session.progress_queue.empty():
            try:
                latest = session.progress_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if latest:
            progress = ProgressInfo(**latest)

    return GenerationStatusResponse(
        session_id=session.session_id,
        status=session.status,
        progress=progress,
        error=session.error_message or None,
    )


# ------------------------------------------------------------------
# GET /{session_id}/status/stream  (SSE)
# ------------------------------------------------------------------

@router.get("/{session_id}/status/stream")
async def stream_status(session_id: str):
    """SSE 스트림으로 실시간 진행 상태를 전송합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    async def event_generator():
        while True:
            if session.progress_queue is not None:
                try:
                    event = await asyncio.wait_for(session.progress_queue.get(), timeout=1.0)
                    data = json.dumps(event, ensure_ascii=False)
                    step = event.get("step", "")

                    if step in ("ready",):
                        yield f"event: complete\ndata: {data}\n\n"
                        break
                    elif step in ("error",):
                        yield f"event: error_event\ndata: {data}\n\n"
                        break
                    else:
                        yield f"event: progress\ndata: {data}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
            else:
                await asyncio.sleep(1)

            # Also break if session ended without queue event
            if session.status in ("ready", "error"):
                final = {
                    "step": session.status,
                    "current": 0,
                    "total": 0,
                    "message": session.error_message if session.status == "error" else "완료",
                }
                evt = "complete" if session.status == "ready" else "error_event"
                yield f"event: {evt}\ndata: {json.dumps(final, ensure_ascii=False)}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# GET /{session_id}/articles
# ------------------------------------------------------------------

@router.get("/{session_id}/articles", response_model=GenerationArticlesResponse)
async def get_articles(session_id: str):
    """생성된 기사 카드 목록을 반환합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if session.status not in ("ready",):
        raise HTTPException(status_code=409, detail="기사 생성이 아직 완료되지 않았습니다")

    cards = article_service.get_articles(session_id)
    if cards is None:
        raise HTTPException(status_code=404, detail="기사를 찾을 수 없습니다")

    return GenerationArticlesResponse(session_id=session_id, articles=cards)


# ------------------------------------------------------------------
# POST /{session_id}/replace
# ------------------------------------------------------------------

@router.post("/{session_id}/replace", response_model=ReplaceResponse)
async def replace_articles(session_id: str, req: ReplaceRequest):
    """선택된 기사를 교체합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="기사 생성이 완료된 후에만 교체할 수 있습니다")

    results = article_service.replace_articles(session_id, req.article_indices)
    if results is None:
        raise HTTPException(status_code=400, detail="교체에 실패했습니다")

    replacements = []
    for r in results:
        from web.models import ArticleCard
        replacements.append(ReplacementDetail(
            index=r["index"],
            before=ArticleCard(**r["before"]),
            after=ArticleCard(**r["after"]),
            excluded_keyword=r["excluded_keyword"],
            replacement_count=r["replacement_count"],
        ))

    return ReplaceResponse(replacements=replacements, status="pending_approval")


# ------------------------------------------------------------------
# POST /{session_id}/replace/approve
# ------------------------------------------------------------------

@router.post("/{session_id}/replace/approve")
async def approve_replacement(session_id: str, req: ReplaceApproveRequest):
    """교체를 승인/재시도/취소합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    if req.action not in ("approve", "retry", "cancel"):
        raise HTTPException(status_code=400, detail="action은 approve, retry, cancel 중 하나여야 합니다")

    ok = article_service.approve_replacement(session_id, req.article_indices, req.action)
    if not ok:
        raise HTTPException(status_code=400, detail="교체 승인/취소에 실패했습니다")

    return {"status": "ok", "action": req.action, "indices": req.article_indices}


# ------------------------------------------------------------------
# PATCH /{session_id}/articles/{index}/body
# ------------------------------------------------------------------

@router.patch("/{session_id}/articles/{index}/body")
async def update_article_body(session_id: str, index: int, req: UpdateArticleBodyRequest):
    """기사 본문을 수정합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="기사 생성이 완료된 후에만 수정할 수 있습니다")

    ok = article_service.update_article_body(session_id, index, req.body_full)
    if not ok:
        raise HTTPException(status_code=400, detail="본문 수정에 실패했습니다")

    return {"status": "ok", "index": index}


# ------------------------------------------------------------------
# GET /{session_id}/preview
# ------------------------------------------------------------------

@router.get("/{session_id}/preview", response_model=PreviewResponse)
async def preview(session_id: str):
    """마크다운 미리보기를 반환합니다 (원본 + HTML)."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="기사 생성이 완료된 후에만 미리보기할 수 있습니다")

    raw = article_service.generate_preview(session_id)
    if raw is None:
        raise HTTPException(status_code=500, detail="미리보기 생성에 실패했습니다")

    html = md.markdown(raw, extensions=["tables"])
    return PreviewResponse(markdown_raw=raw, markdown_html=html)


# ------------------------------------------------------------------
# POST /{session_id}/confirm
# ------------------------------------------------------------------

@router.post("/{session_id}/confirm", response_model=ConfirmResponse)
async def confirm(session_id: str):
    """마크다운을 파일로 저장하고 히스토리에 등록합니다."""
    session = article_service.session_manager.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="기사 생성이 완료된 후에만 확정할 수 있습니다")

    result = article_service.confirm(session_id)
    if result is None:
        raise HTTPException(status_code=500, detail="파일 저장에 실패했습니다")

    history_id, md_filename = result

    # Build article summaries for history entry
    summaries: list[ArticleSummary] = []
    all_picks = session._flat_picks()
    for i, article in enumerate(all_picks):
        slot = session._slot_for_index(i)
        summaries.append(ArticleSummary(
            title=article.title,
            source=article.source,
            score=article.score,
            slot=slot,
            keyword=article.keyword,
            link=article.link,
            date=article.date,
            replaced=session.replacement_counts.get(i, 0) > 0,
        ))

    monday, sunday = get_week_range()

    entry = HistoryEntry(
        id=history_id,
        created_at=datetime.now(KST).isoformat(),
        week_range=[monday.isoformat(), sunday.isoformat()],
        article_count=len(all_picks),
        status="미리보기",
        md_filename=md_filename,
        dooray_page_id=None,
        articles=summaries,
    )
    history_service.add_entry(entry)

    return ConfirmResponse(
        history_id=history_id,
        md_filename=md_filename,
        status="미리보기",
    )
