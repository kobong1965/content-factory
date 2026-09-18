"""Bounded incremental HTTP/SSE reads, independent of model output validation."""

from __future__ import annotations

import http.client
import json
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.error import HTTPError, URLError

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_PREFIX = "_content_factory_"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _TerminalEvents:
    """Stop only at SSE boundaries; the gateway must still validate success.

    Decode only complete events, including when UTF-8/CRLF crosses reads.
    JSON response bodies cannot be mistaken for SSE data.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.pending = b""
        self.data: list[bytes] = []

    def feed(self, chunk: bytes) -> bool:
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if line.startswith(b"data:"):
                self.data.append(line[5:].lstrip(b" "))
            elif not line:
                data = b"\n".join(self.data)
                self.data.clear()
                if data == b"[DONE]":
                    return True
                try:
                    event = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error") is not None or event.get("type") == "error":
                    return True
                if self.mode == "responses" and event.get("type") in {
                    "response.completed", "response.failed", "response.incomplete", "response.cancelled",
                }:
                    return True
        return False


@dataclass
class _Transfer:
    started: float = field(default_factory=time.monotonic)
    request_started_at: str = field(default_factory=_now)
    response_headers_at: str | None = None
    first_event_at: str | None = None
    last_event_at: str | None = None
    received_bytes: int = 0
    headers: dict[str, str] = field(default_factory=dict)

    def record(self, chunk: bytes) -> None:
        if chunk:
            self.first_event_at = self.first_event_at or _now()
            self.last_event_at = _now()
            self.received_bytes += len(chunk)

    def finish(self, exception: BaseException | None = None) -> dict[str, str]:
        stage = "waiting_headers" if self.response_headers_at is None else (
            "receiving_response" if self.received_bytes else "waiting_first_byte"
        )
        # Never retain exception messages or partial content: either may contain
        # credentials/prompts returned by an untrusted relay.
        cause = exception.reason if isinstance(exception, URLError) and isinstance(exception.reason, BaseException) else exception
        values = {
            "request_started_at": self.request_started_at,
            "response_headers_at": self.response_headers_at or "",
            "first_event_at": self.first_event_at or "",
            "last_event_at": self.last_event_at or "",
            "completed_at": _now(),
            "received_bytes": str(self.received_bytes),
            "elapsed_seconds": str(round(time.monotonic() - self.started, 3)),
            "transport_stage": stage,
            "transport_exception": type(cause).__name__ if cause else "",
        }
        self.headers.update({_PREFIX + key: value for key, value in values.items()})
        return self.headers


class TransportInterrupted(RuntimeError):
    def __init__(self, headers: Mapping[str, str], *, diagnostic_code: str) -> None:
        super().__init__(diagnostic_code)
        self.headers = dict(headers)
        self.diagnostic_code = diagnostic_code


def transport_diagnostics(headers: Mapping[str, str]) -> dict[str, Any]:
    """Allowlist local measurements only, never arbitrary HTTP headers."""
    result: dict[str, Any] = {}
    for name in (
        "request_started_at", "response_headers_at", "first_event_at", "last_event_at",
        "transport_stage", "transport_exception",
    ):
        result[name] = headers.get(_PREFIX + name) or None
    for name, conversion in (("received_bytes", int), ("elapsed_seconds", float)):
        result[name] = conversion(headers.get(_PREFIX + name) or 0)
    return result


def _remaining_timeout(response: Any, seconds: float) -> None:
    # urllib HTTP/HTTPS: HTTPResponse -> buffered SocketIO -> socket.
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(seconds)


def read_transport(opener: Any, request: Any, *, mode: str, timeout_seconds: float) -> tuple[str, bytes, dict[str, str]]:
    transfer = _Transfer()
    # Preserve the no-byte timeout (normally 180 s); bound a heartbeat-only
    # stream at 3x that budget, never longer than 15 minutes.
    total_budget = min(900.0, max(0.01, float(timeout_seconds)) * 3)
    deadline = transfer.started + total_budget
    try:
        with opener.open(request, timeout=min(timeout_seconds, total_budget)) as response:
            transfer.response_headers_at = _now()
            transfer.headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            transfer.headers[_PREFIX + "http_status"] = str(getattr(response, "status", 200))
            content_type = response.headers.get_content_type()
            terminal = _TerminalEvents(mode)
            chunks: list[bytes] = []
            # read1 makes at most one underlying read. read(64 KiB) can wait for
            # a full buffer even though small SSE events have already arrived.
            read = getattr(response, "read1", response.read)
            while transfer.received_bytes <= MAX_RESPONSE_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("total response deadline")
                _remaining_timeout(response, max(0.001, min(timeout_seconds, remaining)))
                chunk = read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - transfer.received_bytes))
                if not chunk:
                    # HTTPResponse.read1 does not itself reject a short
                    # Content-Length body, unlike read(). Preserve that guard.
                    outstanding = getattr(response, "length", None)
                    if isinstance(outstanding, int) and outstanding > 0:
                        raise http.client.IncompleteRead(b"", outstanding)
                    break
                transfer.record(chunk)
                chunks.append(chunk)
                if transfer.received_bytes > MAX_RESPONSE_BYTES or terminal.feed(chunk):
                    break
            return content_type, b"".join(chunks), transfer.finish()
    except HTTPError:
        # Explicit schema/path compatibility remains at the protocol layer.
        raise
    except (OSError, http.client.HTTPException) as exc:
        if isinstance(exc, http.client.IncompleteRead):
            transfer.record(exc.partial)
        code = "secure_connection_interrupted" if isinstance(exc, ssl.SSLError) else (
            "connection_interrupted" if isinstance(exc, http.client.HTTPException) else "connection_failed"
        )
        raise TransportInterrupted(transfer.finish(exc), diagnostic_code=code) from exc
