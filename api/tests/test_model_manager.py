"""Cold start: the model is pulled through the Ollama API with visible progress, then warmed up."""

import asyncio
import json

import httpx

from app.model_manager import ModelManager


def ollama(present: bool, pull_lines: list[dict] | None = None, fail_first_tags: int = 0):
    calls: list[str] = []
    state = {"tags_failures": fail_first_tags}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/tags":
            if state["tags_failures"]:
                state["tags_failures"] -= 1
                raise httpx.ConnectError("ollama not up yet")
            models = [{"name": "qwen2.5:3b"}] if present else []
            return httpx.Response(200, json={"models": models})
        if request.url.path == "/api/pull":
            body = "\n".join(json.dumps(line) for line in pull_lines or [{"status": "success"}])
            return httpx.Response(200, content=body.encode())
        if request.url.path == "/api/generate":
            assert json.loads(request.content)["keep_alive"] == -1
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


def run(manager: ModelManager) -> None:
    asyncio.run(asyncio.wait_for(manager.run_forever(), timeout=5))


def test_present_model_is_only_warmed_up():
    transport, calls = ollama(present=True)
    m = ModelManager("http://ollama", "qwen2.5:3b", transport=transport)
    run(m)
    assert m.status.ready
    assert calls == ["GET /api/tags", "POST /api/generate"]  # no re-download on restart


def test_missing_model_is_pulled_with_progress_then_ready():
    lines = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "total": 1000, "completed": 250},
        {"status": "pulling abc", "total": 1000, "completed": 1000},
        {"status": "success"},
    ]
    transport, calls = ollama(present=False, pull_lines=lines)
    m = ModelManager("http://ollama", "qwen2.5:3b", transport=transport)
    run(m)
    assert m.status.ready
    assert calls == ["GET /api/tags", "POST /api/pull", "POST /api/generate"]


def test_pull_error_is_reported_and_retried():
    transport, calls = ollama(present=False, pull_lines=[{"error": "network unreachable"}])
    m = ModelManager("http://ollama", "qwen2.5:3b", retry_delay=0.01, transport=transport)

    async def observe():
        task = asyncio.create_task(m.run_forever())
        async with asyncio.timeout(5):  # wait for an event, not a fixed sleep: no flakiness on a busy CI runner
            while calls.count("POST /api/pull") < 2:
                await asyncio.sleep(0.005)
        task.cancel()

    asyncio.run(observe())
    assert m.status.state == "error"
    assert "network unreachable" in m.status.detail
    assert calls.count("POST /api/pull") >= 2  # keeps retrying instead of giving up


def test_ollama_not_up_yet_is_retried():
    transport, _ = ollama(present=True, fail_first_tags=2)
    m = ModelManager("http://ollama", "qwen2.5:3b", retry_delay=0.01, transport=transport)
    run(m)
    assert m.status.ready
