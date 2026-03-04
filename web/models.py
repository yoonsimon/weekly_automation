"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class ArticleSummary(BaseModel):
    title: str
    source: str
    score: int
    slot: str  # "main" | "market" | "other"
    keyword: str
    link: str
    date: str
    replaced: bool = False


class HistoryEntry(BaseModel):
    id: str
    created_at: str
    week_range: list[str]  # [monday_iso, sunday_iso]
    article_count: int
    status: str  # "생성중" | "미리보기" | "업로드완료"
    md_filename: str
    dooray_page_id: str | None = None
    articles: list[ArticleSummary] = []


class HistoryListResponse(BaseModel):
    items: list[HistoryEntry]
    total: int
    page: int
    per_page: int


class MarkdownResponse(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class GenerationStartResponse(BaseModel):
    session_id: str
    status: str


class ProgressInfo(BaseModel):
    step: str
    current: int
    total: int
    message: str


class GenerationStatusResponse(BaseModel):
    session_id: str
    status: str  # "collecting" | "scoring" | "scraping" | "ready" | "error"
    progress: ProgressInfo | None = None
    error: str | None = None


class ArticleCard(BaseModel):
    index: int
    title: str
    source: str
    score: int
    category: str
    keyword: str
    link: str
    date: str
    body_preview: str  # first ~200 chars
    body_full: str
    image_url: str
    image_local: str
    replacement_count: int = 0
    max_replacements: int = 3
    scrape_status: str = "ok"  # "ok" | "partial" | "failed"


class GenerationArticlesResponse(BaseModel):
    session_id: str
    articles: dict[str, list[ArticleCard]]  # main/market/other


class UpdateArticleBodyRequest(BaseModel):
    body_full: str


class ReplaceRequest(BaseModel):
    article_indices: list[int]


class ReplacementDetail(BaseModel):
    index: int
    before: ArticleCard
    after: ArticleCard
    excluded_keyword: str
    replacement_count: int


class ReplaceResponse(BaseModel):
    replacements: list[ReplacementDetail]
    status: str  # "pending_approval"


class ReplaceApproveRequest(BaseModel):
    article_indices: list[int]
    action: str  # "approve" | "retry" | "cancel"


class PreviewResponse(BaseModel):
    markdown_raw: str
    markdown_html: str


class ConfirmResponse(BaseModel):
    history_id: str
    md_filename: str
    status: str


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class UploadResponse(BaseModel):
    history_id: str
    status: str
    dooray_page_id: str
