"""AgentCouncil Web UI — FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import create_api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Ensure sessions directory exists
    Path("sessions").mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="AgentCouncil Web UI",
    description="Web dashboard for multi-agent council runs",
    version="1.0.0",
    lifespan=lifespan,
)

# Register API routes
create_api_router(app)

# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard page."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("""
    <html>
        <head><title>AgentCouncil Web UI</title></head>
        <body>
            <h1>AgentCouncil Web UI</h1>
            <p>Dashboard not built yet. Run <code>uv sync --extra web</code> and build the frontend.</p>
        </body>
    </html>
    """)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


def main():
    """Entry point for running the web server."""
    import uvicorn

    uvicorn.run(
        "src.web.main:app",
        host="127.0.0.1",
        port=8080,
        reload=True,
    )


if __name__ == "__main__":
    main()
