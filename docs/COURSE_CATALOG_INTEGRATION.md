# Catálogo de cursos autenticado da Mobile API

O agente educacional do Eko usa o RAG para orientações gerais de separação e reciclagem.

Para solicitações que pedem a indicação ou listagem de cursos, o Eko consulta diretamente `GET /api/cursos` da `ds-mobile-api` com o Bearer JWT da própria solicitação. A Mobile API só retorna cursos reais cadastrados no banco de dados.

## Contrato

- Origem: `CALENDAR_API_BASE_URL` (a mesma Mobile API usada pelo calendário), sem nova variável de produção.
- Endpoint: `GET /api/cursos`.
- Resposta esperada: lista JSON de `{id, titulo, descricao}`, com `descricao` opcional.
- Autenticação: token obtido por `current_bearer_token()` dentro do contexto da solicitação HTTP. Não entra em prompts, estado de agentes, históricos ou logs.
- Perfis: as rotas educativas públicas continuam disponíveis a todos os perfis autenticados. A API Java mantém a validação do JWT.
- As indicações e IDs são determinísticos e derivados da resposta da API. O juiz de saída não modifica os cursos retornados.

## Diagnóstico

Se o Eko disser que o catálogo não está configurado, verifique `CALENDAR_API_BASE_URL` no deployment AWS.

Se informar indisponibilidade, verifique comunicação entre `ia-eko` e `mobile-api-service` no namespace `ecociente` e se `GET /api/cursos` responde em tempo hábil.

Se a API responder `401`, verifique a validade do JWT entre Eko e Mobile API. Nunca copie o token em logs nem compartilhe segredos.

Se retornar catálogo vazio, é preciso revisar os dados de cursos cadastrados; não há fallback com cursos inventados.

## Homologação por perfil

1. Usuário comum: "Me recomende um curso de reciclagem disponível no EcoCiente."
2. Morador: "Quais cursos estão disponíveis no EcoCiente?"
3. Cooperativa: "Me indique um curso de compostagem."
4. Síndico: "Me recomende um curso de reciclagem."
5. Em todas as contas, consultar também "Como separar vidro quebrado?" para assegurar que a rota RAG geral continua funcionando.
