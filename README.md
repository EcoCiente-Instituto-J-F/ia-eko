# EcoCiente IA

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?style=for-the-badge)
![Neo4j](https://img.shields.io/badge/Neo4j-5-4581C3?style=for-the-badge&logo=neo4j&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-async-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![GitHub repo size](https://img.shields.io/github/repo-size/EcoCiente-Instituto-J-F/Eko?style=for-the-badge)
![GitHub last commit](https://img.shields.io/github/last-commit/EcoCiente-Instituto-J-F/Eko?style=for-the-badge)
![License](https://img.shields.io/badge/license-a%20definir-lightgrey?style=for-the-badge)

> API multiagente do EcoCiente: FastAPI + LangChain/LangGraph orquestrando especialistas (FAQ, educação ambiental, coletas, analytics e grafo de relacionamentos) com RAG, memória conversacional persistente, guardrails de entrada/saída e integrações MCP/A2A.

## Sobre o projeto

O EcoCiente é uma plataforma de gestão de reciclagem para condomínios que conecta moradores,
síndicos e cooperativas de coleta. Este repositório é o backend de IA: um chatbot multiagente
que responde de forma diferente conforme o perfil autenticado (`usuario_comum`,
`sindico_residencial`, `sindico_comercial`, `morador_residencial`, `usuario_comercial`,
`cooperativa`), cada um com seu próprio recorte de permissões.

Uma mensagem do usuário passa por um pipeline determinístico em grafo (LangGraph): validação
de entrada, recuperação de memória, roteamento para o especialista correto, validação de saída
por um juiz e, se reprovada, uma rodada de correção — tudo isso instrumentado com Prometheus e
exposto via FastAPI.

| Perfil | Pode acessar |
| --- | --- |
| `usuario_comum` | `faq`, `educacional` |
| `sindico_residencial` / `sindico_comercial` | `faq`, `educacional`, `analytics`, `coletas`, `grafo` |
| `morador_residencial` | `faq`, `educacional`, `analytics` (individual), `grafo` |
| `usuario_comercial` | `faq`, `educacional`, `analytics` (individual), `coletas` |
| `cooperativa` | `faq`, `coletas` |

As regras completas de autorização (perfil × ação, não só perfil × agente) estão em
`src/security/policies.py`.
