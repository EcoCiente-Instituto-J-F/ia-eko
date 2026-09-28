"""Carga do FAQ canônico no Qdrant (usado com FAQ_BACKEND=qdrant).

Fonte: a seção "Perguntas Frequentes Canônicas" de
`data/FAQ_KNOWLEDGE_BASE.md` — cada item `#### FAQ-NNN — <pergunta>` com
Intenção, Perfis relacionados, Resposta canônica e Palavras-chave. A
`resposta_canonica` é o texto que o chat devolve sem passar por LLM, então só
entra na coleção o que está escrito ali, sem reescrita.

Cada execução:

1. lê e valida todos os FAQs (qualquer item malformado aborta a carga: indexar
   metade da base em silêncio é pior que não indexar);
2. cria a coleção se não existir, com a dimensão do modelo de embedding;
3. só gera embedding dos FAQs novos ou alterados (hash do conteúdo no
   payload) — sem mudanças, o modelo de ~2 GB nem é carregado;
4. faz upsert com id determinístico (uuid5 do faq_id), então rodar de novo
   não duplica nada;
5. remove da coleção os FAQs canônicos que saíram do arquivo (só pontos com
   tipo=faq_canonica; qualquer outro conteúdo da coleção fica intacto).

Uso:
    python -m src.etl.populate_qdrant                  # carga incremental
    python -m src.etl.populate_qdrant --dry-run        # só valida o arquivo
    python -m src.etl.populate_qdrant --recriar        # apaga e recria a coleção
    python -m src.etl.populate_qdrant --testar "como faço login?" "o que é o app?"

Troca de QDRANT_EMBEDDING_MODEL exige --recriar: vetores de modelos
diferentes não são comparáveis.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.security.roles import (
    COOPERATIVA,
    MORADOR_RESIDENCIAL,
    SINDICO_COMERCIAL,
    SINDICO_RESIDENCIAL,
    USUARIO_COMERCIAL,
    USUARIO_COMUM,
)

logger = logging.getLogger("ecociente.etl.qdrant")

TIPO_FAQ = "faq_canonica"
# Namespace fixo: o id do ponto depende só do faq_id.
_NAMESPACE = uuid.UUID("5b1c6f0e-3f7a-4c52-9d0b-ec0c1e47a001")
_SECAO = re.compile(r"^##\s+\d+\.\s+Perguntas Frequentes Can[oô]nicas\s*$", re.IGNORECASE)
_ITEM = re.compile(r"^####\s+(FAQ-\d+)\s+[—–-]\s+(.+?)\s*$")
_CAMPO = re.compile(r"^\*\*(.+?):\*\*\s*(.*?)\s*$")

_CAMPOS = {
    "intencao": "intencao",
    "perfis relacionados": "perfis",
    "resposta canonica": "resposta_canonica",
    "palavras-chave": "palavras_chave",
}
_OBRIGATORIOS = ("intencao", "perfis", "resposta_canonica")

# Rótulos do documento → perfis do sistema. Rótulos descritivos ("Todos",
# "perfis com mapa", "perfis autorizados"...) não restringem: a resposta é
# documentação pública, e a permissão dos DADOS é aplicada pelos outros agentes.
_PERFIS = {
    "usuario comum": [USUARIO_COMUM],
    "morador residencial": [MORADOR_RESIDENCIAL],
    "residencial": [MORADOR_RESIDENCIAL],
    "usuario comercial": [USUARIO_COMERCIAL],
    "sindico residencial": [SINDICO_RESIDENCIAL],
    "sindico comercial": [SINDICO_COMERCIAL],
    "sindicos": [SINDICO_RESIDENCIAL, SINDICO_COMERCIAL],
    "sindico": [SINDICO_RESIDENCIAL, SINDICO_COMERCIAL],
    "cooperativa": [COOPERATIVA],
    "cooperativas": [COOPERATIVA],
}


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def normalizar_perfis(texto: str) -> list[str]:
    """"Morador Residencial, Síndicos" → [MORADOR_RESIDENCIAL, SINDICO_*].
    Lista vazia = vale para todos. Se QUALQUER rótulo for descritivo/"Todos",
    o FAQ fica aberto — restringir por um rótulo que não entendemos
    esconderia a resposta de quem deveria vê-la."""
    perfis: list[str] = []
    for parte in texto.split(","):
        chave = _sem_acento(parte)
        if not chave:
            continue
        mapeado = _PERFIS.get(chave)
        if mapeado is None:
            return []
        for perfil in mapeado:
            if perfil not in perfis:
                perfis.append(perfil)
    return perfis


@dataclass(frozen=True)
class FaqCanonico:
    faq_id: str
    titulo: str
    categoria: str
    intencao: str
    perfis_texto: str
    perfis_relacionados: list[str]
    resposta_canonica: str
    palavras_chave: list[str] = field(default_factory=list)

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(_NAMESPACE, self.faq_id))

    def texto_embedding(self) -> str:
        # Pergunta + resposta + palavras-chave: a pergunta do usuário costuma
        # parecer com o título; a resposta e as palavras-chave cobrem sinônimos.
        partes = [self.titulo, self.resposta_canonica]
        if self.palavras_chave:
            partes.append("Palavras-chave: " + ", ".join(self.palavras_chave))
        return "\n".join(partes)

    def payload(self, embedding_model: str) -> dict[str, Any]:
        # Mesmos campos que QdrantFaqService.buscar_faq lê, mais rastreio.
        base = {
            "faq_id": self.faq_id,
            "titulo": self.titulo,
            "intencao": self.intencao,
            "perfis_relacionados": self.perfis_relacionados,
            "resposta_canonica": self.resposta_canonica,
            "palavras_chave": self.palavras_chave,
            "tipo": TIPO_FAQ,
            "categoria": self.categoria,
            "perfis_texto": self.perfis_texto,
            "embedding_model": embedding_model,
        }
        base["content_hash"] = content_hash(base)
        return base


def content_hash(payload: dict[str, Any]) -> str:
    relevante = {k: v for k, v in payload.items() if k != "content_hash"}
    return hashlib.sha256(json.dumps(relevante, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class FaqParseError(ValueError):
    pass


def parse_faq_canonicas(texto: str) -> list[FaqCanonico]:
    linhas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    inicio = next((i for i, linha in enumerate(linhas) if _SECAO.match(linha.strip())), None)
    if inicio is None:
        raise FaqParseError("Seção 'Perguntas Frequentes Canônicas' não encontrada.")

    itens: list[dict[str, Any]] = []
    categoria = ""
    atual: dict[str, Any] | None = None
    campo_atual: str | None = None
    for linha in linhas[inicio + 1 :]:
        crua = linha.rstrip()
        limpa = crua.strip()
        if limpa.startswith("## "):
            break  # próxima seção de nível 2
        if limpa.startswith("### "):
            categoria = limpa[4:].strip()
            atual, campo_atual = None, None
            continue
        item = _ITEM.match(limpa)
        if item:
            atual = {"faq_id": item.group(1), "titulo": item.group(2), "categoria": categoria}
            itens.append(atual)
            campo_atual = None
            continue
        if atual is None:
            continue
        campo = _CAMPO.match(limpa)
        if campo:
            nome = _CAMPOS.get(_sem_acento(campo.group(1)))
            campo_atual = nome
            if nome:
                atual[nome] = campo.group(2)
            continue
        if not limpa:
            campo_atual = None
            continue
        if campo_atual:  # continuação do campo na linha seguinte
            atual[campo_atual] = f"{atual[campo_atual]} {limpa}".strip()

    problemas: list[str] = []
    vistos: set[str] = set()
    faqs: list[FaqCanonico] = []
    for bruto in itens:
        faq_id = bruto["faq_id"]
        if faq_id in vistos:
            problemas.append(f"{faq_id}: id duplicado")
        vistos.add(faq_id)
        faltando = [nome for nome in _OBRIGATORIOS if not str(bruto.get(nome, "")).strip()]
        if faltando:
            problemas.append(f"{faq_id}: sem {', '.join(faltando)}")
            continue
        palavras = [p.strip() for p in str(bruto.get("palavras_chave", "")).split(",") if p.strip()]
        faqs.append(
            FaqCanonico(
                faq_id=faq_id,
                titulo=bruto["titulo"],
                categoria=bruto["categoria"],
                intencao=bruto["intencao"].strip(),
                perfis_texto=bruto["perfis"].strip(),
                perfis_relacionados=normalizar_perfis(bruto["perfis"]),
                resposta_canonica=bruto["resposta_canonica"].strip(),
                palavras_chave=palavras,
            )
        )
    if not itens:
        problemas.append("nenhum item '#### FAQ-NNN — pergunta' encontrado")
    if problemas:
        raise FaqParseError("FAQ canônico inválido:\n- " + "\n- ".join(problemas))
    return faqs


@dataclass
class CargaResultado:
    total: int = 0
    inseridos: int = 0
    atualizados: int = 0
    inalterados: int = 0
    removidos: int = 0
    colecao_criada: bool = False
    dimensao: int | None = None
    duracao_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class DimensaoIncompativel(RuntimeError):
    pass


class QdrantFaqLoader:
    def __init__(self, settings: Settings, *, client: Any = None, embedding_service: Any = None):
        self.settings = settings
        self.collection = settings.qdrant_collection
        self._client = client
        self._embedder = embedding_service

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.settings.qdrant_url:
                raise RuntimeError("QDRANT_URL não configurada.")
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
                timeout=int(self.settings.qdrant_timeout),
            )
        return self._client

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from src.services.embedding_service import EmbeddingService

            self._embedder = EmbeddingService(self.settings.qdrant_embedding_model)
        return self._embedder

    async def _embed(self, textos: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embedder.gerar_embeddings_documentos, textos)

    async def _dimensao_colecao(self) -> int | None:
        if not await self.client.collection_exists(self.collection):
            return None
        info = await self.client.get_collection(self.collection)
        vetores = info.config.params.vectors
        size = getattr(vetores, "size", None)
        if size is None and isinstance(vetores, dict) and vetores:
            raise DimensaoIncompativel(
                f"A coleção '{self.collection}' usa vetores nomeados {sorted(vetores)}; "
                "o FAQ usa um vetor único. Use outra coleção ou --recriar."
            )
        return int(size)

    async def _hashes_existentes(self, faqs: list[FaqCanonico]) -> dict[str, str]:
        pontos = await self.client.retrieve(
            self.collection, ids=[faq.point_id for faq in faqs], with_payload=True, with_vectors=False
        )
        return {str(p.id): str((p.payload or {}).get("content_hash", "")) for p in pontos}

    async def _faq_ids_na_colecao(self) -> dict[str, str]:
        from qdrant_client import models

        filtro = models.Filter(must=[models.FieldCondition(key="tipo", match=models.MatchValue(value=TIPO_FAQ))])
        encontrados: dict[str, str] = {}
        offset = None
        while True:
            pontos, offset = await self.client.scroll(
                self.collection, scroll_filter=filtro, limit=256, offset=offset, with_payload=["faq_id"], with_vectors=False
            )
            for ponto in pontos:
                encontrados[str(ponto.id)] = str((ponto.payload or {}).get("faq_id"))
            if offset is None:
                return encontrados

    async def carregar(
        self,
        faqs: list[FaqCanonico],
        *,
        recriar: bool = False,
        remover_ausentes: bool = True,
        batch_size: int = 32,
    ) -> CargaResultado:
        from qdrant_client import models

        started = time.perf_counter()
        resultado = CargaResultado(total=len(faqs))
        modelo = self.settings.qdrant_embedding_model

        # Com --recriar, a coleção só é apagada DEPOIS que todos os embeddings
        # foram gerados: se o modelo falhar (download, memória, dimensão), a
        # coleção antiga continua servindo o chat.
        dimensao = None if recriar else await self._dimensao_colecao()
        existentes = {} if dimensao is None else await self._hashes_existentes(faqs)

        pendentes: list[tuple[FaqCanonico, dict[str, Any]]] = []
        for faq in faqs:
            payload = faq.payload(modelo)
            anterior = existentes.get(faq.point_id)
            if anterior == payload["content_hash"]:
                resultado.inalterados += 1
                continue
            if anterior is None:
                resultado.inseridos += 1
            else:
                resultado.atualizados += 1
            pendentes.append((faq, payload))

        vetores: list[list[float]] = []
        for inicio in range(0, len(pendentes), batch_size):
            lote = pendentes[inicio : inicio + batch_size]
            vetores.extend(await self._embed([faq.texto_embedding() for faq, _ in lote]))

        if vetores:
            tamanho = len(vetores[0])
            if dimensao is not None and tamanho != dimensao:
                raise DimensaoIncompativel(
                    f"O modelo '{modelo}' gera vetores de {tamanho} dimensões, mas a coleção "
                    f"'{self.collection}' tem {dimensao}. Rode com --recriar para reindexar tudo."
                )
            if dimensao is None:
                if await self.client.collection_exists(self.collection):
                    await self.client.delete_collection(self.collection)
                await self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(size=tamanho, distance=models.Distance.COSINE),
                )
                await self.client.create_payload_index(
                    self.collection, field_name="tipo", field_schema=models.PayloadSchemaType.KEYWORD
                )
                resultado.colecao_criada = True
                dimensao = tamanho
            for inicio in range(0, len(pendentes), batch_size):
                await self.client.upsert(
                    self.collection,
                    points=[
                        models.PointStruct(id=faq.point_id, vector=vetor, payload=payload)
                        for (faq, payload), vetor in zip(
                            pendentes[inicio : inicio + batch_size], vetores[inicio : inicio + batch_size]
                        )
                    ],
                    wait=True,
                )

        if remover_ausentes and dimensao is not None:
            validos = {faq.point_id for faq in faqs}
            na_colecao = await self._faq_ids_na_colecao()
            sobrando = [pid for pid in na_colecao if pid not in validos]
            if sobrando:
                await self.client.delete(
                    self.collection, points_selector=models.PointIdsList(points=sobrando), wait=True
                )
            resultado.removidos = len(sobrando)

        resultado.dimensao = dimensao
        resultado.duracao_s = round(time.perf_counter() - started, 3)
        return resultado

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


async def testar_perguntas(settings: Settings, perguntas: list[str]) -> list[dict[str, Any]]:
    """Mostra o que o chat responderia: top-3 com score, e se passa no
    QDRANT_MIN_SCORE. Use para calibrar o limiar com perguntas reais."""
    from src.services.qdrant_service import QdrantFaqService

    service = QdrantFaqService(settings)
    try:
        saida = []
        for pergunta in perguntas:
            faqs = await service.buscar_faq(pergunta)
            melhor = service.melhor_resposta(faqs, None)
            saida.append(
                {
                    "pergunta": pergunta,
                    "resultado": melhor["faq_id"] if melhor else None,
                    "candidatos": [(f["faq_id"], round(float(f["score"]), 4), f["titulo"]) for f in faqs],
                }
            )
        return saida
    finally:
        await service.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carrega o FAQ canônico no Qdrant.")
    parser.add_argument("--arquivo", default=None, help="Markdown da base (padrão: KNOWLEDGE_BASE_PATH)")
    parser.add_argument("--recriar", action="store_true", help="apaga e recria a coleção (troca de modelo)")
    parser.add_argument("--manter-ausentes", action="store_true", help="não remove FAQs que saíram do arquivo")
    parser.add_argument("--dry-run", action="store_true", help="só valida o arquivo e mostra o resumo")
    parser.add_argument("--testar", nargs="+", metavar="PERGUNTA", help="consulta a coleção e mostra os scores")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = Settings.from_env()
    if args.testar:
        print(json.dumps(asyncio.run(testar_perguntas(settings, args.testar)), ensure_ascii=False, indent=2))
        return 0

    caminho = Path(args.arquivo) if args.arquivo else settings.knowledge_base_file
    try:
        faqs = parse_faq_canonicas(caminho.read_text(encoding="utf-8"))
    except FaqParseError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.dry_run:
        restritos = sum(1 for faq in faqs if faq.perfis_relacionados)
        print(json.dumps({"arquivo": str(caminho), "faqs": len(faqs), "restritos_por_perfil": restritos}, ensure_ascii=False))
        return 0

    async def _run() -> CargaResultado:
        loader = QdrantFaqLoader(settings)
        try:
            return await loader.carregar(faqs, recriar=args.recriar, remover_ausentes=not args.manter_ausentes)
        finally:
            await loader.close()

    try:
        resultado = asyncio.run(_run())
    except DimensaoIncompativel as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(resultado.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
