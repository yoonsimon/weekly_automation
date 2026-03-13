"""Launch the weekly news dashboard web server."""

import os
import sys

# Ensure weekly_automation is the working directory so relative imports work
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("RENDER") is None  # 로컬에서만 reload
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
    )
