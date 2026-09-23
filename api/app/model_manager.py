"""Makes sure the LLM weights are present and loaded, and reports progress while they are not.

The API starts immediately; this background task pulls the model through the Ollama HTTP API
(so the download progress is known) and warms it up. Until then requests get a 503 with the
current state instead of a connection error.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("router.model")


@dataclass
class ModelState:
    state: str = "starting"  # starting -> downloading -> loading -> ready | error (retried)
    progress: float | None = None  # 0..1 while downloading
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_dict(self) -> dict:
        out: dict = {"state": self.state}
        if self.progress is not None and self.state == "downloading":
            out["progress_percent"] = round(self.progress * 100, 1)
        if self.detail:
            out["detail"] = self.detail
        return out


class ModelManager:
    def __init__(self, ollama_url: str, model: str, retry_delay: float = 5.0, transport=None):
        self.url, self.model, self.retry_delay = ollama_url, model, retry_delay
        self._transport = transport  # tests inject httpx.MockTransport
        self.status = ModelState()

    async def run_forever(self) -> None:
        while not self.status.ready:
            try:
                async with httpx.AsyncClient(base_url=self.url, timeout=None, transport=self._transport) as client:
                    if not await self._is_present(client):
                        await self._pull(client)
                    self.status = ModelState("loading", detail="ładowanie modelu do pamięci")
                    # keep_alive=-1: the model stays in RAM, so the first user request is not a cold start
                    r = await client.post("/api/generate", json={"model": self.model, "prompt": "", "keep_alive": -1})
                    r.raise_for_status()
                self.status = ModelState("ready")
                log.info("model %s ready", self.model)
            except (httpx.HTTPError, ValueError) as exc:
                self.status = ModelState("error", detail=f"{type(exc).__name__}: {exc}; ponawiam")
                log.warning("model %s not ready: %s", self.model, exc)
                await asyncio.sleep(self.retry_delay)

    async def _is_present(self, client: httpx.AsyncClient) -> bool:
        r = await client.get("/api/tags", timeout=10)
        r.raise_for_status()
        names = {m.get("name") for m in r.json().get("models", [])}
        return self.model in names or f"{self.model}:latest" in names

    async def _pull(self, client: httpx.AsyncClient) -> None:
        self.status = ModelState("downloading", progress=0.0, detail=f"pobieranie {self.model}")
        async with client.stream("POST", "/api/pull", json={"model": self.model}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise ValueError(event["error"])
                if event.get("total") and event.get("completed") is not None:
                    self.status.progress = event["completed"] / event["total"]
                self.status.detail = f"pobieranie {self.model}: {event.get('status', '')}"
