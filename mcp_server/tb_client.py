"""Synchronous JSON-RPC client for the TrenchBroom MCP bridge.

The bridge speaks newline-delimited JSON on 127.0.0.1:6161. One request per
line, one response per line, in order per connection.

This client is deliberately small: the MCP server is single-threaded per tool
call, so a blocking socket with a timeout is simpler and more predictable than
an async stack (and avoids nested-event-loop problems inside FastMCP).
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any


class TbError(RuntimeError):
    """A JSON-RPC error returned by the bridge."""

    def __init__(self, code: int, message: str, method: str = "") -> None:
        super().__init__(f"{method}: [{code}] {message}" if method else f"[{code}] {message}")
        self.code = code
        self.rpc_message = message
        self.method = method


class TrenchBroomClient:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.host = host or os.environ.get("TB_MCP_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("TB_MCP_PORT", "6161"))
        self.token = token if token is not None else os.environ.get("TB_MCP_TOKEN")
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._file = None
        self._id = 0

    # -- connection -----------------------------------------------------

    def connect(self) -> "TrenchBroomClient":
        if self._socket is not None:
            return self
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._file = sock.makefile("rwb")
        return self

    def close(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        finally:
            self._file = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def __enter__(self) -> "TrenchBroomClient":
        return self.connect()

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- calls ----------------------------------------------------------

    def call(self, method: str, params: dict | None = None) -> dict:
        self.connect()
        assert self._socket is not None and self._file is not None

        self._id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or {},
        }
        if self.token:
            request["auth"] = self.token

        try:
            self._file.write(json.dumps(request).encode("utf-8") + b"\n")
            self._file.flush()
            line = self._file.readline()
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as exc:
            self.close()
            raise ConnectionError(
                f"lost connection to TrenchBroom at {self.host}:{self.port}: {exc}"
            ) from exc

        if not line:
            self.close()
            raise ConnectionError(
                "TrenchBroom closed the connection (did the editor exit?)"
            )

        response = json.loads(line.decode("utf-8"))
        if "error" in response:
            error = response["error"]
            raise TbError(int(error.get("code", 0)), error.get("message", "unknown"), method)
        return response.get("result", {})

    # -- convenience ----------------------------------------------------

    def ping(self) -> dict:
        return self.call("rpc.ping")

    def is_alive(self) -> bool:
        try:
            self.ping()
            return True
        except Exception:
            return False


_default: TrenchBroomClient | None = None


def client() -> TrenchBroomClient:
    """Process-wide client, created lazily from environment variables."""
    global _default
    if _default is None:
        _default = TrenchBroomClient()
    return _default
