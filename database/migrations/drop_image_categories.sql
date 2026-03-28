-- Удаление таблицы image_categories (промпты теперь берутся из config.yaml)
DROP TRIGGER IF EXISTS trigger_image_categories_updated_at ON public.image_categories;
DROP FUNCTION IF EXISTS public.update_image_categories_updated_at();
DROP TABLE IF EXISTS public.image_categories;
