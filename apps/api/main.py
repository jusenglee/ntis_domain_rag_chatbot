"""FastAPI composition root for the NTIS RAG application."""

import uvicorn

from apps.api.app_factory import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8008, access_log=False)
