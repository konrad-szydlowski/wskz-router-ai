"""Request/response models — they also drive the Swagger documentation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class MessageIn(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="ignore",  # "co najmniej dwa parametry": extra fields are accepted and ignored
        json_schema_extra={
            "examples": [
                {"email": "jan.kowalski@firma.pl", "message": "Chciałbym zgłosić urlop od poniedziałku do piątku."},
                {"email": "anna@firma.pl", "message": "Drukarka na drugim piętrze znowu się zacięła."},
            ]
        },
    )
    email: EmailStr = Field(description="Adres nadawcy — trafi do nagłówka Reply-To.")
    message: str = Field(min_length=1, max_length=5000, description="Treść wiadomości (1–5000 znaków).")


class MessageOut(BaseModel):
    status: Literal["sent"]
    department: str = Field(description="Adres działu wybrany przez agenta.")
    reply_to: str
    subject: str = Field(description="Temat maila napisany przez agenta.")
    sent_by: Literal["agent_tool_call"] = Field(description="Mail zawsze wysyła narzędzie agenta, nigdy kod API.")
    nudges: int = Field(description="Ile razy trzeba było przypomnieć modelowi o wywołaniu narzędzia.")
    seconds: float


class ErrorOut(BaseModel):
    error: str
    detail: str
    model_state: dict | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "starting"]
    model: str
    model_state: dict
    smtp: Literal["ok", "unreachable"]


ERROR_RESPONSES: dict = {
    502: {"model": ErrorOut, "description": "Agent nie wywołał narzędzia mimo przypomnień — nic nie wysłano."},
    503: {"model": ErrorOut, "description": "Model się pobiera/ładuje albo SMTP/Ollama niedostępne — nic nie wysłano."},
    504: {"model": ErrorOut, "description": "Model nie odpowiedział w limicie czasu — nic nie wysłano."},
}
