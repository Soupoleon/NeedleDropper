"""DECK-EVENTS-NO-ORIGIN (docs/review/baseline-findings.md): a web page must not register a fake player
through a token-less, Origin-less no-cors request to ``/events`` (and must not reach ``/api`` or
``/status`` that way). Same-origin pages (which send no Origin on EventSource) and tools still work."""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "companion") not in sys.path:
    sys.path.insert(0, str(ROOT / "companion"))

from needledropper.deck.server import Deck  # noqa: E402


@pytest.fixture
def deck():
    d = Deck(port=0, heartbeat_sec=0.2).start()
    try:
        yield d
    finally:
        d.stop()


def request(deck, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", deck.port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    status = resp.status
    if path.startswith("/events") and status == 200:
        return status, conn  # the stream stays open: the caller closes it
    resp.read()
    conn.close()
    return status, None


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_cross_site_no_cors_events_request_is_refused(deck):
    """The finding's reproduction: valid Host, no Origin, no token, Sec-Fetch-Site: cross-site."""
    status, _ = request(deck, "GET", "/events?client=player",
                        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"})
    assert status == 403
    time.sleep(0.1)
    assert not deck.page_open and not deck.connected and deck.client_count == 0
    for site in ("same-site", "cross-site"):
        assert request(deck, "GET", "/api/state", {"Sec-Fetch-Site": site, "X-ND-Token": deck.token})[0] == 403
        assert request(deck, "POST", "/status", {"Sec-Fetch-Site": site, "X-ND-Token": deck.token,
                                                  "Content-Type": "application/json"},
                       json.dumps({"state": "ready"}))[0] == 403


def test_same_origin_pages_and_tools_still_connect(deck):
    # v1.2.1 review ON-1: the player page sends the launch key from its address
    status, conn = request(deck, "GET", f"/events?client=player&k={deck.player_key}", {"Sec-Fetch-Site": "same-origin"})
    try:
        assert status == 200 and wait_for(lambda: deck.page_open)
    finally:
        conn.close()
    status, conn = request(deck, "GET", "/events?client=panel")  # curl-like: no Sec-Fetch-* at all
    try:
        assert status == 200
    finally:
        conn.close()
    assert request(deck, "GET", "/healthz", {"Sec-Fetch-Site": "cross-site"})[0] == 200  # single-instance probe
    assert request(deck, "POST", "/status", {"Sec-Fetch-Site": "same-origin", "X-ND-Token": deck.token,
                                             "Content-Type": "application/json"},
                   json.dumps({"state": "ready"}))[0] == 204
