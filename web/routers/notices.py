"""Notices API router.

Collects e-commerce platform notices and uploads to Dooray wiki.
"""

import asyncio
import json
import logging

import markdown as md
from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from dooray.wiki_client import DoorayApiError, DoorayWikiClient
from scraper.notice_scraper import (
    TARGETS,
    NoticeTarget,
    collect_all_notices,
    format_notices_markdown,
    get_week_label,
    get_week_range,
)
from web.models import (
    NoticeItem,
    NoticePlatform,
    NoticesCollectResponse,
    NoticesUploadRequest,
    NoticesUploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# POST /collect
# ------------------------------------------------------------------

def _do_collect(targets, week_range):
    """Blocking collection — run inside executor."""
    return collect_all_notices(targets, week_range)


@router.get("/collect/stream")
async def collect_notices_stream():
    """SSE 스트림으로 플랫폼별 수집 진행 상태를 전송합니다."""
    from web.app import get_config

    config = get_config()

    raw_targets = config.get("notices", {}).get("targets", [])
    if raw_targets:
        targets = [
            NoticeTarget(
                name=t["name"],
                url=t["url"],
                encoding=t["encoding"],
                lookahead_days=t.get("lookahead_days", 0),
            )
            for t in raw_targets
        ]
    else:
        targets = TARGETS

    week_range = get_week_range()
    monday, sunday = week_range

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def on_progress(name: str, status: str):
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "progress", "name": name, "status": status})

    def run_collect():
        return collect_all_notices(targets, week_range, on_progress=on_progress)

    async def event_generator():
        # Send platform list first
        platform_names = [t.name for t in targets]
        yield f"data: {json.dumps({'type': 'platforms', 'names': platform_names}, ensure_ascii=False)}\n\n"

        # Start collection in thread pool
        task = loop.run_in_executor(None, run_collect)

        # Stream progress events
        while True:
            done = task.done()
            # Drain all queued events
            while not queue.empty():
                event = queue.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if done:
                break
            await asyncio.sleep(0.1)

        # Get result
        notices_by_target, errors = await task

        # Build final response (same as POST /collect)
        platforms = []
        for target in targets:
            notices = notices_by_target.get(target.name, [])
            target_error = errors.get(target.name)
            platforms.append(NoticePlatform(
                name=target.name,
                count=len(notices),
                status="error" if target_error else "ok",
                error=target_error,
                notices=[
                    NoticeItem(
                        title=n.title,
                        url=n.url,
                        date=n.date.isoformat() if n.date else None,
                    )
                    for n in notices
                ],
            ))

        total_count = sum(p.count for p in platforms)
        markdown_raw = format_notices_markdown(notices_by_target, week_range)
        markdown_html = md.markdown(markdown_raw, extensions=["tables"])

        result = NoticesCollectResponse(
            week_range=[monday.isoformat(), sunday.isoformat()],
            platforms=platforms,
            total_count=total_count,
            markdown_raw=markdown_raw,
            markdown_html=markdown_html,
        )
        yield f"data: {json.dumps({'type': 'complete', 'data': result.model_dump()}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/collect", response_model=NoticesCollectResponse)
async def collect_notices():
    """이커머스 플랫폼 공지사항을 수집합니다."""
    from web.app import get_config

    config = get_config()

    # Build targets from config, fallback to hardcoded defaults
    raw_targets = config.get("notices", {}).get("targets", [])
    if raw_targets:
        targets = [
            NoticeTarget(
                name=t["name"],
                url=t["url"],
                encoding=t["encoding"],
                lookahead_days=t.get("lookahead_days", 0),
            )
            for t in raw_targets
        ]
    else:
        targets = TARGETS

    week_range = get_week_range()
    monday, sunday = week_range

    # Run blocking I/O in thread pool
    result = await asyncio.get_event_loop().run_in_executor(
        None, _do_collect, targets, week_range
    )
    notices_by_target, errors = result

    # Build response
    platforms = []
    for target in targets:
        notices = notices_by_target.get(target.name, [])
        target_error = errors.get(target.name)
        platforms.append(NoticePlatform(
            name=target.name,
            count=len(notices),
            status="error" if target_error else "ok",
            error=target_error,
            notices=[
                NoticeItem(
                    title=n.title,
                    url=n.url,
                    date=n.date.isoformat() if n.date else None,
                )
                for n in notices
            ],
        ))

    total_count = sum(p.count for p in platforms)

    markdown_raw = format_notices_markdown(notices_by_target, week_range)
    markdown_html = md.markdown(markdown_raw, extensions=["tables"])

    return NoticesCollectResponse(
        week_range=[monday.isoformat(), sunday.isoformat()],
        platforms=platforms,
        total_count=total_count,
        markdown_raw=markdown_raw,
        markdown_html=markdown_html,
    )


# ------------------------------------------------------------------
# POST /upload
# ------------------------------------------------------------------

@router.post("/upload", response_model=NoticesUploadResponse)
async def upload_notices(req: NoticesUploadRequest):
    """수집된 공지사항을 두레이 위키에 업로드합니다."""
    from web.app import get_config

    config = get_config()
    dooray_config = config.get("dooray", {})
    api_token = dooray_config.get("api_token", "")
    wiki_id = dooray_config.get("wiki_id", "")
    parent_page_id = dooray_config.get("notices_parent_page_id", "")

    # Request body overrides
    if req.wiki_id:
        wiki_id = req.wiki_id
    if req.parent_page_id:
        parent_page_id = req.parent_page_id

    if not api_token or not wiki_id:
        raise HTTPException(
            status_code=500,
            detail="두레이 API 토큰 또는 위키 ID가 설정되지 않았습니다",
        )
    if not parent_page_id:
        raise HTTPException(
            status_code=422,
            detail="notices_parent_page_id_missing",
        )

    try:
        client = DoorayWikiClient(api_token=api_token, wiki_id=wiki_id)

        monday, sunday = get_week_range()
        week_label = get_week_label()
        subject = f"주간 공지사항 ({week_label})"

        page_result = client.create_page(
            parent_page_id=parent_page_id,
            subject=subject,
            content=req.markdown_raw,
        )
        page_id = page_result.get("id", "")

        if not page_id:
            raise HTTPException(
                status_code=500,
                detail="위키 페이지 생성 결과에서 ID를 찾을 수 없습니다",
            )

        page_url = f"https://nhnent.dooray.com/wiki/{wiki_id}/pages/{page_id}"

        return NoticesUploadResponse(
            status="업로드완료",
            dooray_page_id=page_id,
            dooray_page_url=page_url,
        )

    except DoorayApiError as e:
        logger.exception("두레이 API 오류")
        raise HTTPException(status_code=502, detail=f"두레이 API 오류: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("공지사항 업로드 중 오류")
        raise HTTPException(status_code=500, detail=f"업로드 중 오류: {str(e)}")
