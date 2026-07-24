import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Optional, List
from pymongo import MongoClient
import redis as redis_lib
from langchain.tools import tool
from pydantic import BaseModel, Field

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
REDIS_URL = os.getenv("REDIS_URL")
SESSION_TTL_SECONDS = 1800  # 30 minutos, mesmo TTL do Redis e do índice do Mongo

_mongo_client = None
_redis_client = None

def get_mongo_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client["ecociente"]

def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

def _ptr_key(usuario_id: str) -> str:
    return f"session:ptr:{usuario_id}"

def _safe_close(*_args):
    pass  # placeholder — pymongo/redis usam pool de conexão, não fecham por request como o psycopg2




## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=
##                          AGENTE QUE VAI BUSCAR NA MEMORIA
## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=


class ObterContextoSessaoArgs(BaseModel):
    usuario_id: str = Field(..., description="ID do usuário autenticado na conversa atual.")

@tool("obter_contexto_sessao", args_schema=ObterContextoSessaoArgs)
def obter_contexto_sessao(usuario_id: str) -> dict:
    """
    Recupera o contexto da sessão ATIVA do usuário (memória de curto prazo).

    CAPACIDADES:
    - Consulta o ponteiro no Redis (session:ptr:{usuario_id}) para saber se existe
      sessão ativa.
    - Se existir, busca o documento correspondente na collection `sessoes` do Mongo
      e devolve o resumo parcial, a última rota utilizada e as últimas mensagens.

    QUANDO USAR:
    - Sempre, no início do processamento de qualquer mensagem nova do usuário —
      é o primeiro passo antes de decidir se a memória de longo prazo também
      precisa ser consultada.

    COMO INTERPRETAR:
    - "sessao_ativa": false → não havia conversa em andamento (usuário novo na
      sessão, ou ficou 30+ min inativo). Trate a mensagem como início de conversa.
    - "sessao_ativa": true → use "resumo_parcial" e "ultima_rota" para entender
      a continuidade da conversa.

    REGRAS IMPORTANTES:
    - Nunca invente contexto se sessao_ativa vier false — retorne isso ao Roteador
      exatamente como está, sem preencher com suposição.
    - Esta tool NUNCA escreve nada — é só leitura. Não tente usá-la para renovar TTL.

    LIMITAÇÕES:
    - Não devolve o array de mensagens inteiro, só as últimas (evita estourar
      contexto com histórico irrelevante).
    """
    r = get_redis()
    session_id = r.get(_ptr_key(usuario_id))

    if not session_id:
        return {
            "status": "ok",
            "sessao_ativa": False,
            "resumo_parcial": "",
            "ultima_rota": None,
            "mensagens_recentes": []
        }

    db = get_mongo_db()
    doc = db.sessoes.find_one({"_id": session_id, "usuario_id": usuario_id})

    if not doc:
        # Ponteiro existia no Redis mas o documento já não existe no Mongo
        # (ex.: TTLs levemente dessincronizados). Trata como sessão inexistente.
        return {
            "status": "ok",
            "sessao_ativa": False,
            "resumo_parcial": "",
            "ultima_rota": None,
            "mensagens_recentes": []
        }

    mensagens = doc.get("mensagens", [])
    ultima_rota = next(
        (m["agente"] for m in reversed(mensagens) if m.get("agente")),
        None
    )

    return {
        "status": "ok",
        "sessao_ativa": True,
        "session_id": session_id,
        "resumo_parcial": doc.get("resumo_parcial", ""),
        "ultima_rota": ultima_rota,
        "mensagens_recentes": mensagens[-6:]  # últimas 3 trocas, ajuste conforme necessidade
    }


class ObterMemoriaLongoPrazoArgs(BaseModel):
    usuario_id: str = Field(..., description="ID do usuário autenticado.")

@tool("obter_memoria_longo_prazo", args_schema=ObterMemoriaLongoPrazoArgs)
def obter_memoria_longo_prazo(usuario_id: str) -> dict:
    """
    Recupera o perfil permanente do usuário (memória de longo prazo).

    CAPACIDADES:
    - Busca o documento único do usuário na collection `memoria_longo_prazo`.

    QUANDO USAR:
    - Quando a mensagem atual sugerir que o histórico de longo prazo é relevante
      (ex.: pergunta sobre padrão de uso, preferência, ou quando o contexto da
      sessão atual sozinho não é suficiente).
    - NÃO é obrigatório chamar em toda mensagem — só quando fizer diferença
      para a resposta.

    COMO INTERPRETAR:
    - "memoria_existente": false → usuário sem histórico de longo prazo ainda
      (primeira vez interagindo, ou nunca gerou informação relevante o
      suficiente). Não é erro, é estado normal para usuário novo.

    REGRAS IMPORTANTES:
    - Nunca invente fatos ou padrões se memoria_existente vier false.
    - Esta tool NUNCA escreve nada — é só leitura.

    LIMITAÇÕES:
    - O documento é sempre compacto por design (limites de caracteres e de
      quantidade de tópicos), então não espere um histórico detalhado aqui —
      é um resumo, não um log.
    """
    db = get_mongo_db()
    doc = db.memoria_longo_prazo.find_one({"_id": usuario_id})

    if not doc:
        return {
            "status": "ok",
            "memoria_existente": False,
            "fatos_estaveis": "",
            "padroes_comportamento": "",
            "topicos_recorrentes": [],
            "total_sessoes": 0
        }

    return {
        "status": "ok",
        "memoria_existente": True,
        "fatos_estaveis": doc.get("fatos_estaveis", ""),
        "padroes_comportamento": doc.get("padroes_comportamento", ""),
        "topicos_recorrentes": doc.get("topicos_recorrentes", []),
        "total_sessoes": doc.get("total_sessoes", 0),
        "ultima_interacao_em": str(doc.get("ultima_interacao_em", ""))
    }


TOOLS_RECUPERACAO = [obter_contexto_sessao, obter_memoria_longo_prazo]

## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=
##                                     fim
## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=


## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=
##                                 AGENTE CONSOLIDADOR
## -=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-==---=-=-=-=-=-=-==-=-=-=


class RegistrarMensagemArgs(BaseModel):
    usuario_id: str = Field(..., description="ID do usuário.")
    role: str = Field(..., description="'usuario' ou 'assistente'.")
    agente: Optional[str] = Field(default=None, description="Nome do especialista que respondeu (coletas, educador, analytics, faq), ou null se role='usuario'.")
    content: str = Field(..., description="Conteúdo da mensagem.")

@tool("registrar_mensagem", args_schema=RegistrarMensagemArgs)
def registrar_mensagem(usuario_id: str, role: str, agente: Optional[str], content: str) -> dict:
    """
    Grava uma mensagem na sessão ativa do usuário, criando a sessão se ainda não existir,
    e renova o TTL deslizante (Redis + Mongo).

    CAPACIDADES:
    - Se não existir sessão ativa (ponteiro ausente no Redis), cria uma nova sessão
      com um session_id novo e grava o ponteiro no Redis.
    - Insere a mensagem no array `mensagens` do documento em `sessoes`.
    - Renova o TTL de 30 minutos tanto no Redis quanto no campo `atualizada_em`
      do Mongo (o índice TTL do Mongo é baseado nesse campo).

    QUANDO USAR:
    - A cada mensagem trocada na conversa (tanto a do usuário quanto a resposta
      do especialista), para manter o histórico de curto prazo atualizado.

    REGRAS IMPORTANTES:
    - Nunca chame esta tool para "corrigir" uma mensagem já enviada — ela só
      adiciona, nunca edita ou remove.
    - O campo `agente` deve ser preenchido apenas quando role='assistente'.

    LIMITAÇÕES:
    - Não atualiza o `resumo_parcial` — isso é feito por `atualizar_resumo_sessao`.
    """
    r = get_redis()
    db = get_mongo_db()
    ptr_key = _ptr_key(usuario_id)

    session_id = r.get(ptr_key)
    agora = datetime.now(timezone.utc)

    if not session_id:
        session_id = str(uuid.uuid4())
        db.sessoes.insert_one({
            "_id": session_id,
            "usuario_id": usuario_id,
            "iniciada_em": agora,
            "atualizada_em": agora,
            "resumo_parcial": "",
            "mensagens": []
        })

    db.sessoes.update_one(
        {"_id": session_id},
        {
            "$push": {"mensagens": {
                "role": role,
                "agente": agente,
                "content": content,
                "timestamp": agora
            }},
            "$set": {"atualizada_em": agora}
        }
    )

    r.set(ptr_key, session_id, ex=SESSION_TTL_SECONDS)

    return {"status": "ok", "session_id": session_id}


class AtualizarResumoSessaoArgs(BaseModel):
    usuario_id: str = Field(..., description="ID do usuário.")
    resumo_parcial: str = Field(..., description="Resumo atualizado e compacto da conversa até agora.")

@tool("atualizar_resumo_sessao", args_schema=AtualizarResumoSessaoArgs)
def atualizar_resumo_sessao(usuario_id: str, resumo_parcial: str) -> dict:
    """
    Atualiza o resumo parcial da sessão ativa, sem encerrá-la.

    QUANDO USAR:
    - Após cada troca de mensagens, para manter `resumo_parcial` coerente com
      o que já foi conversado — isso é o que o Agente de Recuperação vai ler
      na próxima mensagem do usuário.

    REGRAS IMPORTANTES:
    - resumo_parcial deve ser a versão reescrita e atualizada do resumo,
      nunca o texto antigo com algo colado no final.
    - Esta tool não mexe na memória de longo prazo — para isso, use
      `consolidar_memoria_longo_prazo`.
    """
    db = get_mongo_db()
    r = get_redis()
    session_id = r.get(_ptr_key(usuario_id))

    if not session_id:
        return {"status": "error", "message": "Não há sessão ativa para atualizar."}

    db.sessoes.update_one(
        {"_id": session_id},
        {"$set": {"resumo_parcial": resumo_parcial, "atualizada_em": datetime.now(timezone.utc)}}
    )
    return {"status": "ok"}


class TopicoRecorrente(BaseModel):
    topico: str
    frequencia: int = Field(..., ge=1)

class ConsolidarMemoriaLongoPrazoArgs(BaseModel):
    usuario_id: str = Field(..., description="ID do usuário.")
    fatos_estaveis: str = Field(..., description="Texto reescrito e completo (máx. ~300 caracteres).")
    padroes_comportamento: str = Field(..., description="Texto reescrito e completo (máx. ~500 caracteres).")
    topicos_recorrentes: List[TopicoRecorrente] = Field(..., description="Lista final, máximo 10 itens, já deduplicada.")
    encerrar_sessao: bool = Field(..., description="Se true, remove o ponteiro no Redis e o documento em `sessoes` após consolidar.")

@tool("consolidar_memoria_longo_prazo", args_schema=ConsolidarMemoriaLongoPrazoArgs)
def consolidar_memoria_longo_prazo(
    usuario_id: str,
    fatos_estaveis: str,
    padroes_comportamento: str,
    topicos_recorrentes: List[TopicoRecorrente],
    encerrar_sessao: bool
) -> dict:
    """
    Grava a versão compactada e atualizada da memória de longo prazo do usuário.

    CAPACIDADES:
    - Substitui (nunca concatena) os campos fatos_estaveis, padroes_comportamento
      e topicos_recorrentes pelo conteúdo já reescrito recebido como argumento.
    - Incrementa total_sessoes e atualiza ultima_interacao_em.
    - Se encerrar_sessao=true, apaga o ponteiro no Redis e o documento em
      `sessoes` — a sessão foi processada e não precisa mais existir.

    QUANDO USAR:
    - Apenas quando a sessão está sendo encerrada de fato (ponteiro do Redis
      expirou, ou logout explícito) E o Consolidador identificou algo relevante
      o suficiente para atualizar o perfil permanente.
    - Se nada relevante foi identificado nesta sessão, NÃO chame esta tool —
      apenas deixe a sessão expirar sozinha (TTL cuida disso).

    REGRAS IMPORTANTES:
    - Os textos recebidos aqui já devem estar reescritos e compactos — esta
      tool não faz merge de texto, só grava o que foi passado. A reescrita
      (juntar o que já existia com o que é novo, sem duplicar) é responsabilidade
      do agente antes de chamar esta tool.
    - topicos_recorrentes deve ter no máximo 10 itens — a tool rejeita listas maiores.

    LIMITAÇÕES:
    - Não faz merge automático — grava exatamente o que foi enviado. Certifique-se
      de ter lido a memória existente com `obter_memoria_longo_prazo` (ou o
      equivalente já fornecido no contexto) antes de reescrever.
    """
    if len(topicos_recorrentes) > 10:
        return {"status": "error", "message": "topicos_recorrentes excede o limite de 10 itens."}

    db = get_mongo_db()
    agora = datetime.now(timezone.utc)

    db.memoria_longo_prazo.update_one(
        {"_id": usuario_id},
        {
            "$set": {
                "fatos_estaveis": fatos_estaveis[:300],
                "padroes_comportamento": padroes_comportamento[:500],
                "topicos_recorrentes": [t.dict() for t in topicos_recorrentes],
                "ultima_interacao_em": agora
            },
            "$inc": {"total_sessoes": 1}
        },
        upsert=True
    )

    if encerrar_sessao:
        r = get_redis()
        session_id = r.get(_ptr_key(usuario_id))
        r.delete(_ptr_key(usuario_id))
        if session_id:
            db.sessoes.delete_one({"_id": session_id})

    return {"status": "ok"}


TOOLS_CONSOLIDADOR = [
    obter_contexto_sessao,          
    obter_memoria_longo_prazo,      
    registrar_mensagem,
    atualizar_resumo_sessao,
    consolidar_memoria_longo_prazo,
]


# END