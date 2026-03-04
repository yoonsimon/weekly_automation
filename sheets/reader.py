"""Google Spreadsheet reader for article entries."""

import logging
from dataclasses import dataclass

import gspread

logger = logging.getLogger(__name__)


@dataclass
class ArticleEntry:
    title: str
    link: str
    category: str = ""
    source: str = ""


class SheetsReader:
    """Reads article entries from a Google Spreadsheet."""

    def __init__(
        self,
        credentials_path: str,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> None:
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

    def read_articles(
        self,
        start_row: int = 2,
        title_col: str = "C",
        link_col: str = "E",
        category_col: str = "A",
        source_col: str = "D",
    ) -> list[ArticleEntry]:
        """Read article entries from the spreadsheet.

        Args:
            start_row: First data row (1-based, default 2 to skip header).
            title_col: Column letter for article title.
            link_col: Column letter for article link.
            category_col: Column letter for category.
            source_col: Column letter for source.

        Returns:
            List of ArticleEntry objects.
        """
        # Convert column letters to 0-based indices
        title_idx = _col_to_index(title_col)
        link_idx = _col_to_index(link_col)
        category_idx = _col_to_index(category_col)
        source_idx = _col_to_index(source_col)

        logger.info(
            "Connecting to spreadsheet %s, sheet '%s'",
            self.spreadsheet_id,
            self.sheet_name,
        )
        gc = gspread.service_account(filename=self.credentials_path)
        spreadsheet = gc.open_by_key(self.spreadsheet_id)
        worksheet = spreadsheet.worksheet(self.sheet_name)

        all_values = worksheet.get_all_values()
        logger.info("Fetched %d rows from spreadsheet", len(all_values))

        # Slice from start_row (convert 1-based to 0-based)
        data_rows = all_values[start_row - 1 :]

        articles: list[ArticleEntry] = []
        for row in data_rows:
            link = _safe_get(row, link_idx)
            title = _safe_get(row, title_idx)

            if not link or not title:
                continue

            articles.append(
                ArticleEntry(
                    title=title.strip(),
                    link=link.strip(),
                    category=_safe_get(row, category_idx).strip(),
                    source=_safe_get(row, source_idx).strip(),
                )
            )

        logger.info("Parsed %d article entries", len(articles))
        return articles


def _col_to_index(col_letter: str) -> int:
    """Convert a column letter (A, B, ..., Z) to a 0-based index."""
    return ord(col_letter.upper()) - ord("A")


def _safe_get(row: list[str], idx: int) -> str:
    """Safely get a value from a row by index, returning empty string if out of bounds."""
    if idx < len(row):
        return row[idx]
    return ""


if __name__ == "__main__":
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    with open("weekly_automation/config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    gs = config["google_sheets"]
    reader = SheetsReader(
        credentials_path=gs["credentials_path"],
        spreadsheet_id=gs["spreadsheet_id"],
        sheet_name=gs["sheet_name"],
    )
    entries = reader.read_articles(
        start_row=gs.get("start_row", 2),
        title_col=gs.get("title_column", "C"),
        link_col=gs.get("link_column", "E"),
        category_col=gs.get("category_column", "A"),
        source_col=gs.get("source_column", "D"),
    )

    for entry in entries:
        print(f"[{entry.category}] {entry.title} ({entry.source})")
        print(f"  {entry.link}")
