"""History API router."""

import io
import os
import re
import zipfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from web.models import HistoryEntry, HistoryListResponse, MarkdownResponse
from web.services import history_service

router = APIRouter()


@router.get("/recent", response_model=list[HistoryEntry])
async def recent_entries():
    """최근 5개 히스토리 항목을 반환합니다."""
    return history_service.get_recent(n=5)


@router.get("/", response_model=HistoryListResponse)
async def list_entries(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    search: str | None = Query(None),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):
    """페이지네이션된 히스토리 목록을 반환합니다."""
    items, total = history_service.get_paginated(
        page=page,
        per_page=per_page,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    return HistoryListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{entry_id}", response_model=HistoryEntry)
async def get_entry(entry_id: str):
    """단일 히스토리 항목을 반환합니다."""
    entry = history_service.get_entry_by_id(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다")
    return entry


@router.get("/{entry_id}/markdown", response_model=MarkdownResponse)
async def get_markdown(entry_id: str):
    """히스토리 항목의 마크다운 원본을 반환합니다."""
    content = history_service.get_markdown_content(entry_id)
    if content is None:
        raise HTTPException(status_code=404, detail="마크다운 파일을 찾을 수 없습니다")
    return MarkdownResponse(content=content)


@router.get("/{entry_id}/images/zip")
async def download_history_images_zip(entry_id: str):
    """히스토리 항목의 이미지를 ZIP으로 다운로드합니다."""
    content = history_service.get_markdown_content(entry_id)
    if content is None:
        raise HTTPException(status_code=404, detail="마크다운 파일을 찾을 수 없습니다")

    # Extract image filenames from markdown: ![img](images/filename)
    image_refs = re.findall(r'!\[.*?\]\((images/[^)]+)\)', content)
    output_dir = history_service._output_dir()

    image_files: list[str] = []
    for ref in image_refs:
        filepath = os.path.join(output_dir, ref)
        if os.path.isfile(filepath):
            image_files.append(filepath)

    if not image_files:
        raise HTTPException(status_code=404, detail="다운로드할 이미지가 없습니다")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filepath in image_files:
            zf.write(filepath, os.path.basename(filepath))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=images.zip"},
    )
