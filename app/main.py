from pathlib import Path
import sys

# Ensure project root is on sys.path so `python app/main.py` can be run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.auth.router import router as auth_router
from app.core.logging import get_logger

log = get_logger("main")

app = FastAPI(
    title="SentinelOps API",
    version="0.1.0",
)

# Enable CORS for local frontend during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

@app.on_event("startup")
def startup():
    log.info("🚀 SentinelOps API started")

if __name__ == "__main__":
    # Allow running `python app/main.py` directly. For development, prefer `uvicorn app.main:app --reload`.
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
