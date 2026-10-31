from __future__ import annotations

import time

from starlette.testclient import TestClient

from app import config
from app.server import create_app
from app.sessions import SessionStore
from conftest import AUTH, answer, idle, new_session, post_frame


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


def test_new_goal_clears_payment_and_suggestions(backend):
    session_id = new_session(backend.client)
    backend.meta.answers = [answer(needs_user_confirm=True), answer()]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    response = backend.client.post("/sessions/" + session_id + "/goal", headers=AUTH, json={"goal": "Новая цель"})
    assert response.status_code == 200
    session = backend.store.sessions[session_id]
    assert not session.needs_user_confirm and session.last_hint is None
    assert post_frame(backend.client, session_id).json()["accepted"] is True
    idle(backend, session_id)
    assert session.step_id == "s2"
    assert backend.client.get("/sessions/" + session_id + "/hint?after=s1", headers=AUTH).json()["step_id"] == "s2"


def test_active_http_request_touches_session(backend):
    session_id = new_session(backend.client)
    session = backend.store.sessions[session_id]
    session.last_activity -= 60
    before = session.last_activity
    backend.client.get("/sessions/" + session_id + "/suggestions", headers=AUTH)
    assert session.last_activity > before
