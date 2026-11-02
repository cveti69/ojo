import asyncio
import json
import logging
import re
import stat

import pytest
from fastapi.testclient import TestClient

from app import config, landing
from conftest import TOKEN, new_session


@pytest.fixture
def visitor(backend, monkeypatch, tmp_path):
    """Somebody on the street: no token, and a file to keep the form in."""
    monkeypatch.setattr(config, "BETA_FILE", str(tmp_path / "beta.jsonl"))
    return TestClient(backend.app, follow_redirects=False)


def kept(tmp_path):
    file = tmp_path / "beta.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []


def ask(visitor, **fields):
    return visitor.post("/beta", data=fields or {"email": "someone@example.com"})


def state_of(response):
    return re.search(r'data-state="(\w*)"', response.text).group(1)


@pytest.mark.parametrize("path", ["/", "/beta"])
def test_the_page_is_open_to_anybody_and_asks_for_nothing_from_outside(visitor, path):
    response = visitor.get(path)
    text = response.text
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert state_of(response) == "" and "<script" not in text.lower() and "<iframe" not in text.lower()
    assert re.findall(r'(?:src|href|action)="([^"]+)"', text) == [text.split('href="')[1].split('"')[0], "/beta#beta"]
    assert re.findall(r"https?://[^'\"\s]+", text) == ["http://www.w3.org/2000/svg"]  # The namespace of the small icon.
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src" not in policy and "form-action 'self'" in policy
    assert response.headers["x-content-type-options"] == "nosniff" and response.headers["cache-control"] == "no-store"


def test_the_page_says_what_it_must_and_nothing_it_must_not(visitor):
    text = visitor.get("/").text.lower()
    assert "shows you" in text and "request beta" in text and "by request" in text and "we'll write to you" in text
    assert "3-minute" in text and "meta's ai" in text and "elevenlabs" in text and "payment screens" in text
    for word in ("any app", "every app", "instant", "right away", "immediately", "medic", "doctor", "health", "github", "tunjuk"):
        assert word not in text, word


@pytest.mark.parametrize("method, path", [("get", "/debug/s_none"), ("post", "/sessions"), ("get", "/beta/"), ("get", "/beta/x"),
                                          ("get", "/index.html"), ("get", "/favicon.ico")])
def test_nothing_else_opened_with_the_page(visitor, method, path):
    assert getattr(visitor, method)(path).status_code == 401


@pytest.mark.parametrize("host", ["example.com/beta?x=", "example.com/?x=", "example.com/#", "example.com/health?x="])
def test_a_host_header_does_not_open_a_private_path(visitor, host):
    assert visitor.post("/sessions", json={}, headers={"Host": host}).status_code == 401


def test_the_token_still_opens_what_it_opened(backend, visitor):
    assert visitor.get("/health").json() == {"ok": True}
    session_id = new_session(backend.client)
    assert visitor.get(f"/debug/{session_id}", headers={"X-Token": TOKEN}).status_code == 200
    assert visitor.get(f"/debug/{session_id}", headers={"X-Token": "wrong"}).status_code == 401


def test_a_request_keeps_the_email_and_the_time_and_nothing_else(visitor, tmp_path):
    response = visitor.post("/beta", data={"email": "  someone@example.com ", "extra": ""},
                            headers={"User-Agent": "A phone", "Referer": "https://a.page", "X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == 200 and state_of(response) == "sent"
    [entry] = kept(tmp_path)
    assert set(entry) == {"email", "at"} and entry["email"] == "someone@example.com"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", entry["at"])
    assert "203.0.113.9" not in (tmp_path / "beta.jsonl").read_text()
    assert "someone@example.com" not in response.text


def test_the_file_is_closed_to_others_even_if_it_was_not(visitor, tmp_path):
    ask(visitor)
    file = tmp_path / "beta.jsonl"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    file.chmod(0o644)
    ask(visitor)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600 and len(kept(tmp_path)) == 2


@pytest.mark.parametrize("email", ["", " ", "someone", "someone@", "@example.com", "someone@example", "some one@example.com",
                                   "a@b@example.com", "someone@.com", "someone@example.", "someone@exa mple.com", "line\nbreak@example.com",
                                   "bell\x07@example.com", "a" * 245 + "@example.com", "someone@example.com other", "someone@example.com,other@example.com",
                                   "someone@example/com.net", "someone@example.com/"])
def test_what_is_no_email_is_not_kept(visitor, tmp_path, email):
    response = ask(visitor, email=email)
    assert response.status_code == 400 and state_of(response) == "invalid" and kept(tmp_path) == []


@pytest.mark.parametrize("email", ["someone@example.com", "first.last+tag@mail.example.co.uk", "имя@пример.рф", "a" * 242 + "@example.com",
                                   "someone@my-mail.example.com"])
def test_a_real_email_is_kept(visitor, tmp_path, email):
    assert state_of(ask(visitor, email=email)) == "sent" and kept(tmp_path)[0]["email"] == email


@pytest.mark.parametrize("body", [b"", b"\xff\xfe", b"email=%ff%40example.com", b"a=1&b=2&c=3&d=4&e=5&email=x%40example.com", b'{"email": "someone@example.com"}'])
def test_a_body_that_is_no_form_is_not_kept(visitor, tmp_path, body):
    response = visitor.post("/beta", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400 and state_of(response) == "invalid" and kept(tmp_path) == []


def test_a_body_past_the_limit_is_not_read_on(visitor, tmp_path):
    fits = b"email=someone%40example.com&extra=" + b"".ljust(landing.MAX_BODY_BYTES - 34, b" ")
    assert len(fits) == landing.MAX_BODY_BYTES
    assert visitor.post("/beta", content=fits).status_code != 413
    response = visitor.post("/beta", content=fits + b" ")
    assert response.status_code == 413 and state_of(response) == "invalid"


def test_a_body_that_never_ends_is_not_waited_for(backend, visitor, tmp_path, monkeypatch):
    monkeypatch.setattr(landing, "BODY_SECONDS", 0.05)
    bodies = [{"type": "http.request", "body": b"email=", "more_body": True}]
    answer = []

    async def receive():
        if bodies:
            return bodies.pop()
        await asyncio.Event().wait()

    async def send(message):
        answer.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": "/beta", "raw_path": b"/beta",
             "root_path": "", "scheme": "http", "query_string": b"", "headers": [], "client": ("203.0.113.9", 1), "server": ("testserver", 80)}
    asyncio.run(asyncio.wait_for(backend.app(scope, receive, send), 5))
    assert answer[0]["status"] == 408 and kept(tmp_path) == []


def test_the_field_nobody_sees_gets_the_same_thanks_and_keeps_nothing(visitor, tmp_path):
    honest = ask(visitor, email="someone@example.com")
    bot = ask(visitor, email="bot@example.com", extra="http://spam.example")
    assert bot.status_code == honest.status_code and bot.text == honest.text and len(kept(tmp_path)) == 1


def test_one_address_can_ask_a_few_times_in_a_window(visitor, tmp_path):
    for number in range(landing.PER_ADDRESS):
        assert ask(visitor, email=f"person{number}@example.com").status_code == 200
    response = ask(visitor, email="one-more@example.com")
    assert response.status_code == 429 and state_of(response) == "later" and len(kept(tmp_path)) == landing.PER_ADDRESS


def test_wrong_requests_count_too(visitor, tmp_path):
    for _ in range(landing.PER_ADDRESS):
        assert ask(visitor, email="nothing").status_code == 400
    assert ask(visitor).status_code == 429 and kept(tmp_path) == []


def test_the_limit_is_for_each_address_and_ends_with_its_window(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(landing.time, "monotonic", lambda: now[0])
    limit = landing.Limit()
    assert all(limit.allows("one") for _ in range(landing.PER_ADDRESS))
    assert not limit.allows("one") and limit.allows("two")
    now[0] += landing.WINDOW_SECONDS - 1
    assert not limit.allows("one")
    now[0] += 1
    assert limit.allows("one") and len(limit.seen) == 1


def test_the_limit_holds_for_everybody_and_stays_small(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(landing.time, "monotonic", lambda: now[0])
    limit = landing.Limit()
    assert all(limit.allows(str(number)) for number in range(landing.PER_WINDOW))
    assert not limit.allows("somebody new") and len(limit.seen) == landing.PER_WINDOW
    now[0] += landing.WINDOW_SECONDS
    assert limit.allows("somebody new") and len(limit.seen) == 1


def test_a_full_file_takes_no_more_and_does_not_thank_them(visitor, tmp_path, monkeypatch):
    monkeypatch.setattr(landing, "MAX_FILE_BYTES", 10)
    (tmp_path / "beta.jsonl").write_text("x" * 10)
    response = ask(visitor)
    assert response.status_code == 503 and state_of(response) == "later"
    assert (tmp_path / "beta.jsonl").read_text() == "x" * 10


def test_no_file_means_no_thanks(visitor, monkeypatch):
    monkeypatch.setattr(config, "BETA_FILE", "")
    response = ask(visitor)
    assert response.status_code == 503 and state_of(response) == "later"


def test_a_failed_write_does_not_thank_them_or_log_the_email(visitor, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "BETA_FILE", str(tmp_path / "no" / "such" / "folder" / "beta.jsonl"))
    with caplog.at_level(logging.INFO):
        response = ask(visitor)
    assert response.status_code == 503 and state_of(response) == "later" and "someone@example.com" not in caplog.text


def test_a_part_written_does_not_thank_them(visitor, monkeypatch):
    monkeypatch.setattr(landing.os, "write", lambda file, data: len(data) - 1)
    response = ask(visitor)
    assert response.status_code == 503 and state_of(response) == "later"


def test_the_journal_keeps_no_address_of_the_page(monkeypatch):
    def passes(path):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("203.0.113.9", "POST", path, "1.1", 200), None)
        return landing.NoVisitors().filter(record)

    assert not passes("/") and not passes("/beta") and not passes("/?a=1") and not passes("/beta?b=2")
    assert passes("/health") and passes("/sessions/s_1/look") and passes("/debug/s_1?token=x")
    assert any(isinstance(item, landing.NoVisitors) for item in logging.getLogger("uvicorn.access").filters)


def test_a_line_that_is_no_request_passes_the_journal():
    assert landing.NoVisitors().filter(logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "started", None, None))


def test_the_file_stands_beside_the_calls_and_outside_the_repository():
    source = open(config.__file__, encoding="utf-8").read()
    assert 'os.path.dirname(CALLS_DIR.rstrip("/")), "beta.jsonl"' in source
