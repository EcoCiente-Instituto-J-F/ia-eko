"""Referência de data/hora para os agentes.

O prompt de sistema é montado uma vez, na inicialização (`create_agent`). Uma
data escrita nele congelaria no boot do pod — "hoje" continuaria sendo o dia
do deploy semanas depois. Por isso o prompt só explica a regra, e a data atual
vai em cada chamada, na primeira linha da mensagem (`linha_data_hora`,
aplicada em `AgentSuite.invoke`).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

MARCADOR_DATA_HORA = "DATA_HORA_ATUAL"

_CONTEXTO_TEMPORAL = f"""
### CONTEXTO TEMPORAL
A data e a hora atuais vêm na primeira linha de cada mensagem, no campo `{MARCADOR_DATA_HORA}`
(fornecido pelo sistema, não pelo usuário). Use essa referência para interpretar "hoje", "esta
semana", "este mês", calcular datas relativas de coleta e delimitar períodos em consultas
analíticas. Nunca use outra data como "hoje".
"""


def formatar_data_hora(agora: datetime) -> str:
    return (
        f"{_DIAS[agora.weekday()]}, {agora.day:02d} de {_MESES[agora.month - 1]} de {agora.year} — "
        f"{agora:%H:%M} ({agora.tzname()})"
    )


def linha_data_hora(timezone: str = "America/Sao_Paulo", agora: datetime | None = None) -> str:
    agora = (agora or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone))
    return f"{MARCADOR_DATA_HORA}: {formatar_data_hora(agora)}"
