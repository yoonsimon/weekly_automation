"""Upload API router.

Handles uploading confirmed markdown + images to Dooray wiki.
Includes image upload and category-based sorting.
"""

import logging
import os
import re
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

from dooray.wiki_client import DoorayApiError, DoorayWikiClient
from web.models import ArticleSummary, HistoryEntry, UploadResponse
from web.services import history_service

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadRequest(BaseModel):
    parent_page_id: str | None = None
    wiki_id: str | None = None


def _get_dirs() -> tuple[str, str]:
    from web.app import BASE_DIR
    output_dir = os.path.join(BASE_DIR, "output")
    images_dir = os.path.join(output_dir, "images")
    return output_dir, images_dir


def _parse_dooray_wiki_url(url: str) -> tuple[str, str]:
    """Dooray 위키 URL에서 wiki_id와 page_id를 추출합니다.

    형식: https://{org}.dooray.com/wiki/{wiki_id}/{page_id}
    """
    m = re.match(r"https?://[^/]+/wiki/(\d+)/(\d+)", url.strip())
    if not m:
        raise ValueError("올바른 Dooray 위키 URL 형식이 아닙니다")
    return m.group(1), m.group(2)


# ------------------------------------------------------------------
# Category sorting
# ------------------------------------------------------------------

CATEGORY_ORDER = {
    "최상단": 1,
    "기타 커머스/IT 동향": 2,
    "오픈마켓/소셜커머스": 3,
}


def _sort_table_rows(md_content: str) -> str:
    """테이블 행을 카테고리 순서대로 정렬합니다.

    순서: 최상단 → 기타 커머스/IT 동향 → 오픈마켓/소셜커머스
    """
    lines = md_content.strip().split("\n")
    if not lines or not lines[0].strip().startswith("|"):
        return md_content

    header_lines = lines[:2]
    data_lines = [l for l in lines[2:] if l.strip().startswith("|")]

    def row_sort_key(line: str) -> int:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 2:
            return 99
        category = cells[1].replace("**", "").strip()
        category = category.split("<br>")[0].strip()
        return CATEGORY_ORDER.get(category, 99)

    data_lines.sort(key=row_sort_key)
    return "\n".join(header_lines + data_lines)


# ------------------------------------------------------------------
# Image upload helpers
# ------------------------------------------------------------------

def _find_image_refs(content: str) -> list[str]:
    """마크다운 콘텐츠에서 images/article_img_xxx 형태의 파일명을 추출합니다."""
    return re.findall(r'images/(article_img_[^)]+)', content)


def _upload_images_and_replace(
    client: DoorayWikiClient,
    page_id: str,
    content: str,
    images_dir: str,
) -> str:
    """이미지를 두레이에 업로드하고 마크다운 경로를 page-files로 치환합니다."""
    image_files = _find_image_refs(content)
    if not image_files:
        return content

    file_id_map: dict[str, str] = {}
    for img_file in image_files:
        img_path = os.path.join(images_dir, img_file)
        if not os.path.isfile(img_path):
            logger.warning("이미지 파일 없음, 건너뜀: %s", img_file)
            continue

        try:
            page_file_id = client.upload_file(page_id, img_path)
            file_id_map[img_file] = page_file_id
            logger.info("이미지 업로드 성공: %s → %s", img_file, page_file_id)
        except Exception as e:
            logger.error("이미지 업로드 실패: %s (%s)", img_file, e)

    logger.info("이미지 업로드: %d/%d건 성공", len(file_id_map), len(image_files))

    # 성공한 이미지 경로 치환
    result = content
    for img_file, fid in file_id_map.items():
        result = result.replace(f"images/{img_file}", f"/page-files/{fid}")

    # 실패한 이미지 참조 제거
    result = re.sub(r'!\[[^\]]*\]\(images/[^)]+\)\s*', '', result)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


# ------------------------------------------------------------------
# Table → Section converter
# ------------------------------------------------------------------

UPLOAD_BODY_MAX_CHARS = 0  # 0 = 제한 없음 (원문 전체 업로드)


def _table_to_sections(md_content: str) -> str:
    """마크다운 테이블을 섹션(제목+본문) 형식으로 변환합니다.

    테이블 행: | 날짜 | 카테고리 | 내용 | 이미지 |
    내용 셀:  [제목](url)<br><br>본문...

    변환 결과:
      ### 제목
      **카테고리** · 날짜

      본문 (최대 UPLOAD_BODY_MAX_CHARS자)

      이미지

      ---
    """
    lines = md_content.strip().split("\n")

    if not lines or not lines[0].strip().startswith("|"):
        return md_content

    sections: list[str] = []
    for line in lines[2:]:
        line = line.strip()
        if not line.startswith("|"):
            continue

        raw_cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(raw_cells) < 3:
            continue

        date_str = raw_cells[0]
        category_raw = raw_cells[1]
        content_raw = raw_cells[2]
        image_raw = raw_cells[3] if len(raw_cells) > 3 else ""

        category = category_raw.replace("**", "").replace("<br>", " / ")

        title_link = ""
        body_text = content_raw

        link_match = re.match(r'\[([^\]]*)\]\(([^)]+)\)', content_raw)
        if link_match:
            title = link_match.group(1)
            url = link_match.group(2)
            title_link = f"[{title}]({url})"
            body_text = content_raw[link_match.end():]

        body_text = re.sub(r'^(<br>)+', '', body_text).strip()
        body_text = body_text.replace("<br><br>", "\n\n").replace("<br>", "\n")
        body_text = body_text.replace("&lt;", "<").replace("&gt;", ">")

        if UPLOAD_BODY_MAX_CHARS > 0 and len(body_text) > UPLOAD_BODY_MAX_CHARS:
            truncated = body_text[:UPLOAD_BODY_MAX_CHARS]
            last_space = truncated.rfind(" ")
            last_newline = truncated.rfind("\n")
            cut_at = max(last_space, last_newline)
            if cut_at > UPLOAD_BODY_MAX_CHARS // 2:
                truncated = truncated[:cut_at]
            body_text = truncated.rstrip() + " ..."

        parts = []
        if title_link:
            parts.append(f"### {title_link}")
        parts.append(f"**{category}** · {date_str}")
        parts.append("")
        if body_text:
            parts.append(body_text)
            parts.append("")
        if image_raw:
            parts.append(image_raw)
            parts.append("")
        parts.append("---")

        sections.append("\n".join(parts))

    if not sections:
        return md_content

    return "\n\n".join(sections)


# ------------------------------------------------------------------
# Local upload helpers
# ------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

SLOT_MAP = {
    "최상단": "main",
    "오픈마켓/소셜커머스": "market",
    "기타 커머스/IT 동향": "other",
}


def _safe_save_path(directory: str, filename: str) -> str:
    """Return a path in *directory* for *filename*, appending _1, _2 etc. on collision."""
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{name}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _parse_articles_from_md(md_content: str) -> list[ArticleSummary]:
    """Parse article rows from a markdown table."""
    lines = md_content.strip().split("\n")
    articles: list[ArticleSummary] = []

    for line in lines[2:]:  # skip header + separator
        line = line.strip()
        if not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 3:
            continue

        date_str = cells[0]

        # category cell: **카테고리**<br>키워드
        cat_raw = cells[1]
        cat_clean = cat_raw.replace("**", "").strip()
        cat_parts = cat_clean.split("<br>")
        category = cat_parts[0].strip()
        keyword = cat_parts[1].strip() if len(cat_parts) > 1 else ""

        # content cell: [title](url)<br><br>body...
        content_raw = cells[2]
        title = ""
        link = ""
        link_match = re.match(r'\[([^\]]*)\]\(([^)]+)\)', content_raw)
        if link_match:
            title = link_match.group(1)
            link = link_match.group(2)

        slot = SLOT_MAP.get(category, "other")

        articles.append(ArticleSummary(
            title=title,
            source=keyword,
            score=0,
            slot=slot,
            keyword=keyword,
            link=link,
            date=date_str,
            replaced=False,
        ))

    return articles


@router.post("/local")
async def upload_local(
    markdown: UploadFile = File(...),
    images: list[UploadFile] | None = File(default=None),
):
    """로컬에서 생성한 마크다운과 이미지를 서버에 업로드하여 히스토리를 생성합니다."""
    output_dir, images_dir = _get_dirs()
    os.makedirs(images_dir, exist_ok=True)

    # 1. Save markdown file
    md_bytes = await markdown.read()
    md_content = md_bytes.decode("utf-8")
    original_filename = markdown.filename or "upload.md"
    md_save_path = _safe_save_path(output_dir, original_filename)
    saved_filename = os.path.basename(md_save_path)

    with open(md_save_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info("마크다운 저장: %s", md_save_path)

    # 2. Save image files
    saved_images = 0
    for img_file in (images or []):
        if not img_file.filename:
            continue
        img_bytes = await img_file.read()
        img_save_path = _safe_save_path(images_dir, img_file.filename)
        with open(img_save_path, "wb") as f:
            f.write(img_bytes)
        saved_images += 1

    logger.info("이미지 %d개 저장", saved_images)

    # 3. Parse articles from markdown table
    articles = _parse_articles_from_md(md_content)

    # 4. Create history entry
    now = datetime.now(KST)
    entry_id = now.strftime("%Y%m%d%H%M%S")
    week_range = history_service._extract_week_range(saved_filename)

    entry = HistoryEntry(
        id=entry_id,
        created_at=now.isoformat(),
        week_range=week_range,
        article_count=len(articles),
        status="미리보기",
        md_filename=saved_filename,
        dooray_page_id=None,
        articles=articles,
    )
    history_service.add_entry(entry)
    logger.info("히스토리 항목 생성: %s (%d개 기사)", entry_id, len(articles))

    return {
        "history_id": entry_id,
        "md_filename": saved_filename,
        "article_count": len(articles),
        "status": "미리보기",
    }


# ------------------------------------------------------------------
# API endpoints
# ------------------------------------------------------------------

class ResolveWikiUrlRequest(BaseModel):
    url: str


@router.post("/resolve-wiki-url")
async def resolve_wiki_url(body: ResolveWikiUrlRequest):
    """Dooray 위키 URL을 파싱하고 해당 페이지 정보를 반환합니다."""
    try:
        wiki_id_from_url, page_id = _parse_dooray_wiki_url(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from web.app import get_config
    config = get_config()
    dooray_config = config.get("dooray", {})
    api_token = dooray_config.get("api_token", "")

    if not api_token:
        raise HTTPException(status_code=500, detail="두레이 API 토큰이 설정되지 않았습니다")

    try:
        client = DoorayWikiClient(api_token=api_token, wiki_id=wiki_id_from_url)
        page_info = client.get_page_content(page_id)
        subject = page_info.get("subject", "")
        return {
            "page_id": page_id,
            "wiki_id": wiki_id_from_url,
            "subject": subject,
            "url": body.url.strip(),
        }
    except DoorayApiError as e:
        raise HTTPException(status_code=502, detail=f"페이지 정보 조회 실패: {e}")


@router.post("/{history_id}", response_model=UploadResponse)
async def upload_to_dooray(history_id: str, body: UploadRequest | None = None):
    """히스토리 항목의 마크다운과 이미지를 두레이 위키에 업로드합니다."""
    # 1. Load entry
    entry = history_service.get_entry_by_id(history_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="히스토리 항목을 찾을 수 없습니다")

    # 2. Load markdown content
    md_content = history_service.get_markdown_content(history_id)
    if md_content is None:
        raise HTTPException(status_code=404, detail="마크다운 파일을 찾을 수 없습니다")

    # 3. Build wiki client
    from web.app import get_config
    config = get_config()
    dooray_config = config.get("dooray", {})
    api_token = dooray_config.get("api_token", "")
    wiki_id = dooray_config.get("wiki_id", "")
    parent_page_id = dooray_config.get("articles_parent_page_id", "")

    if body and body.parent_page_id:
        parent_page_id = body.parent_page_id
    if body and body.wiki_id:
        wiki_id = body.wiki_id

    if not api_token or not wiki_id:
        raise HTTPException(status_code=500, detail="두레이 API 토큰 또는 위키 ID가 설정되지 않았습니다")
    if not parent_page_id:
        raise HTTPException(
            status_code=422,
            detail="articles_parent_page_id_missing",
        )

    try:
        client = DoorayWikiClient(api_token=api_token, wiki_id=wiki_id)
        _, images_dir = _get_dirs()

        # 4. 카테고리 순서대로 테이블 행 정렬
        sorted_content = _sort_table_rows(md_content)

        # 5. 테이블 → 섹션 형식 변환 (두레이 렌더링 호환)
        upload_content = _table_to_sections(sorted_content)

        # 6. 페이지 생성 (placeholder - 이미지 업로드 후 본문 업데이트)
        from scraper.notice_scraper import get_week_label
        week_label = get_week_label()
        subject = f"주간 기사 모음 ({week_label})"

        page_result = client.create_page(
            parent_page_id=parent_page_id,
            subject=subject,
            content="업로드 중...",
        )
        page_id = page_result.get("id", "")

        if not page_id:
            raise HTTPException(status_code=500, detail="위키 페이지 생성 결과에서 ID를 찾을 수 없습니다")

        # 7. 이미지 업로드 + 경로 치환
        upload_content = _upload_images_and_replace(
            client, page_id, upload_content, images_dir
        )

        # 8. 페이지 본문 업데이트
        client.modify_page_content(page_id, upload_content)
        logger.info("페이지 업데이트 완료: %s", page_id)

        # 9. Update history entry status
        history_service.update_status(
            history_id, "업로드완료", dooray_page_id=page_id
        )

        page_url = f"https://nhnent.dooray.com/wiki/{wiki_id}/pages/{page_id}"

        return UploadResponse(
            history_id=history_id,
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
        logger.exception("업로드 중 오류 발생")
        raise HTTPException(status_code=500, detail=f"업로드 중 오류: {str(e)}")
