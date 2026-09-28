-- Volume sintético sobre sql/ecociente_schema.sql (testes do ETL em Postgres real).
-- Parâmetros via psql -v: usuarios, condos, coops, cursos, tentativas, postagens, votos.
\set ON_ERROR_STOP 1
SELECT setseed(0.42);

INSERT INTO tb_lkp_tipos_usuarios (nome_tipo) VALUES
 ('USUARIO_COMUM'),('MORADOR_RESIDENCIAL'),('USUARIO_COMERCIAL'),('SINDICO_RESIDENCIAL'),('SINDICO_COMERCIAL'),('COOPERATIVA');
INSERT INTO tb_lkp_tipos_condominios (nome_tipo) VALUES ('RESIDENCIAL'),('COMERCIAL');
INSERT INTO tb_lkp_niveis_confianca (nome_nivel, peso_voto) VALUES ('novato',1),('confiavel',2),('referencia',3);
INSERT INTO tb_lkp_status_validacoes_postagens (nome_status) VALUES ('PENDENTE'),('APROVADA'),('REPROVADA');
INSERT INTO tb_lkp_tipos_votos_postagens (nome_tipo) VALUES ('CONFIRMA'),('CONTESTA');
INSERT INTO tb_lkp_motivos_denuncia (descricao) VALUES ('foto falsa'),('categoria errada'),('foto repetida');
INSERT INTO tb_lkp_categorias_residuos (nome_categoria) VALUES
 ('Plástico'),('Papel'),('Vidro'),('Metal'),('Orgânico'),('Eletrônico'),('Óleo'),('Pilhas');

INSERT INTO tb_enderecos (cidade, estado)
SELECT 'São Paulo', 'SP' FROM generate_series(1, :usuarios + :condos + :coops);

INSERT INTO tb_usuarios (nome_usuario, email_usuario, senha_hash, tipo_usuario_id, endereco_id, registro_em)
SELECT 'Usuario ' || g, 'u' || g || '@ex.com', 'x',
       CASE WHEN g % 50 = 0 THEN 4 WHEN g % 10 = 0 THEN 1 ELSE 2 END,
       g, now() - (random() * interval '365 days')
FROM generate_series(1, :usuarios) g;

INSERT INTO tb_sindicos (usuario_id) SELECT id_usuario FROM tb_usuarios WHERE tipo_usuario_id = 4 LIMIT :condos;

INSERT INTO tb_condominios (nome_condominio, tipo_condominio_id, sindico_id, endereco_id)
SELECT 'Condominio ' || g, 1, g, :usuarios + g FROM generate_series(1, :condos) g;

INSERT INTO tb_torres (nome_torre, condominio_id)
SELECT 'Torre ' || t, c FROM generate_series(1, :condos) c, generate_series(1, 4) t;

-- 80% moram em algum condomínio (usuário comum = g%10=0 não mora).
INSERT INTO tb_moradores (condominio_id, usuario_id)
SELECT 1 + (id_usuario % :condos), id_usuario FROM tb_usuarios WHERE id_usuario % 10 <> 0 AND id_usuario % 7 <> 0;

INSERT INTO tb_rel_usuarios_condominios (usuario_id, condominio_id, aprovado, trust_score,
       postagens_validadas_sem_contestacao, denuncias_realizadas, denuncias_procedentes)
SELECT usuario_id, condominio_id, true, round((random() * 100)::numeric, 2), (random()*30)::int, 3, 1 FROM tb_moradores;

INSERT INTO tb_cooperativas (cnpj_cooperativa, nome_cooperativa, usuario_id, endereco_id, data_cadastro)
SELECT lpad(g::text, 14, '0'), 'Cooperativa ' || g, NULL, :usuarios + :condos + g, now() - (random() * interval '365 days')
FROM generate_series(1, :coops) g;

INSERT INTO tb_cursos (titulo_curso, esta_ativo) SELECT 'Curso ' || g, g % 9 <> 0 FROM generate_series(1, :cursos) g;
INSERT INTO tb_aulas (curso_id, titulo_aula, ordem) SELECT c, 'Aula ' || a, a FROM generate_series(1, :cursos) c, generate_series(1, 5) a;
INSERT INTO tb_quizzes (curso_id, aula_id, titulo_quiz, pontos_recompensa) SELECT curso_id, id_aula, 'Quiz ' || id_aula, 10 FROM tb_aulas;

INSERT INTO tb_tentativas_quiz (usuario_id, quiz_id, nota, aprovado, iniciado_em, concluido_em)
SELECT u, q, n, n >= 70, ts - interval '10 minutes', ts
FROM (
  SELECT 1 + (random() * (:usuarios - 1))::int AS u,
         1 + (random() * (:cursos * 5 - 1))::int AS q,
         round((random() * 100)::numeric, 1) AS n,
         now() - interval '1 day' - (random() * interval '364 days') AS ts
  FROM generate_series(1, :tentativas)
) x;

INSERT INTO tb_postagens (usuario_id, condominio_id, torre_id, categoria_id, url_foto, hash_foto,
                          capturada_em, data_postagem, status_validacao_id, saldo_confianca, resolvido_em)
SELECT m.usuario_id, m.condominio_id, (m.condominio_id - 1) * 4 + 1 + (g % 4), 1 + (g % 8), 'https://x/' || g, md5(g::text),
       ts - interval '1 minute', ts, 1 + (g % 3), 0, CASE WHEN g % 3 = 0 THEN NULL ELSE ts + interval '5 hours' END
FROM (
  SELECT g, now() - interval '1 day' - (random() * interval '364 days') AS ts,
         arr[1 + ((g::bigint * 7919) % cardinality(arr))] AS uid
  FROM generate_series(1, :postagens) g,
       (SELECT array_agg(usuario_id ORDER BY usuario_id) AS arr FROM tb_moradores) a
) p JOIN tb_moradores m ON m.usuario_id = p.uid;

INSERT INTO tb_rel_votos_postagens (postagem_id, usuario_id, tipo_voto_id, motivo_denuncia_id, peso_aplicado, votado_em)
SELECT DISTINCT ON (pid, uid) pid, uid, 1 + (k % 2), CASE WHEN k % 7 = 0 THEN 1 + (k % 3) ELSE NULL END, 1,
       now() - interval '1 day' - (random() * interval '360 days')
FROM (
  SELECT k, 1 + (random() * (:postagens - 1))::int AS pid, 1 + (random() * (:usuarios - 1))::int AS uid
  FROM generate_series(1, :votos) k
) v;

ANALYZE;
