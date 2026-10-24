"""Optional, consent-based evaluation log for corrections.

When — and only when — a request explicitly asks for it, the backend appends one
JSON Lines record per successful correction to a dedicated file
(``CORRECTION_LOG_PATH``, default ``<repo root>/logs/corrections.jsonl``). The
record holds the timestamp, the original text, the **provider and model that
actually produced the correction** (which may be the Gemini fallback), and the
final corrected text, so the quality of the prompt and of each provider can be
evaluated later.

Privacy rules (`coding-rules.md` -> Security):

- Nothing is written unless the request opted in; a declined request leaves no
  evaluation record at all.
- The store never logs the user's text, API keys, or authorization headers. A
  failed write is reported with the exception class name and the configured path
  only.
- A failed write never fails the request and never claims success: ``append``
  returns ``False`` and the endpoint still returns the corrected text, because
  the user's correction did succeed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def record_line(
    *,
    timestamp: datetime,
    original_text: str,
    provider: str,
    model: str,
    corrected_text: str,
) -> str:
    """Serialize one evaluation record as a single JSON Lines row.

    ``ensure_ascii=False`` keeps Persian readable, and the timestamp is written
    as an ISO 8601 string carrying its UTC offset.
    """
    record = {
        "timestamp": timestamp.isoformat(),
        "original_text": original_text,
        "provider": provider,
        "model": model,
        "corrected_text": corrected_text,
    }
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


class CorrectionLogStore:
    """Append-only JSON Lines store for consented corrections.

    The file and its directory are created on the first write, so an
    unconsented or never-used deployment leaves nothing on disk.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Where records are written (configuration, never user input)."""
        return self._path

    async def append(
        self,
        *,
        original_text: str,
        provider: str,
        model: str,
        corrected_text: str,
    ) -> bool:
        """Append one record; return ``True`` only when it reached the file.

        The blocking file work runs in a worker thread so a slow disk cannot
        stall the event loop. A write failure is logged without the text and
        reported as ``False`` rather than raised, so the correction the user
        asked for is still returned.
        """
        line = record_line(
            timestamp=datetime.now(timezone.utc),
            original_text=original_text,
            provider=provider,
            model=model,
            corrected_text=corrected_text,
        )

        try:
            await asyncio.to_thread(self._append_line, line)
        except OSError as error:
            # Exception class and configured path only: the record (and with it
            # the user's text) never reaches the application log.
            logger.warning(
                "Could not append the consented evaluation record (%s) to %s; "
                "returning the correction without it",
                type(error).__name__,
                self._path,
            )
            return False

        return True

    def _append_line(self, line: str) -> None:
        """Create the directory if needed and append one complete line."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
