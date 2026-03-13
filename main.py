"""Weekly automation entry point.

Orchestrates two workflows:
1. Articles: RSS collect -> score -> scrape -> reframe markdown -> Dooray wiki
2. Notices: Scrape e-commerce notices -> Dooray wiki page
"""

import argparse
import logging
import os
import sys

import yaml
from dotenv import load_dotenv

from collector.rss import NewsArticle, collect_news, resolve_google_urls
from dooray.wiki_client import DoorayWikiClient
from processor.markdown import build_reframe_table
from scorer.ranking import ScoredArticle, select_weekly_picks
from scraper.article_scraper import scrape_article
from scraper.notice_scraper import (
    NoticeTarget,
    collect_all_notices,
    format_notices_markdown,
    get_week_range,
)

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load config.yaml with environment variable override for API token."""
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # API token from .env (never hardcoded in config.yaml)
    token = os.environ.get("DOORAY_API_TOKEN", "")
    config.setdefault("dooray", {})["api_token"] = token

    return config


def setup_logging() -> None:
    # Fix Windows console encoding for Unicode output
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("automation.log", encoding="utf-8"),
        ],
    )


# ------------------------------------------------------------------
# Workflow 1: Articles (RSS -> Score -> Scrape -> Reframe -> Dooray)
# ------------------------------------------------------------------

def _print_picks_summary(picks: dict[str, list[ScoredArticle]]) -> None:
    """Print selected articles summary to terminal."""
    print("\n" + "=" * 60)
    print("  📰 Weekly Picks 선정 결과")
    print("=" * 60)

    for label, key in [
        ("🏆 메인 톱픽", "main"),
        ("💡 기타 커머스/IT 동향", "other"),
        ("🛒 오픈마켓/소셜커머스", "market"),
    ]:
        articles = picks.get(key, [])
        print(f"\n{label}:")
        for i, a in enumerate(articles, 1):
            print(f"  {i}. [{a.score}점] {a.title} ({a.source})")
            print(f"     키워드: {a.keyword} | {a.date}")

    total = sum(len(v) for v in picks.values())
    print(f"\n  총 {total}개 기사 선정")
    print("=" * 60 + "\n")


def _resolve_pick_urls(all_picks: list[ScoredArticle]) -> None:
    """Resolve Google News URLs for selected articles using googlenewsdecoder."""
    # Build temporary NewsArticle wrappers to reuse resolve_google_urls
    temp_articles = [
        NewsArticle(keyword=p.keyword, title=p.title, source=p.source,
                    link=p.link, date=p.date)
        for p in all_picks
    ]
    resolve_google_urls(temp_articles)
    # Write resolved URLs back to ScoredArticle objects
    for pick, resolved in zip(all_picks, temp_articles):
        if pick.link != resolved.link:
            logger.info("URL 해석: %s -> %s", pick.title[:30], resolved.link[:80])
        pick.link = resolved.link


def run_articles_workflow(
    config: dict,
    week_range: tuple,
    dry_run: bool = False,
) -> None:
    """Collect RSS news, score, scrape, and save as local markdown file."""
    monday, sunday = week_range

    # Step 1: Collect news from RSS
    logger.info("Step 1: RSS 뉴스 수집 시작...")
    all_articles = collect_news(config)

    if not all_articles:
        logger.warning("수집된 기사가 없습니다")
        return

    logger.info("수집된 기사: %d개", len(all_articles))

    # Step 2: Score and select 7 articles
    logger.info("Step 2: 스코어링 및 7개 기사 선정...")
    picks = select_weekly_picks(all_articles, config)
    _print_picks_summary(picks)

    all_picks = picks["main"] + picks["other"] + picks["market"]
    if not all_picks:
        logger.warning("선정된 기사가 없습니다")
        return

    # Step 3: Resolve Google News URLs for selected articles only
    logger.info("Step 3: 선정된 %d개 기사 URL 해석...", len(all_picks))
    _resolve_pick_urls(all_picks)

    if dry_run:
        logger.info("Dry-run 모드: 파일 생성 생략")
        return

    # Step 4: Prepare output directories (clean old images)
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    images_dir = os.path.join(output_dir, "images")
    if os.path.isdir(images_dir):
        for f in os.listdir(images_dir):
            try:
                os.unlink(os.path.join(images_dir, f))
            except OSError:
                pass
    os.makedirs(images_dir, exist_ok=True)

    # Step 5: Scrape each article (images saved to output/images/)
    scraped_texts: dict[str, str] = {}
    image_refs: dict[str, str] = {}

    for idx, article in enumerate(all_picks, 1):
        try:
            scraped = scrape_article(
                article.link, article.title,
                download_images=True, image_output_dir=images_dir,
            )
            scraped_texts[article.link] = scraped.text
            logger.info("크롤링 완료: %s (%d자)", article.title, len(scraped.text))

            if scraped.image_paths:
                old_path = scraped.image_paths[0]
                ext = os.path.splitext(old_path)[1]  # e.g. ".jpg"
                new_filename = f"{idx}{ext}"
                new_path = os.path.join(images_dir, new_filename)
                os.rename(old_path, new_path)
                image_refs[article.link] = f"![img](images/{new_filename})"

        except Exception:
            logger.exception("크롤링 실패: %s (%s)", article.title, article.link)
            scraped_texts[article.link] = "(본문을 추출할 수 없습니다)"

    # Step 6: Build reframe table markdown and save to file
    logger.info("Step 6: Reframe 테이블 마크다운 생성...")
    table_markdown = build_reframe_table(picks, scraped_texts, image_refs)

    md_filename = f"주간_기사_모음_{monday.isoformat()}_{sunday.isoformat()}.md"
    md_path = os.path.join(output_dir, md_filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(table_markdown)

    logger.info("마크다운 파일 저장: %s", md_path)
    logger.info(
        "기사 워크플로우 완료: %d/%d 크롤링 성공",
        len(scraped_texts),
        len(all_picks),
    )


# ------------------------------------------------------------------
# Workflow 2: Notices
# ------------------------------------------------------------------

def run_notices_workflow(
    config: dict,
    wiki_client: DoorayWikiClient,
    week_range: tuple,
) -> None:
    """Scrape e-commerce notices and write to Dooray wiki."""
    monday, sunday = week_range

    targets = [
        NoticeTarget(name=t["name"], url=t["url"], encoding=t["encoding"])
        for t in config.get("notices", {}).get("targets", [])
    ]

    if not targets:
        logger.warning("공지사항 대상이 설정되지 않았습니다")
        return

    notices_by_target = collect_all_notices(targets, week_range)
    content = format_notices_markdown(notices_by_target, week_range)

    parent_page_id = config["dooray"].get("notices_parent_page_id", "")
    if not parent_page_id:
        logger.error("dooray.notices_parent_page_id가 설정되지 않았습니다")
        return

    page_subject = f"주간 공지사항 ({monday.isoformat()} ~ {sunday.isoformat()})"
    wiki_client.create_page(
        parent_page_id=parent_page_id,
        subject=page_subject,
        content=content,
    )

    total = sum(len(ns) for ns in notices_by_target.values())
    logger.info("공지사항 워크플로우 완료: %d개 from %d targets", total, len(targets))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="주간 뉴스 자동 큐레이션")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="RSS 수집 + 스코어링만 실행 (두레이 업로드 생략)",
    )
    parser.add_argument(
        "--articles-only",
        action="store_true",
        help="기사 워크플로우만 실행 (공지사항 생략)",
    )
    parser.add_argument(
        "--notices-only",
        action="store_true",
        help="공지사항 워크플로우만 실행 (기사 생략)",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="설정 파일 경로 (기본: config.yaml)",
    )
    args = parser.parse_args()

    setup_logging()

    # Load .env from same directory as config
    env_path = os.path.join(os.path.dirname(os.path.abspath(args.config)), ".env")
    load_dotenv(env_path)

    config = load_config(args.config)

    week_range = get_week_range()
    monday, sunday = week_range
    logger.info("=== 주간 자동화: %s ~ %s ===", monday, sunday)

    # Run articles workflow (local MD file output)
    if not args.notices_only:
        try:
            run_articles_workflow(config, week_range, dry_run=args.dry_run)
        except Exception:
            logger.exception("기사 워크플로우 실패")

    # Run notices workflow (still uses Dooray API)
    if not args.articles_only and not args.dry_run:
        wiki_client = None
        token = config["dooray"].get("api_token", "")
        wiki_id = config["dooray"].get("wiki_id", "")
        if token and wiki_id:
            wiki_client = DoorayWikiClient(api_token=token, wiki_id=wiki_id)
        if wiki_client:
            try:
                run_notices_workflow(config, wiki_client, week_range)
            except Exception:
                logger.exception("공지사항 워크플로우 실패")

    logger.info("=== 주간 자동화 완료 ===")


if __name__ == "__main__":
    main()
