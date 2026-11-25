#!/usr/bin/env python3
"""
cloud_capture.py - save a page from a browser-use Cloud browser, with code.

Connects to the run's browser over CDP and saves the page's address, its
text and a PDF. The saved text is what quote_check.py later checks the
reader's quotes against, so the model never writes the evidence.

Not a command: cloud_read.py imports it.
"""
from __future__ import annotations

import base64
import binascii
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_client import CdpConnection, CdpError
from sources import host_of


@dataclass
class PageCapture:
    url: str
    text: str
    pdf: bytes


def _evaluate_text(connection: CdpConnection, expression: str, session_id: str) -> str:
    result = connection.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session_id)
    inner = result.get("result")
    if not isinstance(inner, dict) or not isinstance(inner.get("value"), str):
        raise CdpError(f"{expression} did not give text")
    return inner["value"]


def _choose_target(connection: CdpConnection, wanted_url: str, host_allowed: Callable[[str], bool]) -> str:
    result = connection.call("Target.getTargets", {})
    target_infos = result.get("targetInfos")
    if not isinstance(target_infos, list):
        raise CdpError("no-page-target")
    pages: list[tuple[str, str]] = []
    for info in target_infos:
        if not isinstance(info, dict) or info.get("type") != "page":
            continue
        target_id = info.get("targetId")
        url = info.get("url")
        if isinstance(target_id, str) and isinstance(url, str):
            pages.append((target_id, url))
    if not pages:
        raise CdpError("no-page-target")
    for target_id, url in pages:
        if url == wanted_url:
            return target_id
    for target_id, url in pages:
        if host_allowed(host_of(url)):
            return target_id
    return pages[0][0]


def capture_page(websocket_url: str, wanted_url: str, host_allowed: Callable[[str], bool],
                 timeout_s: float, allow_plain: bool) -> PageCapture:
    """Save the page the run ended on. Raises CdpError when it cannot."""
    with CdpConnection(websocket_url, timeout_s, allow_plain=allow_plain) as connection:
        target_id = _choose_target(connection, wanted_url, host_allowed)
        attached = connection.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached.get("sessionId")
        if not isinstance(session_id, str):
            raise CdpError("no session for the page")
        current_url = _evaluate_text(connection, "location.href", session_id)
        if current_url != wanted_url and wanted_url != "" and host_allowed(host_of(wanted_url)):
            connection.call("Page.enable", {}, session_id)
            connection.call("Page.navigate", {"url": wanted_url}, session_id)
            connection.wait_for_event("Page.loadEventFired", session_id, timeout_s)
            current_url = _evaluate_text(connection, "location.href", session_id)
        text = _evaluate_text(connection, "document.body.innerText", session_id)
        printed = connection.call("Page.printToPDF", {"printBackground": True}, session_id)
        encoded_pdf = printed.get("data")
        if not isinstance(encoded_pdf, str):
            raise CdpError("no PDF data")
        try:
            pdf = base64.b64decode(encoded_pdf, validate=True)
        except (binascii.Error, ValueError):
            raise CdpError("PDF data is not base64") from None
        return PageCapture(url=current_url, text=text, pdf=pdf)
