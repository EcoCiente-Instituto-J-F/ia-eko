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

Depois dos FAQs, a mesma coleção recebe os trechos do RAG (tipo=rag_trecho):
o .md inteiro cortado como no índice em memória e, com
ENABLE_EXTERNAL_SOURCE=true, a página do SINIR. Mesma lógica incremental; se
o SINIR não baixar (ou vier sem texto), os trechos antigos dele ficam como
estão. Exceção: --recriar com o SINIR fora do ar deixa a coleção sem os
trechos do SINIR até a próxima carga em que ele baixe.

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

import httpx

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
TIPO_TRECHO = "rag_trecho"
# Namespace fixo: o id do ponto depende só do faq_id (ou de source#chunk).
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


@dataclass(frozen=True)
class Trecho:
    """Um trecho do RAG (pedaço do .md ou da página do SINIR), com os mesmos
    metadados que o índice em memória do RAGService usa."""

    title: str
    source: str
    url: str | None
    kind: str
    chunk: int
    texto: str

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(_NAMESPACE, f"trecho:{self.source}#{self.chunk}"))

    def payload(self, embedding_model: str) -> dict[str, Any]:
        base = {
            "tipo": TIPO_TRECHO,
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "kind": self.kind,
            "chunk": self.chunk,
            "texto": self.texto,
            "embedding_model": embedding_model,
        }
        base["content_hash"] = content_hash(base)
        return base


def montar_trechos(
    texto: str, *, settings: Settings, title: str, source: str, url: str | None, kind: str
) -> list[Trecho]:
    # Mesmo corte do índice em memória: os dois modos devolvem os mesmos trechos.
    from src.services.rag_service import dividir_em_trechos

    return [
        Trecho(title=title, source=source, url=url, kind=kind, chunk=doc.metadata["chunk"], texto=doc.page_content)
        for doc in dividir_em_trechos(texto, title=title, source=source, url=url, kind=kind, settings=settings)
    ]


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

    async def _garantir_indices(self) -> None:
        """Índices KEYWORD de `tipo` e `source`, idempotentes: o Qdrant Cloud
        (strict mode) pode rejeitar filtros em campos sem índice, inclusive em
        coleções criadas por versões anteriores da carga."""
        from qdrant_client import models

        for campo in ("tipo", "source"):
            await self.client.create_payload_index(
                self.collection, field_name=campo, field_schema=models.PayloadSchemaType.KEYWORD
            )

    async def _hashes_existentes(self, ids: list[str]) -> dict[str, str]:
        pontos = await self.client.retrieve(self.collection, ids=ids, with_payload=True, with_vectors=False)
        return {str(p.id): str((p.payload or {}).get("content_hash", "")) for p in pontos}

    async def _ids_na_colecao(self, filtro: Any) -> list[str]:
        encontrados: list[str] = []
        offset = None
        while True:
            pontos, offset = await self.client.scroll(
                self.collection, scroll_filter=filtro, limit=256, offset=offset, with_payload=False, with_vectors=False
            )
            encontrados.extend(str(ponto.id) for ponto in pontos)
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

        modelo = self.settings.qdrant_embedding_model
        filtro = models.Filter(must=[models.FieldCondition(key="tipo", match=models.MatchValue(value=TIPO_FAQ))])
        return await self._sincronizar(
            [(faq.point_id, faq.payload(modelo), faq.texto_embedding()) for faq in faqs],
            recriar=recriar,
            batch_size=batch_size,
            filtro_remocao=filtro if remover_ausentes else None,
        )

    async def carregar_trechos(
        self,
        trechos: list[Trecho],
        *,
        fontes: set[str],
        recriar: bool = False,
        batch_size: int = 32,
    ) -> CargaResultado:
        """Trechos do RAG. Só remove os trechos ausentes cujo `source` está em
        `fontes`: uma fonte que não foi lida nesta carga (ex.: SINIR fora do
        ar) mantém os trechos antigos. `recriar` apaga a coleção INTEIRA,
        inclusive os FAQs canônicos — o main só recria na carga dos FAQs."""
        from qdrant_client import models

        modelo = self.settings.qdrant_embedding_model
        filtro = models.Filter(
            must=[
                models.FieldCondition(key="tipo", match=models.MatchValue(value=TIPO_TRECHO)),
                models.FieldCondition(key="source", match=models.MatchAny(any=sorted(fontes))),
            ]
        )
        return await self._sincronizar(
            [(t.point_id, t.payload(modelo), t.texto) for t in trechos],
            recriar=recriar,
            batch_size=batch_size,
            filtro_remocao=filtro if fontes else None,
        )

    async def _sincronizar(
        self,
        itens: list[tuple[str, dict[str, Any], str]],
        *,
        recriar: bool,
        batch_size: int,
        filtro_remocao: Any,
    ) -> CargaResultado:
        """Upsert incremental de (point_id, payload, texto do embedding) e
        remoção dos pontos de `filtro_remocao` que não vieram em `itens`."""
        from qdrant_client import models

        started = time.perf_counter()
        resultado = CargaResultado(total=len(itens))
        modelo = self.settings.qdrant_embedding_model

        # Com --recriar, a coleção só é apagada DEPOIS que todos os embeddings
        # foram gerados: se o modelo falhar (download, memória, dimensão), a
        # coleção antiga continua servindo o chat.
        dimensao = None if recriar else await self._dimensao_colecao()
        if dimensao is not None:
            await self._garantir_indices()
        existentes = {} if dimensao is None else await self._hashes_existentes([pid for pid, _, _ in itens])

        pendentes: list[tuple[str, dict[str, Any], str]] = []
        for pid, payload, texto in itens:
            anterior = existentes.get(pid)
            if anterior == payload["content_hash"]:
                resultado.inalterados += 1
                continue
            if anterior is None:
                resultado.inseridos += 1
            else:
                resultado.atualizados += 1
            pendentes.append((pid, payload, texto))

        vetores: list[list[float]] = []
        for inicio in range(0, len(pendentes), batch_size):
            lote = pendentes[inicio : inicio + batch_size]
            vetores.extend(await self._embed([texto for _, _, texto in lote]))

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
                await self._garantir_indices()
                resultado.colecao_criada = True
                dimensao = tamanho
            for inicio in range(0, len(pendentes), batch_size):
                await self.client.upsert(
                    self.collection,
                    points=[
                        models.PointStruct(id=pid, vector=vetor, payload=payload)
                        for (pid, payload, _), vetor in zip(
                            pendentes[inicio : inicio + batch_size], vetores[inicio : inicio + batch_size]
                        )
                    ],
                    wait=True,
                )

        if filtro_remocao is not None and dimensao is not None:
            validos = {pid for pid, _, _ in itens}
            sobrando = [pid for pid in await self._ids_na_colecao(filtro_remocao) if pid not in validos]
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
    parser = argparse.ArgumentParser(description="Carrega o FAQ canônico e os trechos do RAG no Qdrant.")
    parser.add_argument("--arquivo", default=None, help="Markdown da base (padrão: KNOWLEDGE_BASE_PATH)")
    parser.add_argument(
        "--recriar",
        action="store_true",
        help="apaga e recria a coleção (troca de modelo); com o SINIR fora do ar, os trechos dele só voltam na próxima carga",
    )
    parser.add_argument("--manter-ausentes", action="store_true", help="não remove FAQs/trechos que saíram da base")
    parser.add_argument("--dry-run", action="store_true", help="só valida o arquivo e mostra o resumo")
    parser.add_argument("--testar", nargs="+", metavar="PERGUNTA", help="consulta a coleção e mostra os scores")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = Settings.from_env()
    if args.testar:
        print(json.dumps(asyncio.run(testar_perguntas(settings, args.testar)), ensure_ascii=False, indent=2))
        return 0

    from src.services import rag_service

    caminho = Path(args.arquivo) if args.arquivo else settings.knowledge_base_file
    texto = caminho.read_text(encoding="utf-8")
    try:
        faqs = parse_faq_canonicas(texto)
    except FaqParseError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # Mesmo `source` que o índice em memória usa, para as citações não mudarem.
    fonte_local = Path(args.arquivo or settings.knowledge_base_path).as_posix()
    trechos = montar_trechos(
        texto, settings=settings, title=rag_service.TITULO_BASE_LOCAL, source=fonte_local, url=None, kind="local"
    )

    if args.dry_run:
        # Offline: o SINIR só é baixado na carga de verdade.
        restritos = sum(1 for faq in faqs if faq.perfis_relacionados)
        print(
            json.dumps(
                {"arquivo": str(caminho), "faqs": len(faqs), "restritos_por_perfil": restritos, "trechos": len(trechos)},
                ensure_ascii=False,
            )
        )
        return 0

    async def _run() -> dict[str, Any]:
        fontes = {fonte_local}
        if settings.enable_external_source:
            url = settings.external_source_url
            try:
                externo = await rag_service.baixar_fonte_externa(settings)
                if not externo.strip():
                    # 200 sem texto (ex.: página só de JS): sem trechos, a fonte
                    # entraria em `fontes` vazia e apagaria tudo o que já foi indexado.
                    raise ValueError("página sem texto")
            except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
                # Sem a página, os trechos antigos do SINIR ficam como estão.
                logger.warning("SINIR indisponível (%s: %s); trechos antigos mantidos.", type(exc).__name__, exc)
            else:
                fontes.add(url)
                trechos.extend(
                    montar_trechos(
                        externo, settings=settings, title=settings.external_source_title, source=url, url=url, kind="external"
                    )
                )
        else:
            # Flag desligada: a fonte entra sem trechos e os já indexados do SINIR
            # são removidos (como na memória, onde desligar esconde o SINIR).
            fontes.add(settings.external_source_url)
        loader = QdrantFaqLoader(settings)
        try:
            # FAQs primeiro: é a carga que pode recriar a coleção.
            r_faqs = await loader.carregar(faqs, recriar=args.recriar, remover_ausentes=not args.manter_ausentes)
            r_trechos = await loader.carregar_trechos(trechos, fontes=set() if args.manter_ausentes else fontes)
            return {"faqs": r_faqs.as_dict(), "trechos": r_trechos.as_dict()}
        finally:
            await loader.close()

    try:
        resumo = asyncio.run(_run())
    except DimensaoIncompativel as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(resumo, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
