"""History service for managing output/history.json."""

import glob
import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta

from web.models import ArticleSummary, HistoryEntry

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_lock = threading.Lock()

# Resolved lazily on first call via _history_path()
_BASE_DIR: str = ""
_HISTORY_PATH: str = ""


def _history_path() -> str:
    """Return the absolute path to history.json, initialising lazily."""
    global _BASE_DIR, _HISTORY_PATH
    if not _HISTORY_PATH:
        from web.app import BASE_DIR
        _BASE_DIR = BASE_DIR
        output_dir = os.path.join(_BASE_DIR, "output")
        os.makedirs(output_dir, exist_ok=True)
        _HISTORY_PATH = os.path.join(output_dir, "history.json")
    return _HISTORY_PATH


def _output_dir() -> str:
    _history_path()  # ensure _BASE_DIR is set
    return os.path.join(_BASE_DIR, "output")


# ------------------------------------------------------------------
# Low-level read/write
# ------------------------------------------------------------------

def load_history() -> dict:
    """Load history.json. If missing, bootstrap from existing .md files."""
    path = _history_path()
    with _lock:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        # Bootstrap from output/*.md
        return _bootstrap_history()


def save_history(data: dict) -> None:
    """Write the full history dict to disk."""
    path = _history_path()
    with _lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _bootstrap_history() -> dict:
    """Scan output/*.md and create an initial history.json."""
    output = _output_dir()
    data: dict = {"version": 1, "entries": []}

    md_files = sorted(glob.glob(os.path.join(output, "*.md")))
    for md_path in md_files:
        filename = os.path.basename(md_path)
        stat = os.stat(md_path)
        created = datetime.fromtimestamp(stat.st_mtime, tz=KST)

        entry_id = created.strftime("%Y%m%d%H%M%S")
        # Try to extract week range from filename pattern:
        # 주간_기사_모음_2026-03-02_2026-03-08.md
        week_range = _extract_week_range(filename)

        entry = HistoryEntry(
            id=entry_id,
            created_at=created.isoformat(),
            week_range=week_range,
            article_count=0,
            status="미리보기",
            md_filename=filename,
            dooray_page_id=None,
            articles=[],
        )
        data["entries"].append(entry.model_dump())

    # Save bootstrapped file (caller already holds _lock)
    path = _history_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info("history.json 생성 완료: %d개 항목", len(data["entries"]))
    return data


def _extract_week_range(filename: str) -> list[str]:
    """Try to extract [monday_iso, sunday_iso] from md filename."""
    import re
    m = re.findall(r"(\d{4}-\d{2}-\d{2})", filename)
    if len(m) >= 2:
        return [m[0], m[1]]
    return ["", ""]


# ------------------------------------------------------------------
# Entry management
# ------------------------------------------------------------------

def add_entry(entry: HistoryEntry) -> None:
    """Append a new entry to history."""
    data = load_history()
    data["entries"].append(entry.model_dump())
    save_history(data)


def update_status(entry_id: str, status: str, **kwargs) -> HistoryEntry | None:
    """Update an entry's status (and optional extra fields). Returns updated entry or None."""
    data = load_history()
    for raw in data["entries"]:
        if raw["id"] == entry_id:
            raw["status"] = status
            for k, v in kwargs.items():
                if k in raw:
                    raw[k] = v
            save_history(data)
            return HistoryEntry(**raw)
    return None


def get_recent(n: int = 5) -> list[HistoryEntry]:
    """Return the *n* most recent entries (newest first)."""
    data = load_history()
    entries = data.get("entries", [])
    # Sort by created_at descending
    entries_sorted = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)
    return [HistoryEntry(**e) for e in entries_sorted[:n]]


def get_paginated(
    page: int = 1,
    per_page: int = 10,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[HistoryEntry], int]:
    """Return a page of entries with optional filters. Returns (items, total)."""
    data = load_history()
    entries = data.get("entries", [])

    # Filter
    filtered = []
    for raw in entries:
        # Search filter: match in md_filename or article titles
        if search:
            haystack = raw.get("md_filename", "").lower()
            for art in raw.get("articles", []):
                haystack += " " + art.get("title", "").lower()
            if search.lower() not in haystack:
                continue

        # Date range filter (against created_at)
        created = raw.get("created_at", "")
        if date_from and created < date_from:
            continue
        if date_to and created > date_to + "T23:59:59":
            continue

        filtered.append(raw)

    # Sort newest first
    filtered.sort(key=lambda e: e.get("created_at", ""), reverse=True)

    total = len(filtered)
    start = (page - 1) * per_page
    page_items = filtered[start: start + per_page]

    return [HistoryEntry(**e) for e in page_items], total


def get_entry_by_id(entry_id: str) -> HistoryEntry | None:
    """Lookup a single entry by id."""
    data = load_history()
    for raw in data.get("entries", []):
        if raw["id"] == entry_id:
            return HistoryEntry(**raw)
    return None


def get_markdown_content(entry_id: str) -> str | None:
    """Read the MD file content for a given entry."""
    entry = get_entry_by_id(entry_id)
    if entry is None:
        return None
    md_path = os.path.join(_output_dir(), entry.md_filename)
    if not os.path.isfile(md_path):
        return None
    with open(md_path, encoding="utf-8") as f:
        return f.read()
