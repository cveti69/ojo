"""The page is not public until the word is given: by default it does not exist."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from conftest import AUTH

BACKEND = Path(__file__).resolve().parents[1]


def setting(*landing):
    environment = {name: value for name, value in os.environ.items() if name != "LANDING"}
    environment.update({"LANDING": value for value in landing})
    answer = subprocess.run([sys.executable, "-B", "-c", "from app import config; print(config.LANDING)"], cwd=BACKEND, env=environment,
                            capture_output=True, text=True, check=True)
    return answer.stdout.strip()


def test_a_server_with_no_word_about_it_has_no_page():
    assert setting() == "False"
    assert config.LANDING is False


@pytest.mark.parametrize("value", ["", "off", "1", "true", "yes", "ON"])
def test_only_the_exact_word_turns_it_on(value):
    assert setting(value) == "False"


def test_the_exact_word_does():
    assert setting("on") == "True"


@pytest.mark.parametrize("method, path", [("get", "/"), ("get", "/beta"), ("post", "/beta"), ("head", "/")])
def test_without_a_token_it_is_closed_like_any_other_path(backend, method, path):
    assert getattr(TestClient(backend.app, follow_redirects=False), method)(path).status_code == 401


@pytest.mark.parametrize("method, path", [("get", "/"), ("get", "/beta"), ("post", "/beta")])
def test_a_token_does_not_open_it(backend, method, path):
    response = getattr(backend.client, method)(path, headers=AUTH)
    assert response.status_code == 404 and "OjO" not in response.text


def test_the_form_keeps_nothing(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BETA_FILE", str(tmp_path / "beta.jsonl"))
    backend.client.post("/beta", headers=AUTH, data={"email": "someone@example.com"})
    TestClient(backend.app).post("/beta", data={"email": "someone@example.com"})
    assert not (tmp_path / "beta.jsonl").exists()


def test_the_calls_go_on_without_it(backend):
    assert TestClient(backend.app).get("/health").json() == {"ok": True}
    assert backend.client.post("/sessions", headers=AUTH, json={}).status_code == 201
