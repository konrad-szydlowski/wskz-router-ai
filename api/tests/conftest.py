"""Test fixtures: a fake SMTP mailer and a scripted LLM (pydantic-ai FunctionModel) — no Ollama needed."""

import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agent import build_agent
from app.config import Settings
from app.mailer import MailerError, OutgoingMail
from app.main import create_app
from app.model_manager import ModelManager, ModelState

SENDER = "jan.kowalski@firma.pl"


class FakeMailer:
    def __init__(self, fail: bool = False, delay: float = 0.0):
        self.sent: list[OutgoingMail] = []
        self.fail = fail
        self.delay = delay  # a slow SMTP server widens any race between concurrent tool calls

    def send(self, mail: OutgoingMail) -> None:
        time.sleep(self.delay)
        if self.fail:
            raise MailerError("connection refused")
        self.sent.append(mail)

    def ping(self) -> bool:
        return not self.fail


def tool_call(to: str, subject: str = "Zgłoszenie", **extra) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name="send_email", args={"to": to, "subject": subject, **extra})])


def text(content: str = "Chętnie pomogę, ale potrzebuję więcej informacji.") -> ModelResponse:
    return ModelResponse(parts=[TextPart(content)])


def scripted(*responses: ModelResponse) -> FunctionModel:
    """LLM that returns the given responses in order (the last one repeats)."""
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    fn.calls = calls  # type: ignore[attr-defined]
    return FunctionModel(fn)


def ready_models() -> ModelManager:
    m = ModelManager("http://ollama.invalid", "test-model")
    m.status = ModelState("ready")
    return m


@pytest.fixture
def settings() -> Settings:
    return Settings(ollama_url="http://ollama.invalid", ollama_model="test-model", tool_nudges=2)


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def make_client(settings, mailer) -> Callable:
    """make_client(model, models=None, mailer=None) -> (TestClient, mailer)."""

    def _make(model: FunctionModel, models: ModelManager | None = None, mail=None):
        agent = build_agent(settings)
        used_mailer = mail or mailer
        app = create_app(settings, agent=agent, mailer=used_mailer, models=models or ready_models())
        ctx = agent.override(model=model)
        ctx.__enter__()
        client = TestClient(app)
        client.__enter__()
        _open.append((client, ctx))
        return client, used_mailer

    _open: list = []
    yield _make
    for client, ctx in _open:
        client.__exit__(None, None, None)
        ctx.__exit__(None, None, None)


def post(client: TestClient, message: str = "Nie działa mi drukarka.", email: str = SENDER):
    return client.post("/api/v1/messages", json={"email": email, "message": message})
