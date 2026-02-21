-- Миграция: хранение аннотаций (обводок/блоков) в Supabase
-- Заменяет хранение в JSON файлах (локально + R2) на таблицу annotations

CREATE TABLE IF NOT EXISTS public.annotations (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    node_id uuid NOT NULL REFERENCES public.tree_nodes(id) ON DELETE CASCADE,
    data jsonb NOT NULL DEFAULT '{}',
    format_version integer NOT NULL DEFAULT 2,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT annotations_node_id_unique UNIQUE (node_id)
);

CREATE INDEX IF NOT EXISTS idx_annotations_node_id ON public.annotations(node_id);

COMMENT ON TABLE public.annotations IS 'Хранение аннотаций (обводок/блоков) документов';
COMMENT ON COLUMN public.annotations.data IS 'Полная структура аннотации в формате v2 (pages, blocks)';
COMMENT ON COLUMN public.annotations.node_id IS 'Ссылка на документ в tree_nodes (1:1)';
COMMENT ON COLUMN public.annotations.format_version IS 'Версия формата аннотации (текущая: 2)';
