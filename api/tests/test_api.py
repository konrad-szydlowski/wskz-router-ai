"""HTTP surface: validation, readiness, Swagger location, safe mail headers, bounded LLM output."""

import logging
from email import message_from_bytes

import pytest

from app.agent import build_agent
from app.mailer import OutgoingMail, SmtpMailer, clean_subject
from app.model_manager import ModelManager, ModelState

from .conftest import SENDER, post, scripted, tool_call


def test_swagger_is_served_under_api_v1_docs(make_client):
    client, _ = make_client(scripted(tool_call("other@example.com")))
    assert client.get("/api/v1/docs").status_code == 200
    spec = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/messages" in spec["paths"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": SENDER, "message": ""},
        {"email": SENDER, "message": "   "},
        {"email": SENDER, "message": "x" * 5001},
        {"email": "to-nie-jest-email", "message": "Cześć"},
        {"message": "Brak adresu"},
        {"email": SENDER, "message": "ok", "to": "ceo@example.com"},
    ],
)
def test_invalid_input_is_422_and_never_reaches_the_model(make_client, payload):
    model = scripted(tool_call("other@example.com"))
    client, mailer = make_client(model)
    r = client.post("/api/v1/messages", json=payload)
    assert r.status_code == 422
    assert model.function.calls["n"] == 0
    assert mailer.sent == []


def test_request_while_model_downloads_is_503_with_progress(make_client):
    models = ModelManager("http://ollama.invalid", "test-model")
    models.status = ModelState("downloading", progress=0.425, detail="pobieranie")
    models.run_forever = _never  # type: ignore[method-assign]
    client, mailer = make_client(scripted(tool_call("it@example.com")), models=models)
    r = post(client)
    assert r.status_code == 503
    assert r.json()["error"] == "model_not_ready"
    assert r.json()["model_state"]["progress_percent"] == 42.5
    assert mailer.sent == []
    health = client.get("/api/v1/health")
    assert health.status_code == 503
    assert health.json()["status"] == "starting"


def test_health_is_200_when_model_and_smtp_ready(make_client):
    client, _ = make_client(scripted(tool_call("it@example.com")))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("subject", ["Awaria\r\nBcc: attacker@evil.example", "Awaria\nX-Injected: 1"])
def test_subject_cannot_inject_mail_headers(subject):
    mail = OutgoingMail(to="it@example.com", reply_to=SENDER, subject=subject, body="treść")
    raw = SmtpMailer("localhost", 1025, "ai-router@example.com").build(mail).as_bytes()
    parsed = message_from_bytes(raw)
    assert parsed["Bcc"] is None and parsed["X-Injected"] is None
    assert parsed["Reply-To"] == SENDER
    assert "\n" not in parsed["Subject"]


def test_empty_or_huge_subject_is_normalised():
    assert clean_subject("   ") == "Zgłoszenie"
    assert len(clean_subject("a" * 500)) == 120


def test_llm_output_and_time_are_bounded(settings):
    agent = build_agent(settings)
    assert agent.model_settings["max_tokens"] == 256
    assert agent.model_settings["temperature"] == 0
    client = agent.model.client  # type: ignore[union-attr]
    assert client.timeout == settings.llm_timeout and client.max_retries == 0


async def _never():
    return None


def test_log_has_metadata_but_no_personal_data(make_client, caplog):
    client, _ = make_client(scripted(tool_call("kadry@example.com", "Numer konta do wypłaty")))
    with caplog.at_level(logging.INFO):
        r = post(client, "Zmieniłam bank, nowy numer konta 12 1020 0000 1111 2222 3333 4444.")
    assert r.status_code == 200, r.text
    assert "routed to=kadry@example.com" in caplog.text
    assert SENDER not in caplog.text
    assert "1111 2222" not in caplog.text
