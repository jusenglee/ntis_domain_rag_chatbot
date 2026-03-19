"""FastAPI composition root for the NTIS RAG application."""

from apps.api.app_factory import create_app


app = create_app()
