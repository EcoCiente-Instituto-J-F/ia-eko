# Compatibilidade com o schema PostgreSQL

O arquivo usado como contrato é `sql/ecociente_schema.sql`.

## Correções realizadas

O módulo PostgreSQL anterior referenciava objetos que não existem no DDL fornecido. Essas dependências foram removidas; simulações agora são calculadas por consultas somente leitura sobre tabelas existentes.

As consultas atuais utilizam, entre outras, `tb_postagens`, `tb_lkp_categorias_residuos`, `tb_lkp_status_validacoes_postagens`, `tb_torres`, `tb_moradores`, `tb_rel_usuarios_condominios`, `tb_lkp_niveis_confianca`, `tb_quizzes` e `tb_tentativas_quiz`.

## Pontuação

A documentação funcional descreve um histórico oficial de movimentações de pontos, mas o DDL entregue não contém essa tabela. Por isso o Python não inventa uma estrutura ausente. As tools identificam somas de `pontos_base` como `pontos_estimados`; o ranking oficial continua vindo da projeção Redis mantida pela API responsável pela pontuação.

Quando o schema ganhar um histórico oficial de movimentações, a tool de pontuação deve ser adaptada explicitamente a esse contrato e acompanhada de teste de integração.
