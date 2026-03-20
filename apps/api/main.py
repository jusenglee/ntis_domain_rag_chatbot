"""FastAPI composition root for the NTIS RAG application."""

from apps.api.app_factory import create_app
import uvicorn

app = create_app()
uvicorn.run(app, host="0.0.0.0", port=8008, access_log=False)