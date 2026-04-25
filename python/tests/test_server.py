"""Tests for WebSocket server message routing and error handling."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

# We test ConnectionHandler in isolation — no real WebSocket needed.
from server import ConnectionHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> tuple[ConnectionHandler, AsyncMock]:
    """Create a ConnectionHandler with a mock WebSocket that captures sends."""
    ws = AsyncMock()
    ws.remote_address = ("127.0.0.1", 9999)

    # Collect all messages sent back
    sent: list[dict] = []

    async def _fake_send(raw: str) -> None:
        sent.append(json.loads(raw))

    ws.send = _fake_send
    handler = ConnectionHandler(ws)
    handler._sent = sent  # stash for assertions
    return handler, ws


# ---------------------------------------------------------------------------
# Ping / Pong
# ---------------------------------------------------------------------------


class TestPingPong:
    @pytest.mark.asyncio
    async def test_ping_returns_pong(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"type": "ping"}))
        assert len(handler._sent) == 1
        msg = handler._sent[0]
        assert msg["type"] == "pong"
        assert msg["status"] == "ok"
        assert "file_open" in msg

    @pytest.mark.asyncio
    async def test_ping_reports_no_file_open(self) -> None:
        handler, _ = _make_handler()
        with patch("server._shared_reader") as mock_reader:
            mock_reader.datatree = None
            await handler.handle_message(json.dumps({"type": "ping"}))
        assert handler._sent[0]["file_open"] is False


# ---------------------------------------------------------------------------
# Invalid messages
# ---------------------------------------------------------------------------


class TestInvalidMessages:
    @pytest.mark.asyncio
    async def test_invalid_json(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message("not json at all {{{")
        assert handler._sent[0]["type"] == "error"
        assert "Invalid JSON" in handler._sent[0]["message"]

    @pytest.mark.asyncio
    async def test_non_object_message(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps([1, 2, 3]))
        assert handler._sent[0]["type"] == "error"
        assert "JSON object" in handler._sent[0]["message"]

    @pytest.mark.asyncio
    async def test_missing_type_field(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"data": "hello"}))
        assert handler._sent[0]["type"] == "error"
        assert "type" in handler._sent[0]["message"]

    @pytest.mark.asyncio
    async def test_unknown_message_type(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"type": "foobar"}))
        assert handler._sent[0]["type"] == "error"
        assert "Unknown message type" in handler._sent[0]["message"]


# ---------------------------------------------------------------------------
# open_file routing
# ---------------------------------------------------------------------------


class TestOpenFileRouting:
    @pytest.mark.asyncio
    async def test_open_file_missing_path(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"type": "open_file"}))
        assert handler._sent[0]["type"] == "error"
        assert "missing" in handler._sent[0]["message"].lower()

    @pytest.mark.asyncio
    async def test_open_file_path_not_string(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"type": "open_file", "path": 123}))
        assert handler._sent[0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_open_file_nonexistent(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(
            json.dumps({"type": "open_file", "path": "/no/such/file.nc"})
        )
        # Should get progress then error
        errors = [m for m in handler._sent if m["type"] == "error"]
        assert len(errors) >= 1
        assert "not found" in errors[0]["message"].lower() or "No such file" in errors[0]["message"]


# ---------------------------------------------------------------------------
# render routing — validation only (no actual datashader)
# ---------------------------------------------------------------------------


class TestRenderRouting:
    @pytest.mark.asyncio
    async def test_render_missing_variable(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(json.dumps({"type": "render"}))
        assert handler._sent[0]["type"] == "error"
        assert "variable" in handler._sent[0]["message"].lower()


# ---------------------------------------------------------------------------
# export routing — validation
# ---------------------------------------------------------------------------


class TestExportRouting:
    @pytest.mark.asyncio
    async def test_export_no_file_open(self) -> None:
        handler, _ = _make_handler()
        with patch("server._shared_reader") as mock_reader:
            mock_reader.datatree = None
            await handler.handle_message(
                json.dumps({"type": "export", "format": "png"})
            )
        errors = [m for m in handler._sent if m["type"] == "error"]
        assert len(errors) >= 1
        assert "no file" in errors[0]["message"].lower()


# ---------------------------------------------------------------------------
# process routing — validation
# ---------------------------------------------------------------------------


class TestProcessRouting:
    @pytest.mark.asyncio
    async def test_process_invalid_pipeline(self) -> None:
        handler, _ = _make_handler()
        await handler.handle_message(
            json.dumps({"type": "process", "pipeline": "not_a_dict"})
        )
        assert handler._sent[0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_process_no_file_open(self) -> None:
        handler, _ = _make_handler()
        with patch("server._shared_reader") as mock_reader:
            mock_reader.datatree = None
            await handler.handle_message(
                json.dumps({"type": "process", "pipeline": {}})
            )
        errors = [m for m in handler._sent if m["type"] == "error"]
        assert len(errors) >= 1
