# Painel de controle do ia-eko

Painel de observabilidade (TypeScript + Vite). Documentação completa em [`docs/PAINEL_DE_CONTROLE.md`](../docs/PAINEL_DE_CONTROLE.md).

```bash
npm install
npm run dev     # http://localhost:5173, lê a API em http://127.0.0.1:8000
npm test        # testes de leitura do /metrics, percentis e custo
npm run build
```

Defina `VITE_API_URL` para apontar para outra API. Sem a API no ar, o painel entra em modo demonstração (dados simulados, sempre rotulados).
