"""로컬에서 생성한 주간 기사 마크다운과 이미지를 서버에 업로드합니다.

Usage:
    python upload_local.py [--server URL] [--file path/to/file.md]

Defaults:
    --server: RENDER_SERVER_URL 환경변수 또는 http://localhost:8000
    --file:   output/ 디렉터리에서 가장 최근 주간_기사_모음_*.md 파일
"""

import argparse
import glob
import os
import re
import sys

import requests


def find_latest_md(output_dir: str) -> str | None:
    """output/ 에서 가장 최근 주간_기사_모음_*.md 파일을 찾습니다."""
    pattern = os.path.join(output_dir, "주간_기사_모음_*.md")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def parse_image_refs(md_content: str) -> list[str]:
    """마크다운에서 참조된 이미지 파일명을 추출합니다."""
    # Match images/filename patterns in markdown image syntax
    return re.findall(r'images/([^)]+)', md_content)


def main():
    parser = argparse.ArgumentParser(description="로컬 주간 기사를 서버에 업로드")
    parser.add_argument(
        "--server",
        default=os.environ.get("RENDER_SERVER_URL", "http://localhost:8000"),
        help="서버 URL (기본: RENDER_SERVER_URL 환경변수 또는 http://localhost:8000)",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="업로드할 마크다운 파일 경로 (기본: output/ 최신 파일)",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "output")

    # 1. Find markdown file
    md_path = args.file
    if md_path is None:
        md_path = find_latest_md(output_dir)
        if md_path is None:
            print("[오류] output/ 디렉터리에서 주간_기사_모음_*.md 파일을 찾을 수 없습니다.")
            sys.exit(1)

    md_path = os.path.abspath(md_path)
    if not os.path.isfile(md_path):
        print(f"[오류] 파일을 찾을 수 없습니다: {md_path}")
        sys.exit(1)

    print(f"[파일] {os.path.basename(md_path)}")

    # 2. Read markdown and find referenced images
    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()

    image_refs = parse_image_refs(md_content)
    images_dir = os.path.join(os.path.dirname(md_path), "images")

    # Collect existing image files
    image_files: list[str] = []
    for img_name in image_refs:
        img_path = os.path.join(images_dir, img_name)
        if os.path.isfile(img_path):
            image_files.append(img_path)
        else:
            print(f"[경고] 이미지 파일 없음, 건너뜀: {img_name}")

    print(f"[이미지] {len(image_files)}개 이미지 업로드 예정")

    # 3. Build multipart request
    server_url = args.server.rstrip("/")
    upload_url = f"{server_url}/api/upload/local"

    files_payload: list[tuple] = []

    # markdown file
    md_filename = os.path.basename(md_path)
    files_payload.append(
        ("markdown", (md_filename, open(md_path, "rb"), "text/markdown"))
    )

    # image files
    for img_path in image_files:
        img_filename = os.path.basename(img_path)
        files_payload.append(
            ("images", (img_filename, open(img_path, "rb"), "image/jpeg"))
        )

    # 4. Upload
    print(f"[업로드] {upload_url} 로 전송 중...")

    try:
        resp = requests.post(upload_url, files=files_payload, timeout=120)
    except requests.ConnectionError:
        print(f"[오류] 서버에 연결할 수 없습니다: {server_url}")
        sys.exit(1)
    except requests.Timeout:
        print("[오류] 업로드 시간 초과 (120초)")
        sys.exit(1)
    finally:
        # Close all opened file handles
        for _, file_tuple in files_payload:
            file_tuple[1].close()

    if resp.status_code != 200:
        print(f"[오류] 서버 응답 {resp.status_code}: {resp.text}")
        sys.exit(1)

    result = resp.json()
    history_id = result.get("history_id", "")
    article_count = result.get("article_count", 0)
    md_filename = result.get("md_filename", "")

    print()
    print("=== 업로드 완료 ===")
    print(f"  히스토리 ID : {history_id}")
    print(f"  파일명      : {md_filename}")
    print(f"  기사 수     : {article_count}건")
    print(f"  대시보드    : {server_url}/")
    print()


if __name__ == "__main__":
    main()
