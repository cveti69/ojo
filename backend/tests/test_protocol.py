from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app import config
from conftest import AUTH, TOKEN, answer, idle, jpeg, new_session, post_frame, receive_type, wav


def test_health_and_session_defaults(backend):
    client = backend.client
    assert client.get("/health").json() == {"ok": True}
    response = client.post("/sessions", headers=AUTH, json={})
    assert response.status_code == 201
    session = backend.store.sessions[response.json()["session_id"]]
    assert (session.goal, session.user_lang, session.target_lang, session.text_entry, session.app_lang) == (
        "", "ru", "id", "keyboard", "id"
    )
    assert client.get("/sessions/" + session.id + "/suggestions", headers=AUTH).json() == {
        "step_id": None, "texts": [], "hint_ru": ""
    }
    assert post_frame(client, session.id).status_code == 409


@pytest.mark.parametrize("method,path,kwargs", [
    ("POST", "/sessions", {"json": {}}),
    ("POST", "/sessions/missing/goal", {"json": {"goal": "Помоги"}}),
    ("POST", "/sessions/missing/frames", {"content": b"", "headers": {
        "X-Frame-Width": "1", "X-Frame-Height": "1", "X-Frame-Ts": "1"}}),
    ("GET", "/sessions/missing/suggestions", {}),
    ("GET", "/sessions/missing/hint", {}),
    ("POST", "/sessions/missing/broadcast", {"json": {"state": "started"}}),
    ("POST", "/sessions/missing/end", {}),
    ("POST", "/translate", {"json": {"text": "Привет", "from": "ru", "to": "id"}}),
    ("POST", "/transcribe", {"content": b""}),
    ("GET", "/debug/missing", {}),
])
@pytest.mark.parametrize("token", [None, "wrong"])
def test_token_on_every_http_request(backend, method, path, kwargs, token):
    kwargs = dict(kwargs)
    kwargs["headers"] = {**kwargs.get("headers", {}), **({"X-Token": token} if token else {})}
    assert backend.client.request(method, path, **kwargs).status_code == 401


def test_empty_server_token_is_not_public(backend, monkeypatch):
    monkeypatch.setattr(config, "SHARED_TOKEN", "")
    assert backend.client.post("/sessions", headers={"X-Token": ""}, json={}).status_code == 401
    assert backend.client.get("/health").status_code == 200


def test_token_is_checked_before_body_and_headers(backend):
    assert backend.client.post("/sessions", content=b"bad JSON", headers={"Content-Type": "application/json"}).status_code == 401
    assert backend.client.post("/sessions/missing/frames", content=b"bad frame").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-Token": "wrong"}])
def test_websocket_token(backend, headers):
    with pytest.raises(WebSocketDenialResponse) as error:
        with backend.client.websocket_connect("/sessions/missing/events?token=" + TOKEN, headers=headers):
            pass
    assert error.value.status_code == 401


@pytest.mark.parametrize("suffix,method,kwargs", [
    ("goal", "POST", {"json": {"goal": "Помоги"}}),
    ("frames", "POST", {"content": b"", "headers": {
        "X-Frame-Width": "1", "X-Frame-Height": "1", "X-Frame-Ts": "1"}}),
    ("suggestions", "GET", {}), ("hint", "GET", {}),
    ("broadcast", "POST", {"json": {"state": "started"}}), ("end", "POST", {}),
])
def test_unknown_session(backend, suffix, method, kwargs):
    kwargs = dict(kwargs)
    kwargs["headers"] = {**AUTH, **kwargs.get("headers", {})}
    assert backend.client.request(method, "/sessions/missing/" + suffix, **kwargs).status_code == 404


def test_unknown_session_debug_translation_websocket(backend):
    assert backend.client.get("/debug/missing?token=" + TOKEN).status_code == 404
    response = backend.client.post("/translate", headers=AUTH, json={
        "text": "Привет", "from": "ru", "to": "id", "session_id": "missing"
    })
    assert response.status_code == 404
    with pytest.raises(WebSocketDenialResponse) as error:
        with backend.client.websocket_connect("/sessions/missing/events", headers=AUTH):
            pass
    assert error.value.status_code == 404


def test_full_protocol_and_debug(backend):
    client = backend.client
    session_id = new_session(client)
    prefix = "/sessions/" + session_id
    assert client.get(prefix + "/hint", headers=AUTH).status_code == 204
    reply = client.post(prefix + "/goal", headers=AUTH, json={"goal": "Помоги купить шампунь"})
    assert reply.status_code == 200
    assert "демонстрацию экрана" in reply.json()["reply_ru"]
    assert len(backend.meta.text_calls) == 1
    with client.websocket_connect(prefix + "/events", headers=AUTH) as websocket:
        assert websocket.receive_json()["state"] == "waiting_frames"
        assert client.post(prefix + "/broadcast", headers=AUTH, json={"state": "started"}).json() == {"ok": True}
        assert websocket.receive_json()["state"] == "broadcasting"
        response = post_frame(client, session_id)
        assert response.status_code == 202 and response.json() == {"accepted": True}
        assert receive_type(websocket, "status")["state"] == "analyzing"
        event = receive_type(websocket, "hint")
        idle(backend, session_id)
        assert event["step_id"] == "s1" and event["speak"] is True
        assert event["paste_text"] is None and event["needs_user_confirm"] is False
        with Image.open(io.BytesIO(base64.b64decode(event["image_jpeg_base64"]))) as image:
            assert image.size == (720, 960) and image.format == "JPEG"
        assert client.get(prefix + "/suggestions", headers=AUTH).json() == {
            "step_id": "s1", "texts": ["sampo anti ketombe"], "hint_ru": "запрос для поиска"
        }
        assert client.get(prefix + "/hint?after=s0", headers=AUTH).json() == event
        assert client.get(prefix + "/hint?after=s1", headers=AUTH).status_code == 204
        assert client.get(prefix + "/hint?after=s9", headers=AUTH).status_code == 204
        page = client.get("/debug/" + session_id + "?token=" + TOKEN)
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        assert 'http-equiv="refresh" content="1"' in page.text
        assert page.text.count("data:image/jpeg;base64,") == 2
        assert "model_ms" in page.text and "Нажми на поиск" in page.text
        assert TOKEN not in page.text and page.headers["cache-control"] == "no-store"
        timings = backend.store.sessions[session_id].timings
        assert all(isinstance(timings[key], int) and timings[key] >= 0 for key in (
            "receive_ms", "model_ms", "box_ms", "image_ms", "send_ms", "total_ms"
        ))
        assert client.post(prefix + "/broadcast", headers=AUTH, json={"state": "finished"}).json() == {"ok": True}
        while websocket.receive_json()["state"] != "broadcast_stopped":
            pass
        assert client.post(prefix + "/end", headers=AUTH).json() == {"ok": True}
        assert receive_type(websocket, "done")["text_ru"] == "Сессия завершена"
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()
    assert post_frame(client, session_id).status_code == 409


def test_model_done_event(backend):
    session_id = new_session(backend.client)
    backend.meta.answers = [answer(done=True, instruction="Готово")]
    with backend.client.websocket_connect("/sessions/" + session_id + "/events", headers=AUTH) as websocket:
        websocket.receive_json()
        post_frame(backend.client, session_id)
        assert receive_type(websocket, "status")["state"] == "analyzing"
        assert receive_type(websocket, "done") == {"type": "done", "text_ru": "Готово, цель достигнута"}
    idle(backend, session_id)
    assert backend.store.sessions[session_id].completed
    assert post_frame(backend.client, session_id, jpeg((30, 60, 90))).json()["accepted"] is False
    assert len(backend.meta.vision_calls) == 1


@pytest.mark.parametrize("mode,action,expected", [("clipboard", "type", True), ("keyboard", "type", False),
                                                  ("clipboard", "tap", False)])
def test_clipboard_and_keyboard(backend, mode, action, expected):
    session_id = new_session(backend.client, text_entry=mode)
    backend.meta.answers = [answer(action=action, insert_texts=["sampo anti ketombe", "sampo"])]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    event = backend.client.get("/sessions/" + session_id + "/hint", headers=AUTH).json()
    assert event["paste_text"] == ("sampo anti ketombe" if expected else None)
    assert ("«Вставить»" in event["instruction_ru"]) is expected
    assert backend.client.get("/sessions/" + session_id + "/suggestions", headers=AUTH).json()["texts"] == [
        "sampo anti ketombe", "sampo"
    ]


def test_confirmation_stops_analysis_and_overrides_clipboard(backend):
    session_id = new_session(backend.client, text_entry="clipboard")
    backend.meta.answers = [answer(needs_user_confirm=True, action="type", done=True)]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.last_hint["instruction_ru"] == "Проверь сумму и подтверди сам."
    assert session.last_hint["needs_user_confirm"] is True
    assert session.last_hint["paste_text"] is None and session.last_hint["insert_texts"] == []
    assert not session.completed
    assert post_frame(backend.client, session_id).json() == {"accepted": False, "reason": "user_confirm"}
    assert post_frame(backend.client, session_id, jpeg((20, 40, 70))).json()["accepted"] is False
    assert len(backend.meta.vision_calls) == 1
    assert len(session.history) == 1


def test_translate_languages_and_auto(backend):
    client = backend.client
    session_id = new_session(client, target_lang="auto")
    payload = {"text": "Подождите здесь", "from": "ru", "to": "auto", "session_id": session_id}
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 409
    assert client.post("/translate", headers=AUTH, json={**payload, "session_id": None}).status_code == 409
    backend.meta.answers = [answer(screen="[lang=id] Layar utama")]
    post_frame(client, session_id)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].app_lang == "id"
    assert "[lang=CODE]" in backend.meta.vision_calls[0][0]["content"][0]["text"]
    response = client.post("/translate", headers=AUTH, json=payload)
    assert response.status_code == 200 and response.json() == {"text": "Tolong tunggu di sini."}
    assert json.loads(backend.meta.text_calls[-1][-1]["content"])["to"] == "id"
    assert "только перевод" in backend.meta.text_calls[-1][0]["content"]
    assert client.post("/translate", headers=AUTH, json={**payload, "from": "en", "to": "ms"}).status_code == 200
    translated = json.loads(backend.meta.text_calls[-1][-1]["content"])
    assert (translated["from"], translated["to"]) == ("en", "ms")


@pytest.mark.parametrize("transcript", ["Пожалуйста, помоги", ""])
def test_transcribe_and_silence(backend, transcript):
    backend.meta.asr_reply = {"transcript": transcript, "audioDurationMs": 450, "turns": [{"text": "ignore"}]}
    response = backend.client.post("/transcribe", headers={**AUTH, "Content-Type": "audio/wav"}, content=wav())
    assert response.status_code == 200 and response.json() == {"text": transcript}
    assert len(backend.meta.asr_calls) == 1


def test_invalid_payloads(backend, monkeypatch):
    client = backend.client
    session_id = new_session(client)
    prefix = "/sessions/" + session_id
    assert client.post("/sessions", headers=AUTH, json={"text_entry": "bad"}).status_code == 422
    assert client.post(prefix + "/goal", headers=AUTH, json={"goal": "  "}).status_code == 422
    assert client.post(prefix + "/broadcast", headers=AUTH, json={"state": "bad"}).status_code == 422
    assert client.get(prefix + "/hint?after=bad", headers=AUTH).status_code == 422
    assert client.post(prefix + "/frames", headers=AUTH, content=jpeg()).status_code == 422
    assert post_frame(client, session_id, b"invalid").status_code == 422
    assert client.post("/transcribe", headers={**AUTH, "Content-Type": "audio/wav"}, content=b"invalid").status_code == 422
    assert client.post("/transcribe", headers={**AUTH, "Content-Type": "audio/wav"}, content=wav(rate=8000)).status_code == 422
    assert client.post("/transcribe", headers={**AUTH, "Content-Type": "audio/mpeg"}, content=wav()).status_code == 415
    monkeypatch.setattr(config, "MAX_AUDIO_BYTES", 4)
    assert client.post("/transcribe", headers={**AUTH, "Content-Type": "audio/wav"}, content=wav()).status_code == 413
    monkeypatch.setattr(config, "MAX_FRAME_BYTES", 4)
    assert post_frame(client, session_id).status_code == 413
