-- Rollback intencionalmente nao destrutivo.
-- Esta migration formaliza tabelas LeoFT ja usadas por E2E e pode conter dados de alunos/planos.
-- Nao dropar tabelas, colunas, indices ou constraints automaticamente nesta fase.
-- Qualquer rollback destrutivo deve ser planejado manualmente, com backup e aprovacao explicita.

select 'rollback noop: leoft formal migration keeps existing data and schema' as message;
