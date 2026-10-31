from __future__ import annotations

import time

from starlette.testclient import TestClient

from app import config
from app.server import create_app
from app.sessions import SessionStore
from conftest import AUTH, new_session


def test_ttl_lookup_and_reaper(backend, monkeypatch):
    monkeypatch.setattr(config, "SESSION_CLEANUP_SECONDS", 0.01)
    store = SessionStore(ttl_seconds=0.04)
    with TestClient(create_app(store=store)) as client:
        first = new_session(client)
        second = new_session(client)
        store.sessions[first].last_activity -= 100
        assert client.get("/sessions/" + first + "/hint", headers=AUTH).status_code == 404
        assert first not in store.sessions
        deadline = time.monotonic() + 2
        while second in store.sessions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert second not in store.sessions


def test_active_http_request_touches_session(backend):
    session_id = new_session(backend.client)
    session = backend.store.sessions[session_id]
    session.last_activity -= 60
    before = session.last_activity
    backend.client.get("/sessions/" + session_id + "/suggestions", headers=AUTH)
    assert session.last_activity > before
