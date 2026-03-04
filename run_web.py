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
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
