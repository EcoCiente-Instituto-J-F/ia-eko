from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalendarStatus(str, Enum):
    AGENDADO = "AGENDADO"
    CONFIRMADO = "CONFIRMADO"
    RECUSADO = "RECUSADO"
    CANCELADO = "CANCELADO"
    REALIZADO = "REALIZADO"


class CalendarFilters(BaseModel):
    """Filtros aceitos pelos controllers reais da ds-calendario-api."""

    status: CalendarStatus | None = None
    data_inicio: datetime | None = None
    data_fim: datetime | None = None
    possui_recorrencia: bool | None = None
    condominio_id: int | None = Field(default=None, gt=0)
    cooperativa_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_period(self) -> "CalendarFilters":
        if self.data_inicio is not None and self.data_fim is not None and self.data_fim < self.data_inicio:
            raise ValueError("data_fim deve ser igual ou posterior a data_inicio")
        return self

    def to_query_params(self, *, include_status: bool = True) -> dict[str, str]:
        params: dict[str, str] = {}
        if include_status and self.status is not None:
            params["status"] = self.status.value
        if self.data_inicio is not None:
            params["dataInicio"] = self.data_inicio.isoformat()
        if self.data_fim is not None:
            params["dataFim"] = self.data_fim.isoformat()
        if self.possui_recorrencia is not None:
            params["possuiRecorrencia"] = str(self.possui_recorrencia).lower()
        if self.condominio_id is not None:
            params["condominioId"] = str(self.condominio_id)
        if self.cooperativa_id is not None:
            params["cooperativaId"] = str(self.cooperativa_id)
        return params


class Agendamento(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    condominio_id: int = Field(alias="condominioId")
    cooperativa_id: int = Field(alias="cooperativaId")
    data_inicio: datetime = Field(alias="dataInicio")
    data_fim: datetime | None = Field(default=None, alias="dataFim")
    status_agendamento: CalendarStatus = Field(alias="statusAgendamento")
    possui_recorrencia: bool = Field(alias="possuiRecorrencia")


class AgendamentoPage(BaseModel):
    """Campos estáveis do Page<CalendarioResponseDto> serializado pelo Spring."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    content: list[Agendamento] = Field(default_factory=list)
    total_elements: int = Field(default=0, alias="totalElements")
    total_pages: int = Field(default=0, alias="totalPages")
    size: int = 10
    number: int = 0
    first: bool = True
    last: bool = True
    number_of_elements: int = Field(default=0, alias="numberOfElements")
    empty: bool = True
    pageable: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None


class CalendarApiValidationError(BaseModel):
    field: str
    message: str


class CalendarApiErrorResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: int
    codigo_error: str = Field(alias="codigoError")
    details: list[CalendarApiValidationError] = Field(default_factory=list)


class CalendarToolError(BaseModel):
    code: str
    message: str
    http_status: int | None = None


class ListarAgendamentosToolResult(BaseModel):
    ok: bool
    agendamentos: list[Agendamento] = Field(default_factory=list)
    page: int = 0
    size: int = 10
    total_elements: int = 0
    total_pages: int = 0
    first: bool = True
    last: bool = True
    error: CalendarToolError | None = None


class BuscarProximaColetaToolResult(BaseModel):
    ok: bool
    found: bool = False
    agendamento: Agendamento | None = None
    error: CalendarToolError | None = None
