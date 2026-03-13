"""Synchronous Dooray Wiki REST API client using requests."""

import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dooray.com"


class DoorayApiError(Exception):
    """Error raised when a Dooray API call fails."""

    def __init__(self, message: str, status_code: int = 0, result_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.result_code = result_code

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"status_code={self.status_code}")
        if self.result_code is not None:
            parts.append(f"result_code={self.result_code}")
        return " | ".join(parts)


class DoorayWikiClient:
    """Synchronous client for the Dooray Wiki REST API."""

    def __init__(self, api_token: str, wiki_id: str):
        self.wiki_id = wiki_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"dooray-api {api_token}",
            "Content-Type": "application/json",
        })
        logger.info("DoorayWikiClient initialized for wiki_id=%s", wiki_id)

    def _check_response(self, resp: requests.Response) -> dict:
        """Parse the API envelope and raise DoorayApiError if not successful.

        Returns the parsed JSON body on success.
        """
        try:
            data = resp.json()
        except ValueError:
            raise DoorayApiError(
                message=f"Invalid JSON response: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        header = data.get("header", {})
        is_successful = header.get("isSuccessful", False)

        if not is_successful:
            result_code = header.get("resultCode")
            result_message = header.get("resultMessage") or "Unknown error"
            raise DoorayApiError(
                message=result_message,
                status_code=resp.status_code,
                result_code=result_code,
            )

        return data

    # ------------------------------------------------------------------
    # Wiki list
    # ------------------------------------------------------------------

    def get_wiki_list(self, page: int = 0, size: int = 20) -> list[dict]:
        """GET /wiki/v1/wikis - Retrieve the list of wikis."""
        url = f"{BASE_URL}/wiki/v1/wikis"
        params = {"page": page, "size": size}
        logger.debug("GET %s params=%s", url, params)

        resp = self.session.get(url, params=params)
        data = self._check_response(resp)
        return data.get("result", [])

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def get_child_pages(self, parent_page_id: str | None = None) -> list[dict]:
        """GET /wiki/v1/wikis/{wiki_id}/pages - List child pages."""
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages"
        params = {}
        if parent_page_id is not None:
            params["parentPageId"] = parent_page_id
        logger.debug("GET %s params=%s", url, params)

        resp = self.session.get(url, params=params)
        data = self._check_response(resp)
        return data.get("result", [])

    def create_page(self, parent_page_id: str, subject: str, content: str) -> dict:
        """POST /wiki/v1/wikis/{wiki_id}/pages - Create a new wiki page."""
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages"
        body = {
            "parentPageId": parent_page_id,
            "subject": subject,
            "body": {
                "mimeType": "text/x-markdown",
                "content": content,
            },
            "attachFileIds": [],
            "referrers": [],
        }
        logger.debug("POST %s subject=%s", url, subject)

        resp = self.session.post(url, json=body)
        data = self._check_response(resp)
        return data.get("result", {})

    def get_page_content(self, page_id: str) -> dict:
        """GET /wiki/v1/wikis/{wiki_id}/pages/{page_id} - Retrieve page details."""
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages/{page_id}"
        logger.debug("GET %s", url)

        resp = self.session.get(url)
        data = self._check_response(resp)
        return data.get("result", {})

    def modify_page_content(self, page_id: str, content: str) -> None:
        """PUT /wiki/v1/wikis/{wiki_id}/pages/{page_id}/content - Update page body."""
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages/{page_id}/content"
        body = {
            "body": {
                "mimeType": "text/x-markdown",
                "content": content,
            },
        }
        logger.debug("PUT %s length=%d", url, len(content))

        resp = self.session.put(url, json=body)
        self._check_response(resp)
        logger.info("Page %s content updated successfully", page_id)

    def delete_page(self, page_id: str) -> None:
        """DELETE /wiki/v1/wikis/{wiki_id}/pages/{page_id} - Delete a wiki page."""
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages/{page_id}"
        logger.debug("DELETE %s", url)

        resp = self.session.delete(url)
        self._check_response(resp)
        logger.info("Page %s deleted successfully", page_id)

    # ------------------------------------------------------------------
    # File upload (redirect-based)
    # ------------------------------------------------------------------

    def upload_file(self, page_id: str, file_path: str) -> str:
        """Upload a file to a wiki page via redirect-based flow.

        1. POST to the files endpoint with allow_redirects=False.
        2. Follow the redirect Location header.
        3. POST multipart form data to the redirect URL.

        Returns:
            The ``pageFileId`` for markdown embedding: ``![](/page-files/{pageFileId})``
        """
        file_path_obj = Path(file_path)
        if not file_path_obj.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Step 1: Initiate upload - get redirect URL
        url = f"{BASE_URL}/wiki/v1/wikis/{self.wiki_id}/pages/{page_id}/files"
        logger.debug("POST %s (redirect initiation)", url)

        resp = self.session.post(url, allow_redirects=False)

        if resp.status_code not in (301, 302, 303, 307, 308):
            raise DoorayApiError(
                message=f"Expected redirect, got status {resp.status_code}",
                status_code=resp.status_code,
            )

        redirect_url = resp.headers.get("Location")
        if not redirect_url:
            raise DoorayApiError(
                message="Redirect response missing Location header",
                status_code=resp.status_code,
            )
        logger.debug("Redirect URL: %s", redirect_url)

        # Step 2: Upload file to redirect URL (multipart, no JSON content-type)
        filename = file_path_obj.name
        import mimetypes
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        with open(file_path_obj, "rb") as f:
            files = {"file": (filename, f, mime_type)}
            data = {"type": "general"}

            # Use a separate request without the JSON Content-Type header
            upload_headers = {
                k: v for k, v in self.session.headers.items()
                if k.lower() != "content-type"
            }
            logger.debug("POST %s (multipart upload: %s)", redirect_url, filename)

            upload_resp = requests.post(
                redirect_url,
                headers=upload_headers,
                files=files,
                data=data,
            )

        upload_data = self._check_response(upload_resp)
        result = upload_data.get("result", {})
        page_file_id = result.get("pageFileId", "")
        logger.info("File uploaded: pageFileId=%s", page_file_id)
        return page_file_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")

    token = os.environ.get("DOORAY_API_TOKEN", "")
    wiki_id = os.environ.get("DOORAY_WIKI_ID", "")

    if not token or not wiki_id:
        logger.error("Set DOORAY_API_TOKEN and DOORAY_WIKI_ID environment variables")
        raise SystemExit(1)

    client = DoorayWikiClient(api_token=token, wiki_id=wiki_id)

    wikis = client.get_wiki_list()
    for wiki in wikis:
        logger.info("Wiki: %s", wiki)
