"""Reframe table markdown generator.

Builds a markdown table for copy-paste into Dooray wiki:
| 날짜 | 카테고리 | 내용 | 이미지 |

Format notes:
- Body text: <br> between lines, <br><br> between paragraphs
- No backslash escaping (strip problematic chars instead)
- Full body text (no truncation)
- Images: local relative paths (images/filename.ext)
"""

import logging
import re
from dataclasses import dataclass

from scorer.ranking import ScoredArticle

logger = logging.getLogger(__name__)

# Source map from reframe_server.py for display name mapping
SOURCE_MAP = {
    "yna.co.kr": "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "joongang.co.kr": "중앙일보",
    "khan.co.kr": "경향신문",
    "newspim.com": "뉴스핌",
    "inews24.com": "아이뉴스24",
    "sentv.co.kr": "서울경제TV",
    "ziksir.com": "직썰",
    "v.daum.net": "다음뉴스",
    "news.daum.net": "다음뉴스",
    "n.news.naver.com": "네이버뉴스",
    "news.naver.com": "네이버뉴스",
    "chosun.com": "조선일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "sedaily.com": "서울경제",
    "etnews.com": "전자신문",
    "zdnet.co.kr": "ZDNet Korea",
    "bloter.net": "블로터",
    "techm.kr": "테크M",
    "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    "asiae.co.kr": "아시아경제",
    "dt.co.kr": "디지털타임스",
    "biz.heraldcorp.com": "헤럴드경제",
    "heraldcorp.com": "헤럴드경제",
    "kukinews.com": "쿠키뉴스",
    "hankookilbo.com": "한국일보",
    "kmib.co.kr": "국민일보",
    "segye.com": "세계일보",
    "fnnews.com": "파이낸셜뉴스",
}


@dataclass
class ReframeRow:
    date_str: str       # "M/D" format
    category: str       # with **최상단** prefix for main
    content: str        # [title](url)<br><br>body...
    image_ref: str      # ![...](/page-files/{id}) or empty


def _format_short_date(date_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM' to 'M/D'."""
    if not date_str or len(date_str) < 10:
        return ""
    try:
        month = int(date_str[5:7])
        day = int(date_str[8:10])
        return f"{month}/{day}"
    except (ValueError, IndexError):
        return ""


def _safe_title(title: str) -> str:
    """Make title safe for markdown link text.

    Strips [ ] instead of escaping (Dooray renderer may not support \\[ \\]).
    Replaces | to prevent table column breaks.
    """
    title = title.replace("[", "").replace("]", "").replace("|", "-")
    title = title.replace("<", "&lt;").replace(">", "&gt;")
    return title


def _body_to_table_cell(text: str) -> str:
    """Convert article body text to table cell format matching Dooray's working style.

    Format: <br> between lines within a paragraph, <br><br> between paragraphs.
    No truncation — full body text included (matching working Dooray pages).
    IMPORTANT: No actual newlines — entire cell must be on one line for table syntax.
    """
    if not text:
        return "(본문을 추출할 수 없습니다)"

    # Replace pipe chars that would break the table
    text = text.replace("|", "-")

    # Escape angle brackets that look like HTML tags (e.g. <디지털데일리>)
    # These break Dooray's markdown renderer
    text = text.replace("<", "&lt;").replace(">", "&gt;")

    # Split into paragraphs (double newline = paragraph break)
    paragraphs = re.split(r'\n\n+', text.strip())

    # Within each paragraph, convert single newlines to <br>
    formatted = []
    for para in paragraphs:
        lines = [line.strip() for line in para.split('\n') if line.strip()]
        if lines:
            formatted.append("<br>".join(lines))

    # All on one line — <br><br> for paragraph breaks (no actual newlines!)
    return "<br><br>".join(formatted)


def _build_row(article: ScoredArticle, category_label: str,
               scraped_texts: dict[str, str],
               image_refs: dict[str, str]) -> ReframeRow:
    """Build a single ReframeRow matching Dooray's working format."""
    body = scraped_texts.get(article.link, "")
    cell_body = _body_to_table_cell(body)

    safe = _safe_title(article.title)
    content = f"[{safe}]({article.link})<br><br>{cell_body}"

    image = image_refs.get(article.link, "")

    return ReframeRow(
        date_str=_format_short_date(article.date),
        category=category_label,
        content=content,
        image_ref=image,
    )


def build_reframe_table(
    picks: dict[str, list[ScoredArticle]],
    scraped_texts: dict[str, str],
    image_refs: dict[str, str],
) -> str:
    """Build reframe markdown table from selected articles.

    Args:
        picks: Dict with 'main', 'market', 'other' lists of ScoredArticle.
        scraped_texts: Mapping of article URL -> scraped body text.
        image_refs: Mapping of article URL -> image markdown ref
                    (e.g., '![img](images/article_img_xxx.jpg)').

    Returns:
        Complete markdown table string.
    """
    rows: list[ReframeRow] = []

    # Main top pick
    for article in picks.get("main", []):
        category_label = f"**최상단**<br>**{article.category}**"
        rows.append(_build_row(article, category_label, scraped_texts, image_refs))

    # Market articles
    for article in picks.get("market", []):
        rows.append(_build_row(article, f"**{article.category}**",
                               scraped_texts, image_refs))

    # Other articles
    for article in picks.get("other", []):
        rows.append(_build_row(article, f"**{article.category}**",
                               scraped_texts, image_refs))

    # Build table
    lines = [
        "| 날짜 | 카테고리 | 내용 | 이미지 |",
        "| --- | ---- | --- | --- |",
    ]

    for row in rows:
        lines.append(
            f"| {row.date_str} | {row.category} | {row.content} | {row.image_ref} |"
        )

    return "\n".join(lines)
