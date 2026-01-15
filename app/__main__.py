"""Entry point so `python -m app` starts the app (recommended).

This mirrors running `uvicorn app.main:app` so it's convenient for developers.
"""

from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
