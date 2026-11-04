"""What OjO says is deleted from the history of the ElevenLabs account. Here ElevenLabs is a fake that numbers each line."""
from __future__ import annotations

import http.client
import http.server
import json
import logging
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from starlette.testclient import TestClient

from app import config, pool, voice
from app.server import create_app
from conftest import AUTH, TOKEN, FakeEars, new_session

KEY = "eleven-test-key"
WORDS = "Нажми на синюю кнопку"
NOT_FOUND = (404, {"detail": {"status": "history_item_not_found", "message": "Item " + WORDS + " was not found"}})
REFUSED = (401, {"detail": {"status": "missing_permissions", "message": "The key " + KEY + " may not say " + WORDS}})


class Answer:
    def __init__(self, status, body, headers):
        self.status, self.body = status, body
        self.headers = {name.lower(): value for name, value in headers.items()}

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self):
        body, self.body = self.body, b""
        return body

    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class Connection:
    def __init__(self, eleven):
        self.eleven, self.sock, self.asked = eleven, self, None

    def settimeout(self, timeout):
        pass

    def request(self, method, path, data, headers):
        self.asked = SimpleNamespace(method=method, path=path, headers=dict(headers), connection=self, at=time.monotonic())
        with self.eleven.lock:
            self.eleven.requests.append(self.asked)

    def getresponse(self):
        return self.eleven.answer(self.asked)

    def close(self):
        self.sock = None


class FakeElevenLabs:
    """Gives each line a number of its own, as ElevenLabs does, and takes deletions only of the numbers it gave. A deletion
    gets the next of its answers, and once they run out, the line is gone. A line can be made to reach the history only some
    time after its number, and each deletion to take some time."""

    def __init__(self, deletions=(), header="history-item-id", number=None, ready_after=0.0, delay=0.0):
        self.deletions = list(deletions)
        self.header, self.number = header, number
        self.ready_after, self.delay = ready_after, delay
        self.given = []
        self.started = {}
        self.answered = []
        self.requests = []
        self.lock = threading.Lock()
        self.deleting = threading.Event()
        self.go = None  # When a test sets it, a deletion waits for it at ElevenLabs.

    def connect(self, host, timeout):
        assert host == "eleven.invalid"
        return Connection(self)

    def answer(self, asked):
        if asked.method == "POST":
            assert asked.path.startswith("/v1/text-to-speech/")
            number = self.number if self.number is not None else "Hx7Qm2Lp9Za1Bc3D%04d" % len(self.given)
            self.given.append(number)
            self.started[number] = time.monotonic()
            return Answer(200, bytes(voice.SPEECH_CHUNK * 3), {self.header: number} if self.header else {})
        assert asked.method == "DELETE" and asked.path.rsplit("/", 1)[0] == "/v1/history"
        number = asked.path.rsplit("/", 1)[1]
        assert number in self.given, "A number ElevenLabs never gave this server"
        self.deleting.set()
        if self.go:
            assert self.go.wait(5)
        time.sleep(self.delay)
        with self.lock:
            if time.monotonic() < self.started[number] + self.ready_after:
                reply = NOT_FOUND
            else:
                reply = self.deletions.pop(0) if self.deletions else (200, {"status": "ok"})
            self.answered.append(reply if isinstance(reply, Exception) else reply[0])
        if isinstance(reply, Exception):
            raise reply
        body = reply[1] if isinstance(reply[1], bytes) else json.dumps(reply[1]).encode()
        return Answer(reply[0], body, {})

    def deleted(self):
        with self.lock:
            return [asked.path.rsplit("/", 1)[1] for asked in self.requests if asked.method == "DELETE"]


@pytest.fixture
def eleven(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_BASE", "https://eleven.invalid/v1")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", KEY)
    monkeypatch.setattr(voice, "HISTORY_FIRST", 0.02)
    monkeypatch.setattr(voice, "HISTORY_AGAIN", (0.02, 0.02, 0.02))

    def make(**changes):
        fake = FakeElevenLabs(**changes)
        monkeypatch.setattr(pool, "connect", fake.connect)
        return fake
    return make


def until(check, seconds=3):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "It did not happen in time"
        time.sleep(0.005)


def settled():
    """Longer than every wait on the clock the tries would take."""
    time.sleep(0.2)


def speak(client, text=WORDS):
    reply = client.post("/speech", headers=AUTH, json={"text": text})
    assert reply.status_code == 200 and reply.content
    return reply


def run_out(client, fake):
    """A line whose four tries on the clock all failed."""
    speak(client)
    until(lambda: len(fake.deleted()) == 4)
    settled()
    assert len(fake.deleted()) == 4


def test_a_line_is_deleted_once_its_sound_is_over(eleven):
    fake = eleven()
    sound = voice.ElevenLabsSynthesizer().stream(WORDS)
    next(sound)
    voice.history.sweep(time.monotonic())  # Not even the end of a call takes a line while its sound still goes.
    settled()
    assert fake.deleted() == []
    assert len(list(sound)) == 2
    over = time.monotonic()
    until(fake.deleted)
    settled()
    (deletion,) = [asked for asked in fake.requests if asked.method == "DELETE"]
    assert deletion.path == "/v1/history/" + fake.given[0] and deletion.headers == {"xi-api-key": KEY} and deletion.at >= over
    assert voice.history.lines == {}


def test_a_line_the_phone_left_halfway_is_deleted_too(eleven):
    fake = eleven()
    sound = voice.ElevenLabsSynthesizer().stream(WORDS)
    next(sound)
    sound.close()
    until(lambda: fake.deleted() == fake.given)


def test_a_line_the_phone_never_took_is_deleted_all_the_same(eleven, monkeypatch):
    monkeypatch.setattr(voice, "HISTORY_LONGEST", 0.1)
    fake = eleven()
    sound = voice.ElevenLabsSynthesizer().stream(WORDS)
    until(lambda: fake.deleted() == fake.given)
    del sound


def test_a_line_spoken_through_the_server_is_deleted(backend, eleven):
    fake = eleven()
    speak(backend.client)
    speak(backend.client, "Теперь вниз")
    until(lambda: sorted(fake.deleted()) == sorted(fake.given))
    settled()
    assert len(fake.given) == 2 and len(fake.deleted()) == 2
    assert {asked.method for asked in fake.requests} == {"POST", "DELETE"}  # Never a look at the history itself.


@pytest.mark.parametrize("header", ["history-item-id", "History-Item-Id", "history_item_id"])
def test_the_number_is_read_however_the_header_is_spelt(eleven, header):
    fake = eleven(header=header)
    b"".join(voice.ElevenLabsSynthesizer().stream(WORDS))
    until(lambda: fake.deleted() == fake.given)


def test_a_line_without_a_number_leaves_nothing_to_delete(eleven):
    fake = eleven(header=None)
    b"".join(voice.ElevenLabsSynthesizer().stream(WORDS))
    settled()
    assert [asked.method for asked in fake.requests] == ["POST"] and voice.history.lines == {}


@pytest.mark.parametrize("number", ["../voices/Hx7Qm2Lp", "Hx7Qm2Lp?all=1", "Hx7 Qm2Lp", "x" * 65])
def test_a_number_that_is_not_one_is_not_sent(eleven, caplog, number):
    fake = eleven(number=number)
    b"".join(voice.ElevenLabsSynthesizer().stream(WORDS))
    settled()
    assert [asked.method for asked in fake.requests] == ["POST"] and voice.history.lines == {}
    assert "number we cannot use" in caplog.text and number not in caplog.text


@pytest.mark.parametrize("failure", [NOT_FOUND, (500, {}), (429, {"detail": {"status": "too_many_concurrent_requests"}}),
                                     ConnectionResetError(), TimeoutError()])
def test_a_deletion_that_failed_is_tried_again(eleven, failure):
    fake = eleven(deletions=[failure, failure])
    b"".join(voice.ElevenLabsSynthesizer().stream(WORDS))
    until(lambda: len(fake.deleted()) == 3)
    settled()
    assert fake.deleted() == fake.given * 3 and voice.history.lines == {}


def test_the_voice_does_not_wait_for_a_deletion(eleven):
    fake = eleven()
    fake.go = threading.Event()
    synthesizer = voice.ElevenLabsSynthesizer()
    first = b"".join(synthesizer.stream(WORDS))
    assert fake.deleting.wait(3)  # The deletion of the first line hangs at ElevenLabs.
    started = time.monotonic()
    second = b"".join(synthesizer.stream("Теперь вниз"))
    assert time.monotonic() - started < 0.5 and second == first
    voices = [asked.connection for asked in fake.requests if asked.method == "POST"]
    assert voices[0] is voices[1]  # The second line went on the warm connection of the first.
    fake.go.set()
    until(lambda: sorted(fake.deleted()) == sorted(fake.given))


@pytest.mark.parametrize("ending", ["end", "erased after an hour"])
def test_a_line_whose_tries_ran_out_is_tried_again_when_the_call_ends(backend, eleven, caplog, ending):
    fake = eleven(deletions=[NOT_FOUND] * 4)
    session_id = new_session(backend.client)
    run_out(backend.client, fake)
    assert "not deleted yet, 404 history_item_not_found" in caplog.text
    if ending == "end":
        assert backend.client.post("/sessions/" + session_id + "/end", headers=AUTH).status_code == 200
    else:
        backend.store.sessions[session_id].last_activity -= config.SESSION_TTL_SECONDS
        assert backend.client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 404
    until(lambda: voice.history.lines == {})
    assert fake.deleted() == fake.given * 5


def test_a_line_not_deleted_at_the_end_of_its_call_is_let_go(backend, eleven, caplog):
    fake = eleven(deletions=[REFUSED] * 9)
    first, second = new_session(backend.client), new_session(backend.client)
    run_out(backend.client, fake)
    backend.client.post("/sessions/" + first + "/end", headers=AUTH)
    until(lambda: voice.history.lines == {})
    assert len(fake.deleted()) == 5 and "a line was not deleted, 401 missing_permissions" in caplog.text
    backend.client.post("/sessions/" + second + "/end", headers=AUTH)
    settled()
    assert len(fake.deleted()) == 5


def test_a_broken_connection_tries_the_line_again_and_keeps_it_for_the_end(backend, eleven, caplog):
    fake = eleven(deletions=[NOT_FOUND] * 5)
    session_id = new_session(backend.client)
    run_out(backend.client, fake)
    with backend.client.websocket_connect("/sessions/" + session_id + "/events", headers=AUTH) as events:
        events.receive_json()
    until(lambda: len(fake.deleted()) == 5)
    settled()
    assert list(voice.history.lines) == fake.given and "was not deleted" not in caplog.text
    assert caplog.text.count("not deleted yet") == 1  # Once for each line, however often it waits again.
    assert backend.client.post("/sessions/" + session_id + "/end", headers=AUTH).status_code == 200
    until(lambda: voice.history.lines == {})
    assert len(fake.deleted()) == 6


def test_a_call_still_going_keeps_its_lines_when_an_old_session_is_erased(backend, eleven, caplog):
    fake = eleven(deletions=[NOT_FOUND] * 5)
    old, live = new_session(backend.client), new_session(backend.client)
    backend.store.sessions[old].last_activity -= config.SESSION_TTL_SECONDS  # Its phone went quiet an hour ago.
    run_out(backend.client, fake)
    assert backend.client.get("/sessions/" + old + "/hint", headers=AUTH).status_code == 404
    until(lambda: len(fake.deleted()) == 5)
    settled()
    assert list(voice.history.lines) == fake.given and "was not deleted" not in caplog.text
    assert backend.client.post("/sessions/" + live + "/end", headers=AUTH).status_code == 200
    until(lambda: voice.history.lines == {})
    assert len(fake.deleted()) == 6


def test_a_line_the_phone_never_took_is_tried_at_the_end_of_the_call_too(eleven, monkeypatch):
    monkeypatch.setattr(voice, "HISTORY_LONGEST", 0.05)
    fake = eleven(deletions=[NOT_FOUND] * 4)
    sound = voice.ElevenLabsSynthesizer().stream(WORDS)
    until(lambda: len(fake.deleted()) == 4)
    settled()
    voice.history.sweep(time.monotonic())
    until(lambda: voice.history.lines == {})
    assert len(fake.deleted()) == 5
    del sound


def test_an_answer_nested_past_the_recursion_limit_stops_no_deletion(eleven):
    fake = eleven(deletions=[(500, b"[" * 100000)])
    synthesizer = voice.ElevenLabsSynthesizer()
    b"".join(synthesizer.stream(WORDS))
    until(lambda: fake.answered == [500, 200])
    b"".join(synthesizer.stream("Теперь вниз"))
    until(lambda: voice.history.lines == {})
    assert sorted(fake.deleted()) == sorted(fake.given + fake.given[:1])


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_try_that_breaks_its_thread_stops_no_deletion(eleven, monkeypatch):
    fake = eleven()
    tried = voice.delete_line
    broken = []

    def breaks_once(number):
        if not broken:
            broken.append(number)
            raise RuntimeError("a fault of our own")
        return tried(number)
    monkeypatch.setattr(voice, "delete_line", breaks_once)
    synthesizer = voice.ElevenLabsSynthesizer()
    b"".join(synthesizer.stream(WORDS))
    until(lambda: broken and voice.history.worker is None)
    b"".join(synthesizer.stream("Теперь вниз"))
    until(lambda: voice.history.lines == {})
    assert sorted(fake.deleted()) == sorted(fake.given)


def test_the_lines_left_are_tried_once_more_as_the_server_stops(eleven, monkeypatch):
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    fake = eleven(deletions=[NOT_FOUND] * 4, delay=0.1)
    with TestClient(create_app(ears=FakeEars)) as client:
        run_out(client, fake)
    assert fake.answered[-1] == 200 and voice.history.lines == {}


def test_a_line_that_ends_just_before_the_server_stops_is_deleted(eleven, monkeypatch):
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(voice, "HISTORY_AGAIN", (3.0, 10.0, 30.0))
    monkeypatch.setattr(voice, "HISTORY_FLUSH", 1.0)  # Sooner than the next try on the clock.
    monkeypatch.setattr(voice, "HISTORY_FLUSH_AGAIN", 0.05)
    fake = eleven(ready_after=0.3, delay=0.05)
    with TestClient(create_app(ears=FakeEars)) as client:
        speak(client)
    assert voice.history.lines == {} and fake.answered[0] == 404 and fake.answered[-1] == 200


def test_the_server_stops_in_time_and_counts_the_lines_left(eleven, monkeypatch, caplog):
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(voice, "HISTORY_FLUSH", 0.3)
    monkeypatch.setattr(voice, "HISTORY_FLUSH_AGAIN", 0.05)
    fake = eleven(deletions=[REFUSED] * 1000)
    with TestClient(create_app(ears=FakeEars)) as client:
        speak(client)
        leaving = time.monotonic()
    assert time.monotonic() - leaving < 1.5 and len(fake.deleted()) > 2
    assert "Voice history: lines not deleted when the server stopped: 1." in caplog.text and fake.given[0] not in caplog.text


class LocalEleven(http.server.BaseHTTPRequestHandler):
    """ElevenLabs on this computer over plain HTTP. The sound comes in pieces, as the real stream does."""
    protocol_version = "HTTP/1.1"
    deleted = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("history-item-id", "LocalLine0001")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for _ in range(40):
                self.wfile.write(b"12c0\r\n" + bytes(4800) + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass

    def do_DELETE(self):
        LocalEleven.deleted.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "15")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')


class Quiet(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # Our server hangs up on the stream it no longer needs, and that is all this would say.


def test_a_line_the_phone_hangs_up_on_is_deleted_by_the_real_server(monkeypatch):
    eleven = Quiet(("127.0.0.1", 0), LocalEleven)
    threading.Thread(target=eleven.serve_forever, daemon=True).start()
    LocalEleven.deleted = []
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(config, "ELEVENLABS_API_BASE", "http://127.0.0.1:%d/v1" % eleven.server_address[1])
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", KEY)
    monkeypatch.setattr(voice, "HISTORY_FIRST", 0.02)
    monkeypatch.setattr(pool, "connect", lambda host, timeout: http.client.HTTPConnection(host, timeout=timeout))
    server = uvicorn.Server(uvicorn.Config(create_app(ears=FakeEars), host="127.0.0.1", port=0, ws="none", log_config=None))
    running = threading.Thread(target=server.run, daemon=True)
    running.start()
    try:
        until(lambda: server.started)
        body = json.dumps({"text": WORDS}).encode()
        phone = socket.create_connection(server.servers[0].sockets[0].getsockname()[:2])
        phone.sendall(b"POST /speech HTTP/1.1\r\nHost: ojo\r\nX-Token: " + TOKEN.encode() + b"\r\nContent-Type: application/json\r\n"
                      b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        assert phone.recv(65536).startswith(b"HTTP/1.1 200")
        phone.close()  # The person spoke over OjO: the phone drops the rest of the line.
        hung_up = time.monotonic()
        until(lambda: LocalEleven.deleted == ["/v1/history/LocalLine0001"])
        assert time.monotonic() - hung_up < 1.5 and voice.history.lines == {}
    finally:
        server.should_exit = True
        running.join(10)
        eleven.shutdown()
        eleven.server_close()


def test_the_log_holds_neither_the_key_nor_the_words(backend, eleven, caplog):
    caplog.set_level(logging.DEBUG)
    fake = eleven(deletions=[REFUSED, TimeoutError(), NOT_FOUND, REFUSED, REFUSED])
    session_id = new_session(backend.client)
    run_out(backend.client, fake)
    backend.client.post("/sessions/" + session_id + "/end", headers=AUTH)
    until(lambda: voice.history.lines == {})
    assert [record.getMessage() for record in caplog.records if record.name == "app.voice"] == [
        "Voice history: a line is not deleted yet, 401 missing_permissions. It is tried again when the call ends.",
        "Voice history: a line was not deleted, 401 missing_permissions.",
    ]
    for record in caplog.records:
        assert KEY not in record.getMessage() and WORDS not in record.getMessage() and fake.given[0] not in record.getMessage()
