"""FastAPI application entry point.

Phase 2 exposes the health endpoint and the public correction API
(``POST /api/v1/correct``). The correction service behind that endpoint is a
placeholder until the LLM adapter is integrated in Phase 3.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.config import settings
from app.errors import register_exception_handlers
from app.routers.correction import router as correction_router

app = FastAPI(
    title="Persian Writing Assistant API",
    description="Backend API for the Persian proofreading service.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

register_exception_handlers(app)

app.include_router(correction_router)


def _openapi_schema() -> dict[str, Any]:
    """Generate the document without FastAPI's unused ``422`` response.

    Validation errors are mapped to ``400 INVALID_INPUT`` by the exception
    handlers (`errors.py`), so FastAPI's default ``422 Validation Error`` entry
    (and its schemas) would document a response this API never sends.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    responses.pop("422", None)

    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    app.openapi_schema = schema
    return schema


app.openapi = _openapi_schema


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness probe used by Docker Compose and local checks."""
    return {"status": "ok"}
