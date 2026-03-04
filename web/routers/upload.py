"""Upload API router.

Handles uploading confirmed markdown + images to Dooray wiki.
"""

import logging
import os
import re

from fastapi import APIRouter, HTTPException

from dooray.wiki_client import DoorayApiError, DoorayWikiClient
from web.models import UploadResponse
from web.services import history_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_dirs() -> tuple[str, str]:
    from web.app import BASE_DIR
    output_dir = os.path.join(BASE_DIR, "output")
    images_dir = os.path.join(output_dir, "images")
    return output_dir, images_dir


@router.post("/{history_id}", response_model=UploadResponse)
async def upload_to_dooray(history_id: str):
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

    if not api_token or not wiki_id:
        raise HTTPException(status_code=500, detail="두레이 API 토큰 또는 위키 ID가 설정되지 않았습니다")
    if not parent_page_id:
        raise HTTPException(status_code=500, detail="dooray.articles_parent_page_id가 설정되지 않았습니다")

    try:
        client = DoorayWikiClient(api_token=api_token, wiki_id=wiki_id)

        # 4. Create wiki page
        week_label = ""
        if entry.week_range and len(entry.week_range) >= 2:
            week_label = f" ({entry.week_range[0]} ~ {entry.week_range[1]})"
        subject = f"주간 기사 모음{week_label}"

        page_result = client.create_page(
            parent_page_id=parent_page_id,
            subject=subject,
            content=md_content,
        )
        page_id = page_result.get("id", "")

        if not page_id:
            raise HTTPException(status_code=500, detail="위키 페이지 생성 결과에서 ID를 찾을 수 없습니다")

        # 5. Upload images and replace local refs with Dooray refs
        updated_content = md_content
        image_pattern = re.compile(r"!\[([^\]]*)\]\(images/([^)]+)\)")

        for match in image_pattern.finditer(md_content):
            alt_text = match.group(1)
            image_filename = match.group(2)
            _, images_dir = _get_dirs()
            image_path = os.path.join(images_dir, image_filename)

            if not os.path.isfile(image_path):
                logger.warning("이미지 파일 없음: %s", image_path)
                continue

            try:
                page_file_id = client.upload_file(page_id, image_path)
                dooray_ref = f"![{alt_text}](/page-files/{page_file_id})"
                local_ref = match.group(0)
                updated_content = updated_content.replace(local_ref, dooray_ref)
                logger.info("이미지 업로드 완료: %s -> %s", image_filename, page_file_id)
            except Exception:
                logger.exception("이미지 업로드 실패: %s", image_filename)

        # 6. Update page content with Dooray image refs
        if updated_content != md_content:
            client.modify_page_content(page_id, updated_content)
            logger.info("위키 페이지 이미지 참조 업데이트 완료")

        # 7. Update history entry status
        history_service.update_status(
            history_id, "업로드완료", dooray_page_id=page_id
        )

        return UploadResponse(
            history_id=history_id,
            status="업로드완료",
            dooray_page_id=page_id,
        )

    except DoorayApiError as e:
        logger.exception("두레이 API 오류")
        raise HTTPException(status_code=502, detail=f"두레이 API 오류: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("업로드 중 오류 발생")
        raise HTTPException(status_code=500, detail=f"업로드 중 오류: {str(e)}")
