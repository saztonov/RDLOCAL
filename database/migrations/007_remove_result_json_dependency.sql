-- ============================================================================
-- Миграция: Убрать зависимость от result_json в расчёте pdf_status
-- ============================================================================
-- Контекст: result.json и _blocks.json больше не создаются.
-- Все OCR-данные блоков хранятся в annotations.data (JSONB).
-- Критерий complete: аннотация в таблице annotations + ocr_html в node_files.
-- ============================================================================

-- 1. Обновить функцию recalculate_all_pdf_statuses:
--    - Убрать проверку result_json из node_files
--    - Проверять наличие аннотации в таблице annotations
--    - Критерий complete: has_annotation + has_ocr_html_db
CREATE OR REPLACE FUNCTION public.recalculate_all_pdf_statuses()
 RETURNS TABLE(node_id uuid, old_status text, new_status text, status_message text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    doc RECORD;
    v_status TEXT;
    v_message TEXT;
    v_has_annotation BOOLEAN;
    v_has_ocr_db BOOLEAN;
    v_r2_key TEXT;
BEGIN
    FOR doc IN
        SELECT id, attributes->>'r2_key' as r2_key, pdf_status
        FROM tree_nodes
        WHERE node_type = 'document'
    LOOP
        v_r2_key := doc.r2_key;

        IF v_r2_key IS NULL OR v_r2_key = '' THEN
            v_status := 'unknown';
            v_message := 'Нет R2 ключа';
        ELSE
            -- Проверяем наличие аннотации в таблице annotations
            SELECT EXISTS(
                SELECT 1 FROM annotations WHERE annotations.node_id = doc.id
            ) INTO v_has_annotation;

            -- Проверяем наличие ocr_html в node_files
            SELECT
                COALESCE(bool_or(file_type = 'ocr_html'), FALSE) AS has_ocr
            INTO v_has_ocr_db
            FROM node_files
            WHERE node_files.node_id = doc.id;

            -- Определяем статус
            IF v_has_annotation AND v_has_ocr_db THEN
                v_status := 'complete';
                v_message := 'Аннотация и OCR на месте';
            ELSIF NOT v_has_annotation THEN
                v_status := 'missing_blocks';
                v_message := 'Нет аннотации в базе данных';
            ELSE
                v_status := 'missing_files';
                v_message := '';
                IF NOT v_has_ocr_db THEN
                    v_message := v_message || 'ocr.html не зарегистрирован; ';
                END IF;
            END IF;
        END IF;

        -- Обновляем статус
        UPDATE tree_nodes
        SET
            pdf_status = v_status,
            pdf_status_message = v_message,
            pdf_status_updated_at = NOW()
        WHERE id = doc.id;

        -- Возвращаем результат
        node_id := doc.id;
        old_status := doc.pdf_status;
        new_status := v_status;
        status_message := v_message;
        RETURN NEXT;
    END LOOP;
END;
$function$;

-- 2. Обновить комментарий к node_files.file_type (убрать result_json, blocks_index из актуальных)
COMMENT ON COLUMN public.node_files.file_type IS 'Тип файла: pdf, result_md, crop, ocr_html, crops_folder, qa_manifest. Legacy (не создаются): annotation, result_json, blocks_index';

-- 3. Обновить комментарий к annotations.data (теперь содержит OCR payload)
COMMENT ON COLUMN public.annotations.data IS 'Полная структура аннотации с OCR данными: pages[].blocks[].{ocr_text, ocr_html, ocr_json, ocr_meta, crop_url, stamp_data}';

-- 4. Пересчитать статусы всех документов по новым критериям
-- (раскомментировать при необходимости)
-- SELECT * FROM recalculate_all_pdf_statuses();
