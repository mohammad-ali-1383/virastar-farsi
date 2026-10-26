"""Shared fixtures for the backend test suite.

Provider traffic is always mocked: the correction service is replaced by a stub
for API tests, and the real provider adapter is exercised through
``httpx.MockTransport`` so no external call is ever made
(`testing.md`, `coding-rules.md` -> Testing).

The provider layer is configured with numbered slots (`API_KEY_N`, `BASE_URL_N`,
`MODEL_N`, ...). The ``provider_slots`` fixture defines two complete slots with
placeholder keys and unsets every slot variable that the machine might carry, so
the tests describe their own provider configuration instead of inheriting one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.config as app_config
from app.config import settings
from app.main import app
from app.services.correction import CorrectionResult, get_correction_service

Handler = Callable[[httpx.Request], httpx.Response]

# Placeholder credentials for the mocked provider calls. They are not real keys
# and never leave the process.
SLOT_1_KEY = "test-slot-1-key-not-real"
SLOT_2_KEY = "test-slot-2-key-not-real"

SLOT_1_BASE_URL = "https://slot-one.test/v1"
SLOT_2_BASE_URL = "https://slot-two.test/v1"

SLOT_1_MODEL = "stub-slot-1-model"
SLOT_2_MODEL = "stub-slot-2-model"

# URL fragments that identify each slot's endpoint, so a test can answer or
# inspect the traffic of one slot independently.
SLOT_1_URL_MARKER = "slot-one.test"
SLOT_2_URL_MARKER = "slot-two.test"

# Every variable that belongs to a provider-slot group, whatever its index.
_SLOT_VARIABLE_PATTERN = re.compile(
    r"^(API_KEY|BASE_URL|MODEL|TIMEOUT_SECONDS|EXTRA_BODY|NAME)_\d+$"
)


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
        provider: str = "slot-1",
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
    """Mock provider transport that records the requests the adapter sends.

    One default handler answers every request; ``respond_for`` installs a
    handler for the requests whose URL contains a given marker, which lets a
    test drive each slot independently (needed by the fallback tests). The
    recorded ``requests`` list gives tests access to the URL, headers, and body
    the adapter actually produced.
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

    The adapter builds its own ``httpx.AsyncClient`` when a request is made, so
    patching the client class is what lets the real adapter code — endpoint,
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


def define_slot(
    monkeypatch: pytest.MonkeyPatch,
    index: int,
    *,
    base_url: str,
    api_key: str | None = None,
    model: str | None = None,
    timeout_seconds: str | None = None,
    extra_body: str | None = None,
    name: str | None = None,
) -> None:
    """Declare one provider slot through the process environment."""
    key = api_key if api_key is not None else f"test-slot-{index}-key-not-real"
    monkeypatch.setenv(f"API_KEY_{index}", key)
    monkeypatch.setenv(f"BASE_URL_{index}", base_url)
    monkeypatch.setenv(f"MODEL_{index}", model if model is not None else f"stub-slot-{index}-model")

    for variable, value in (
        ("TIMEOUT_SECONDS", timeout_seconds),
        ("EXTRA_BODY", extra_body),
        ("NAME", name),
    ):
        if value is not None:
            monkeypatch.setenv(f"{variable}_{index}", value)


@pytest.fixture
def provider_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two complete slots with placeholder keys, and no other slot anywhere.

    The real `.env` files are ignored for the duration of the test, so the suite
    never depends on (or reveals) the deployment's configuration.
    """
    monkeypatch.setattr(app_config, "_ENV_FILES", ())

    for key in list(os.environ):
        if _SLOT_VARIABLE_PATTERN.match(key.upper()):
            monkeypatch.delenv(key, raising=False)

    define_slot(
        monkeypatch,
        1,
        base_url=SLOT_1_BASE_URL,
        api_key=SLOT_1_KEY,
        model=SLOT_1_MODEL,
    )
    define_slot(
        monkeypatch,
        2,
        base_url=SLOT_2_BASE_URL,
        api_key=SLOT_2_KEY,
        model=SLOT_2_MODEL,
    )


@pytest.fixture(autouse=True)
def reset_slot_cooldowns() -> Iterator[None]:
    """Start every test with no slot cooling down (the store is per process)."""
    from app.services import correction as correction_module

    correction_module._cooldowns.reset()

    yield

    correction_module._cooldowns.reset()


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


# --- Shared provider response builders -------------------------------------- #
# One definition of the OpenAI-compatible wire format, used by the adapter,
# service, and fallback tests so the mocked shape cannot drift between modules.


def chat_completion_body(text: str) -> dict[str, object]:
    """A successful chat-completions payload carrying ``text``."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def chat_completion_response(text: str) -> httpx.Response:
    """200 response with a chat-completions success body."""
    return httpx.Response(200, json=chat_completion_body(text))


def chat_completion_error(
    status_code: int, *, message: str = "provider detail"
) -> httpx.Response:
    """Error response; ``message`` stands in for echoed provider detail."""
    return httpx.Response(
        status_code,
        json={
            "error": {
                "message": message,
                "type": "provider_error",
                "code": status_code,
            }
        },
    )
