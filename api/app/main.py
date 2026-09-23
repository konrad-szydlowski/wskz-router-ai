"""HTTP API: POST /api/v1/messages -> the agent picks a department and sends the mail via its tool."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError

from .agent import AgentDidNotSend, RouteDeps, build_agent, route
from .config import Settings
from .config import settings as default_settings
from .mailer import Mailer, MailerError, SmtpMailer
from .model_manager import ModelManager
from .schemas import ERROR_RESPONSES, ErrorOut, HealthOut, MessageIn, MessageOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("router.api")

API = "/api/v1"


def error(status: int, code: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ErrorOut(error=code, detail=detail, **extra).model_dump(exclude_none=True)
    )


def create_app(
    settings: Settings = default_settings,
    *,
    agent: Agent | None = None,
    mailer: Mailer | None = None,
    models: ModelManager | None = None,
) -> FastAPI:
    agent = agent or build_agent(settings)
    mailer = mailer or SmtpMailer(settings.smtp_host, settings.smtp_port, settings.mail_from)
    models = models or ModelManager(settings.ollama_url, settings.ollama_model)
    # One LLM call at a time: a CPU-only Ollama serialises requests anyway; this keeps latency honest
    # and prevents a burst of requests from piling up inside Ollama.
    llm_slot = asyncio.Semaphore(1)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(models.run_forever()) if not models.status.ready else None
        yield
        if task:
            task.cancel()

    app = FastAPI(
        title="WSKZ AI message router",
        version="1.0.0",
        description=(
            "Przyjmuje wiadomość od pracownika; agent AI (lokalny model w Ollamie) wybiera dział "
            "i **sam wysyła maila przez narzędzie** `send_email`. Reply-To = adres nadawcy."
        ),
        docs_url=f"{API}/docs",
        redoc_url=None,
        openapi_url=f"{API}/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(f"{API}/docs")

    @app.get(f"{API}/health", response_model=HealthOut, responses={503: {"model": HealthOut}}, tags=["system"])
    async def health() -> JSONResponse:
        """Gotowość: model pobrany i załadowany + serwer SMTP odpowiada. 503 dopóki nie."""
        smtp_ok = await run_in_threadpool(_ping, mailer)
        ok = models.status.ready and smtp_ok
        body = HealthOut(
            status="ok" if ok else "starting",
            model=settings.ollama_model,
            model_state=models.status.as_dict(),
            smtp="ok" if smtp_ok else "unreachable",
        )
        return JSONResponse(status_code=200 if ok else 503, content=body.model_dump())

    @app.post(
        f"{API}/messages",
        response_model=MessageOut,
        responses=ERROR_RESPONSES,
        tags=["messages"],
        summary="Przekaż wiadomość do właściwego działu",
    )
    async def send_message(payload: MessageIn):
        if not models.status.ready:
            return error(
                503,
                "model_not_ready",
                "Model AI jeszcze się przygotowuje, spróbuj za chwilę.",
                model_state=models.status.as_dict(),
            )
        deps = RouteDeps(sender=str(payload.email), message=payload.message, mailer=mailer)
        started = time.perf_counter()
        try:
            async with llm_slot:
                result = await route(agent, deps)
        except AgentDidNotSend as exc:
            log.warning("agent did not call the tool: %s", exc)
            return error(502, "agent_did_not_send", "Agent nie wywołał narzędzia wysyłki; nic nie wysłano.")
        except MailerError as exc:
            log.error("smtp failure: %s", exc)
            return error(503, "mail_server_unavailable", "Serwer poczty nie przyjął wiadomości; nic nie wysłano.")
        except ModelAPIError as exc:
            if isinstance(exc.__cause__, openai.APITimeoutError):
                return error(
                    504, "model_timeout", f"Model nie odpowiedział w {settings.llm_timeout} s; nic nie wysłano."
                )
            log.error("ollama failure: %s", exc)
            return error(503, "model_unavailable", "Serwer modelu AI nie odpowiada; nic nie wysłano.")
        elapsed = round(time.perf_counter() - started, 2)
        # Log metadata only — the message body is personal data and stays out of logs.
        log.info("routed to=%s nudges=%s seconds=%s", result.mail.to, result.nudges, elapsed)
        return MessageOut(
            status="sent",
            department=result.mail.to,
            reply_to=result.mail.reply_to,
            subject=result.mail.subject,
            sent_by="agent_tool_call",
            nudges=result.nudges,
            seconds=elapsed,
        )

    return app


def _ping(mailer: Mailer) -> bool:
    ping = getattr(mailer, "ping", None)
    return bool(ping()) if ping else True


app = create_app()
