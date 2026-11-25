#!/usr/bin/env python3
"""
cdp_client.py - a small WebSocket client for the Chrome DevTools Protocol (CDP).

cloud_capture.py uses it to save a page from a browser-use Cloud browser:
the address, the text and a PDF. Standard library only (socket, ssl, struct).
It speaks just enough WebSocket for that: text messages, continuation frames,
ping and pong, close. Only wss:// is accepted (ws:// only in tests).

Not a command: cloud_capture.py imports it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_CONTINUATION = 0
OPCODE_TEXT = 1
OPCODE_BINARY = 2
OPCODE_CLOSE = 8
OPCODE_PING = 9
OPCODE_PONG = 10
READ_CHUNK_BYTES = 1024 * 1024


class CdpError(Exception):
    """Anything that stops a CDP call: a bad address, a closed socket, a timeout, an error reply."""


class CdpConnection:
    """One CDP connection. Use as a context manager."""

    def __init__(self, websocket_url: str, timeout_s: float, allow_plain: bool = False) -> None:
        parts = urllib.parse.urlsplit(websocket_url)
        allowed_schemes = ["wss"]
        if allow_plain:
            allowed_schemes.append("ws")
        if parts.scheme not in allowed_schemes or not parts.hostname:
            raise CdpError("cdp-url-scheme")
        self._timeout_s = timeout_s
        self._next_id = 1
        self._pending_events: list[dict[str, object]] = []
        self._socket: socket.socket | None = None
        port = parts.port
        if port is None:
            port = 443 if parts.scheme == "wss" else 80
        resource = parts.path or "/"
        if parts.query:
            resource += "?" + parts.query
        try:
            raw_socket = socket.create_connection((parts.hostname, port), timeout=timeout_s)
            if parts.scheme == "wss":
                raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parts.hostname)
        except (OSError, ssl.SSLError) as error:
            raise CdpError(f"cannot connect: {error.__class__.__name__}") from None
        self._socket = raw_socket
        try:
            self._handshake(parts.netloc, resource)
        except CdpError:
            self.close()
            raise

    def __enter__(self) -> CdpConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- the WebSocket layer -------------------------------------------------------

    def _handshake(self, host_header: str, resource: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {resource} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        deadline = time.monotonic() + self._timeout_s
        self._send_bytes(request.encode("ascii"))
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += self._read_exact(1, deadline)
            if len(reply) > 16 * 1024:
                raise CdpError("handshake reply too long")
        lines = reply.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise CdpError("handshake refused")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise CdpError("handshake accept key is wrong")

    def _send_bytes(self, data: bytes) -> None:
        if self._socket is None:
            raise CdpError("closed")
        try:
            self._socket.settimeout(self._timeout_s)
            self._socket.sendall(data)
        except (OSError, ssl.SSLError):
            raise CdpError("closed") from None

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[position % 4] for position, byte in enumerate(payload))
        self._send_bytes(bytes(header) + masked)

    def _read_exact(self, size: int, deadline: float) -> bytes:
        if self._socket is None:
            raise CdpError("closed")
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError("timeout")
            try:
                self._socket.settimeout(remaining)
                chunk = self._socket.recv(min(size - len(data), READ_CHUNK_BYTES))
            except socket.timeout:
                raise CdpError("timeout") from None
            except (OSError, ssl.SSLError):
                raise CdpError("closed") from None
            if chunk == b"":
                raise CdpError("closed")
            data += chunk
        return bytes(data)

    def _read_message(self, deadline: float) -> str:
        """The next complete text message, answering pings on the way."""
        message = bytearray()
        while True:
            first, second = self._read_exact(2, deadline)
            is_final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2, deadline))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8, deadline))[0]
            mask = b""
            if second & 0x80:
                mask = self._read_exact(4, deadline)
            if len(message) + length > MAX_MESSAGE_BYTES:
                raise CdpError("message too large")
            payload = self._read_exact(length, deadline)
            if mask:
                payload = bytes(byte ^ mask[position % 4] for position, byte in enumerate(payload))
            if opcode == OPCODE_CLOSE:
                raise CdpError("closed")
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode in (OPCODE_TEXT, OPCODE_BINARY, OPCODE_CONTINUATION):
                message += payload
                if is_final:
                    try:
                        return message.decode("utf-8")
                    except UnicodeDecodeError:
                        raise CdpError("message is not text") from None
            else:
                raise CdpError("unknown frame")

    def _read_json(self, deadline: float) -> dict[str, object]:
        text = self._read_message(deadline)
        try:
            message = json.loads(text)
        except ValueError:
            raise CdpError("message is not JSON") from None
        if not isinstance(message, dict):
            raise CdpError("message is not an object")
        return message

    # -- CDP -----------------------------------------------------------------------

    def call(self, method: str, params: dict[str, object], session_id: str = "") -> dict[str, object]:
        """Send one command and return its result object. Events that arrive meanwhile are kept."""
        message_id = self._next_id
        self._next_id += 1
        command: dict[str, object] = {"id": message_id, "method": method, "params": params}
        if session_id != "":
            command["sessionId"] = session_id
        self._send_frame(OPCODE_TEXT, json.dumps(command).encode("utf-8"))
        deadline = time.monotonic() + self._timeout_s
        while True:
            message = self._read_json(deadline)
            if "id" not in message:
                self._pending_events.append(message)
                continue
            if message["id"] != message_id:
                continue
            error = message.get("error")
            if error is not None:
                detail = error.get("message") if isinstance(error, dict) else None
                raise CdpError(str(detail) if detail else f"{method} failed")
            result = message.get("result")
            if isinstance(result, dict):
                return result
            return {}

    def wait_for_event(self, method: str, session_id: str, timeout_s: float) -> dict[str, object]:
        """The params of the next event named method for session_id."""
        for position, event in enumerate(self._pending_events):
            if event.get("method") == method and event.get("sessionId", "") == session_id:
                del self._pending_events[position]
                return _event_params(event)
        deadline = time.monotonic() + timeout_s
        while True:
            message = self._read_json(deadline)
            if "id" in message:
                continue
            if message.get("method") == method and message.get("sessionId", "") == session_id:
                return _event_params(message)
            self._pending_events.append(message)

    def close(self) -> None:
        if self._socket is None:
            return
        try:
            self._send_frame(OPCODE_CLOSE, b"")
        except CdpError:
            pass
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None


def _event_params(event: dict[str, object]) -> dict[str, object]:
    params = event.get("params")
    if isinstance(params, dict):
        return params
    return {}
