"""FastAPI application setup for the weekly news dashboard."""

import os
import sys

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure the weekly_automation package is importable
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.routers import generation, history, upload  # noqa: E402

app = FastAPI(title="주간 뉴스 자동화 대시보드", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_config: dict = {}


def get_config() -> dict:
    return _config


@app.on_event("startup")
def startup():
    global _config
    config_path = os.path.join(BASE_DIR, "config.yaml")
    env_path = os.path.join(BASE_DIR, ".env")
    load_dotenv(env_path)

    with open(config_path, encoding="utf-8") as f:
        _config.update(yaml.safe_load(f))

    token = os.environ.get("DOORAY_API_TOKEN", "")
    _config.setdefault("dooray", {})["api_token"] = token


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(generation.router, prefix="/api/generate", tags=["generation"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_DIR = os.path.join(BASE_DIR, "static")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")


# ---------------------------------------------------------------------------
# HTML page routes
# ---------------------------------------------------------------------------

@app.get("/")
async def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/log")
async def log_page():
    return FileResponse(os.path.join(STATIC_DIR, "log.html"))


@app.get("/generate")
async def generate_page():
    return FileResponse(os.path.join(STATIC_DIR, "generate.html"))
