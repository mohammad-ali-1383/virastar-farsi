"""Shared fixtures for the backend test suite.

Provider traffic is always mocked: the correction service is replaced by a stub
for API tests, and the real provider adapters are exercised through
``httpx.MockTransport`` so no external call is ever made
(`testing.md`, `coding-rules.md` -> Testing).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.main import app
from app.services.correction import CorrectionResult, get_correction_service
from app.services.correction_log import CorrectionLogStore

GROQ_PROVIDER = "groq"
GEMINI_PROVIDER = "gemini"

Handler = Callable[[httpx.Request], httpx.Response]

# Placeholder credentials for the mocked provider calls. They are not real keys
# and never leave the process.
GROQ_TEST_KEY = "test-groq-key-not-real"
GEMINI_TEST_KEY = "test-gemini-key-not-real"

# URL fragments that identify each provider's endpoint, so a test can answer or
# inspect Groq and Gemini traffic independently.
GROQ_URL_MARKER = "api.groq.com"
GEMINI_URL_MARKER = "generativelanguage.googleapis.com"

PROVIDER_URL_MARKERS = (GROQ_URL_MARKER, GEMINI_URL_MARKER)


class StubCorrectionService:
    """Test double for :class:`~app.services.correction.CorrectionService`.

    Records the text it receives and either returns ``result`` or raises
    ``error``, which lets tests simulate both successful and failing providers.
    ``provider``/``model`` stand in for the adapter that produced the text, so a
    test can check what the evaluation log would record.
    """

    def __init__(
        self,
        result: str | None = None,
        error: Exception | None = None,
        provider: str = GROQ_PROVIDER,
        model: str = "stub-model",
    ) -> None:
        self.result = result
        self.error = error
        self.provider = provider
        self.model = model
        self.received: list[str] = []

    async def correct(self, text: str) -> CorrectionResult:
        self.received.append(text)

        if self.error is not None:
            raise self.error

        return CorrectionResult(
            text=self.result if self.result is not None else text,
            provider=self.provider,
            model=self.model,
        )


@pytest.fixture
def stub_service() -> StubCorrectionService:
    """Stub correction service injected into the app's dependencies."""
    return StubCorrectionService()


@pytest.fixture
def client(stub_service: StubCorrectionService) -> Iterator[TestClient]:
    """API client with the stub correction service wired in."""
    app.dependency_overrides[get_correction_service] = lambda: stub_service

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def tolerant_client(stub_service: StubCorrectionService) -> Iterator[TestClient]:
    """Client that returns error responses instead of re-raising server errors."""
    app.dependency_overrides[get_correction_service] = lambda: stub_service

    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


class ProviderTransport:
    """Mock provider transport that records the requests the adapters send.

    One default handler answers every request; ``respond_for`` installs a
    handler for the requests whose URL contains a given marker, which lets a
    test drive Groq and Gemini independently (needed by the fallback tests).
    The recorded ``requests`` list gives tests access to the URL, headers, and
    body each adapter actually produced.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._handler: Handler = lambda _: httpx.Response(200, json={})
        self._routes: list[tuple[str, Handler]] = []

    def respond(self, handler: Handler) -> None:
        """Install the handler that answers subsequent provider requests."""
        self._handler = handler

    def respond_for(self, url_marker: str, handler: Handler) -> None:
        """Answer only requests whose URL contains ``url_marker``.

        Markers are matched in the order they were added, so the default
        handler set by ``respond`` still answers everything else.
        """
        self._routes.append((url_marker, handler))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        url = str(request.url)
        for marker, handler in self._routes:
            if marker in url:
                return handler(request)

        return self._handler(request)

    def requests_for(self, url_marker: str) -> list[httpx.Request]:
        """The recorded requests sent to the endpoint matching ``url_marker``."""
        return [request for request in self.requests if url_marker in str(request.url)]

    @property
    def urls(self) -> list[str]:
        """The URLs of every recorded request, in order."""
        return [str(request.url) for request in self.requests]


@pytest.fixture
def provider_transport(monkeypatch: pytest.MonkeyPatch) -> ProviderTransport:
    """Route provider HTTP through a mock transport (no network, no real key).

    The adapters build their own ``httpx.AsyncClient`` when a request is made,
    so patching the client class is what lets the real adapter code — endpoint,
    headers, payload, timeout, and error mapping — run without external calls.
    """
    transport = ProviderTransport()
    real_client = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(transport.handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    return transport


@pytest.fixture
def provider_client(provider_transport: ProviderTransport) -> Iterator[TestClient]:
    """API client with the real provider wiring and mocked provider HTTP."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def correction_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the evaluation log at a temporary file (never the real logs/).

    Autouse, so *no* test can append to the deployment's configured
    ``logs/corrections.jsonl`` even if it forgets to ask for this fixture. The
    temporary path is returned so a test can read back the JSON Lines rows that
    were written; the file is not created until a consented write happens.
    """
    path = tmp_path / "logs" / "corrections.jsonl"
    monkeypatch.setattr(settings, "correction_log_path", path)

    return path


@pytest.fixture
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give both providers placeholder keys without touching any real secret."""
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(GROQ_TEST_KEY))
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(GEMINI_TEST_KEY))


# --- Shared provider response builders -------------------------------------- #
# One definition per provider payload shape, used by the service and fallback
# tests so the mocked wire format cannot drift between test modules.


def groq_body(text: str) -> dict[str, object]:
    """A successful Groq chat-completions payload carrying ``text``."""
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def gemini_body(text: str) -> dict[str, object]:
    """A successful Gemini ``generateContent`` payload carrying ``text``."""
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ]
    }


def groq_response(text: str) -> httpx.Response:
    """200 response with a Groq success body."""
    return httpx.Response(200, json=groq_body(text))


def gemini_response(text: str) -> httpx.Response:
    """200 response with a Gemini success body."""
    return httpx.Response(200, json=gemini_body(text))


def groq_error(status_code: int, *, message: str = "provider detail") -> httpx.Response:
    """Groq error response; ``message`` stands in for echoed provider detail."""
    return httpx.Response(
        status_code,
        json={"error": {"message": message, "type": "provider_error", "code": status_code}},
    )


def gemini_error(status_code: int, *, message: str = "provider detail") -> httpx.Response:
    """Gemini error response; ``message`` stands in for echoed provider detail."""
    return httpx.Response(
        status_code,
        json={"error": {"code": status_code, "message": message, "status": "UNAVAILABLE"}},
    )
