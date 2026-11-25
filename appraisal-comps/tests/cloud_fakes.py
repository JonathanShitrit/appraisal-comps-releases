"""Fakes for the cloud-read tests: no network, no key, no Chrome."""
from __future__ import annotations

import json
from pathlib import Path

FAST_TIMING = {"poll_seconds": 0.01, "run_timeout_s": 2, "retry_after_cap_s": 0.05, "capture_timeout_s": 2}


def write_replies(folder: Path, replies: dict[str, list[dict[str, object]]],
                  timing: dict[str, float] | None = None) -> Path:
    """Write <folder>/replies.json for the replay transport. Returns the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    document = {"timing": timing if timing is not None else FAST_TIMING, "replies": replies}
    (folder / "replies.json").write_text(json.dumps(document), encoding="utf-8")
    return folder


def sent_requests(folder: Path) -> list[dict[str, object]]:
    """Every request the replay transport recorded, in order."""
    sent_file = folder / "sent.jsonl"
    if not sent_file.exists():
        return []
    return [json.loads(line) for line in sent_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def happy_run(run_id: str, output: object, cdp_url: str, cost: str = "0.04") -> dict[str, list[dict[str, object]]]:
    """The replies for one completed run. The caller adds POST /runs."""
    session_id = f"sess-{run_id}"
    workspace_id = f"ws-{run_id}"
    browser_id = f"br-{run_id}"
    browser_item = {"id": browser_id, "cdpUrl": cdp_url if cdp_url != "" else None}
    run_body = {"status": "completed", "output": output, "error": None, "totalInputTokens": 1200,
                "totalOutputTokens": 300, "totalCostUsd": cost, "sessionId": session_id,
                "workspaceId": workspace_id}
    return {
        f"GET /runs/{run_id}/status": [{"status": 200, "body": {"status": "running"}},
                                       {"status": 200, "body": {"status": "completed"}}],
        f"POST /runs/{run_id}/cancel": [{"status": 200, "body": {}}],
        f"GET /runs/{run_id}": [{"status": 200, "body": run_body}],
        f"GET /browsers?agentSessionId={session_id}&filterBy=active":
            [{"status": 200, "body": {"items": [browser_item]}}],
        f"PATCH /browsers/{browser_id}": [{"status": 200, "body": {}}],
        f"DELETE /sessions/{session_id}": [{"status": 204}],
        f"DELETE /workspaces/{workspace_id}": [{"status": 200, "body": {}}],
    }


# -- a loopback CDP server ---------------------------------------------------------------

import base64
import hashlib
import socket
import struct
import threading
from dataclasses import dataclass

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class FakePage:
    target_id: str
    url: str
    text: str
    pdf: bytes
    type: str = "page"


class FakeCdpServer:
    """A loopback WebSocket server that speaks enough CDP for cloud_capture."""

    def __init__(self, pages: list[FakePage], fragment_bytes: int = 0,
                 ping_before_reply: bool = False, close_on_method: str = "") -> None:
        self.pages = pages
        self.fragment_bytes = fragment_bytes
        self.ping_before_reply = ping_before_reply
        self.close_on_method = close_on_method
        self.calls: list[dict[str, object]] = []
        self.pongs = 0
        self.url = ""
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._connections: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stopping = False

    def start(self) -> None:
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        port = self._listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/devtools/browser/fake"
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()
        self._threads.append(accept_thread)

    def stop(self) -> None:
        self._stopping = True
        try:
            self._listener.close()
        except OSError:
            pass
        for connection in self._connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        for thread in self._threads:
            thread.join(timeout=2)

    # -- plumbing ----------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping:
            try:
                connection, _address = self._listener.accept()
            except OSError:
                return
            self._connections.append(connection)
            thread = threading.Thread(target=self._serve, args=(connection,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _serve(self, connection: socket.socket) -> None:
        try:
            self._handshake(connection)
            attached: dict[str, FakePage] = {}
            while True:
                message = self._read_client_message(connection)
                self.calls.append(message)
                self._answer(connection, message, attached)
        except (OSError, ValueError, EOFError):
            pass
        finally:
            connection.close()

    def _handshake(self, connection: socket.socket) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = connection.recv(4096)
            if chunk == b"":
                raise EOFError
            request += chunk
        key = ""
        for line in request.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        connection.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))

    def _read_exact(self, connection: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if chunk == b"":
                raise EOFError
            data += chunk
        return data

    def _read_client_message(self, connection: socket.socket) -> dict[str, object]:
        while True:
            first, second = self._read_exact(connection, 2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(connection, 2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(connection, 8))[0]
            mask = self._read_exact(connection, 4)
            payload = bytes(byte ^ mask[position % 4] for position, byte in enumerate(self._read_exact(connection, length)))
            if opcode == 10:
                self.pongs += 1
                continue
            if opcode == 8:
                raise EOFError
            return json.loads(payload.decode("utf-8"))

    def _send_frame(self, connection: socket.socket, opcode: int, payload: bytes, final: bool = True) -> None:
        length = len(payload)
        first = (0x80 if final else 0) | opcode
        if length < 126:
            header = bytes([first, length])
        elif length < 65536:
            header = bytes([first, 126]) + struct.pack(">H", length)
        else:
            header = bytes([first, 127]) + struct.pack(">Q", length)
        connection.sendall(header + payload)

    def _send_json(self, connection: socket.socket, document: dict[str, object]) -> None:
        payload = json.dumps(document).encode("utf-8")
        if self.ping_before_reply:
            self._send_frame(connection, 9, b"ping")
        if self.fragment_bytes <= 0 or len(payload) <= self.fragment_bytes:
            self._send_frame(connection, 1, payload)
            return
        position = 0
        opcode = 1
        while position < len(payload):
            chunk = payload[position:position + self.fragment_bytes]
            position += self.fragment_bytes
            self._send_frame(connection, opcode, chunk, final=position >= len(payload))
            opcode = 0

    # -- the CDP methods ---------------------------------------------------------------

    def _answer(self, connection: socket.socket, message: dict[str, object], attached: dict[str, FakePage]) -> None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        session_id = message.get("sessionId")
        if method == self.close_on_method and method != "":
            raise EOFError
        page = attached.get(session_id) if isinstance(session_id, str) else None
        result: dict[str, object] = {}
        event_after: dict[str, object] | None = None
        if method == "Target.getTargets":
            result = {"targetInfos": [{"targetId": item.target_id, "type": item.type, "url": item.url}
                                      for item in self.pages]}
        elif method == "Target.attachToTarget":
            wanted = [item for item in self.pages if item.target_id == params.get("targetId")]
            if not wanted:
                self._send_error(connection, message_id, "no such target")
                return
            new_session = f"s-{wanted[0].target_id}"
            attached[new_session] = wanted[0]
            result = {"sessionId": new_session}
        elif method == "Runtime.evaluate" and page is not None:
            expression = params.get("expression")
            if expression == "location.href":
                result = {"result": {"type": "string", "value": page.url}}
            elif expression == "document.body.innerText":
                result = {"result": {"type": "string", "value": page.text}}
            else:
                self._send_error(connection, message_id, "unknown expression")
                return
        elif method == "Page.printToPDF" and page is not None:
            result = {"data": base64.b64encode(page.pdf).decode("ascii")}
        elif method == "Page.enable":
            result = {}
        elif method == "Page.navigate" and page is not None:
            new_url = params.get("url")
            for other in self.pages:
                if other is not page and other.url == new_url:
                    page.text = other.text
                    page.pdf = other.pdf
            if isinstance(new_url, str):
                page.url = new_url
            result = {"frameId": "frame-1"}
            event_after = {"method": "Page.loadEventFired", "params": {}, "sessionId": session_id}
        else:
            self._send_error(connection, message_id, f"unknown method {method}")
            return
        self._send_json(connection, {"id": message_id, "result": result})
        if event_after is not None:
            self._send_json(connection, event_after)

    def _send_error(self, connection: socket.socket, message_id: object, text: str) -> None:
        self._send_json(connection, {"id": message_id, "error": {"code": -32601, "message": text}})
