-- Database Schema SQL Export
-- Generated: 2026-02-21T14:14:08.198317
-- Database: postgres
-- Host: aws-1-eu-north-1.pooler.supabase.com

-- ============================================
-- TABLES
-- ============================================

-- Table: auth.audit_log_entries
-- Description: Auth: Audit trail for user actions.
CREATE TABLE IF NOT EXISTS auth.audit_log_entries (
    instance_id uuid,
    id uuid NOT NULL,
    payload json,
    created_at timestamp with time zone,
    ip_address character varying(64) NOT NULL DEFAULT ''::character varying,
    CONSTRAINT audit_log_entries_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.audit_log_entries IS 'Auth: Audit trail for user actions.';

-- Table: auth.flow_state
-- Description: Stores metadata for all OAuth/SSO login flows
CREATE TABLE IF NOT EXISTS auth.flow_state (
    id uuid NOT NULL,
    user_id uuid,
    auth_code text,
    code_challenge_method auth.code_challenge_method,
    code_challenge text,
    provider_type text NOT NULL,
    provider_access_token text,
    provider_refresh_token text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    authentication_method text NOT NULL,
    auth_code_issued_at timestamp with time zone,
    invite_token text,
    referrer text,
    oauth_client_state_id uuid,
    linking_target_id uuid,
    email_optional boolean NOT NULL DEFAULT false,
    CONSTRAINT flow_state_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.flow_state IS 'Stores metadata for all OAuth/SSO login flows';

-- Table: auth.identities
-- Description: Auth: Stores identities associated to a user.
CREATE TABLE IF NOT EXISTS auth.identities (
    provider_id text NOT NULL,
    user_id uuid NOT NULL,
    identity_data jsonb NOT NULL,
    provider text NOT NULL,
    last_sign_in_at timestamp with time zone,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    email text DEFAULT lower((identity_data ->> 'email'::text)),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT identities_pkey PRIMARY KEY (id),
    CONSTRAINT identities_provider_id_provider_unique UNIQUE (provider),
    CONSTRAINT identities_provider_id_provider_unique UNIQUE (provider_id),
    CONSTRAINT identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);
COMMENT ON TABLE auth.identities IS 'Auth: Stores identities associated to a user.';
COMMENT ON COLUMN auth.identities.email IS 'Auth: Email is a generated column that references the optional email property in the identity_data';

-- Table: auth.instances
-- Description: Auth: Manages users across multiple sites.
CREATE TABLE IF NOT EXISTS auth.instances (
    id uuid NOT NULL,
    uuid uuid,
    raw_base_config text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    CONSTRAINT instances_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.instances IS 'Auth: Manages users across multiple sites.';

-- Table: auth.mfa_amr_claims
-- Description: auth: stores authenticator method reference claims for multi factor authentication
CREATE TABLE IF NOT EXISTS auth.mfa_amr_claims (
    session_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    authentication_method text NOT NULL,
    id uuid NOT NULL,
    CONSTRAINT amr_id_pk PRIMARY KEY (id),
    CONSTRAINT mfa_amr_claims_session_id_authentication_method_pkey UNIQUE (authentication_method),
    CONSTRAINT mfa_amr_claims_session_id_authentication_method_pkey UNIQUE (session_id),
    CONSTRAINT mfa_amr_claims_session_id_fkey FOREIGN KEY (session_id) REFERENCES auth.sessions(id)
);
COMMENT ON TABLE auth.mfa_amr_claims IS 'auth: stores authenticator method reference claims for multi factor authentication';

-- Table: auth.mfa_challenges
-- Description: auth: stores metadata about challenge requests made
CREATE TABLE IF NOT EXISTS auth.mfa_challenges (
    id uuid NOT NULL,
    factor_id uuid NOT NULL,
    created_at timestamp with time zone NOT NULL,
    verified_at timestamp with time zone,
    ip_address inet NOT NULL,
    otp_code text,
    web_authn_session_data jsonb,
    CONSTRAINT mfa_challenges_auth_factor_id_fkey FOREIGN KEY (factor_id) REFERENCES auth.mfa_factors(id),
    CONSTRAINT mfa_challenges_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.mfa_challenges IS 'auth: stores metadata about challenge requests made';

-- Table: auth.mfa_factors
-- Description: auth: stores metadata about factors
CREATE TABLE IF NOT EXISTS auth.mfa_factors (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    friendly_name text,
    factor_type auth.factor_type NOT NULL,
    status auth.factor_status NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    secret text,
    phone text,
    last_challenged_at timestamp with time zone,
    web_authn_credential jsonb,
    web_authn_aaguid uuid,
    last_webauthn_challenge_data jsonb,
    CONSTRAINT mfa_factors_last_challenged_at_key UNIQUE (last_challenged_at),
    CONSTRAINT mfa_factors_pkey PRIMARY KEY (id),
    CONSTRAINT mfa_factors_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);
COMMENT ON TABLE auth.mfa_factors IS 'auth: stores metadata about factors';
COMMENT ON COLUMN auth.mfa_factors.last_webauthn_challenge_data IS 'Stores the latest WebAuthn challenge data including attestation/assertion for customer verification';

-- Table: auth.oauth_authorizations
CREATE TABLE IF NOT EXISTS auth.oauth_authorizations (
    id uuid NOT NULL,
    authorization_id text NOT NULL,
    client_id uuid NOT NULL,
    user_id uuid,
    redirect_uri text NOT NULL,
    scope text NOT NULL,
    state text,
    resource text,
    code_challenge text,
    code_challenge_method auth.code_challenge_method,
    response_type auth.oauth_response_type NOT NULL DEFAULT 'code'::auth.oauth_response_type,
    status auth.oauth_authorization_status NOT NULL DEFAULT 'pending'::auth.oauth_authorization_status,
    authorization_code text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    expires_at timestamp with time zone NOT NULL DEFAULT (now() + '00:03:00'::interval),
    approved_at timestamp with time zone,
    nonce text,
    CONSTRAINT oauth_authorizations_authorization_code_key UNIQUE (authorization_code),
    CONSTRAINT oauth_authorizations_authorization_id_key UNIQUE (authorization_id),
    CONSTRAINT oauth_authorizations_client_id_fkey FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id),
    CONSTRAINT oauth_authorizations_pkey PRIMARY KEY (id),
    CONSTRAINT oauth_authorizations_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

-- Table: auth.oauth_client_states
-- Description: Stores OAuth states for third-party provider authentication flows where Supabase acts as the OAuth client.
CREATE TABLE IF NOT EXISTS auth.oauth_client_states (
    id uuid NOT NULL,
    provider_type text NOT NULL,
    code_verifier text,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT oauth_client_states_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.oauth_client_states IS 'Stores OAuth states for third-party provider authentication flows where Supabase acts as the OAuth client.';

-- Table: auth.oauth_clients
CREATE TABLE IF NOT EXISTS auth.oauth_clients (
    id uuid NOT NULL,
    client_secret_hash text,
    registration_type auth.oauth_registration_type NOT NULL,
    redirect_uris text NOT NULL,
    grant_types text NOT NULL,
    client_name text,
    client_uri text,
    logo_uri text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    deleted_at timestamp with time zone,
    client_type auth.oauth_client_type NOT NULL DEFAULT 'confidential'::auth.oauth_client_type,
    token_endpoint_auth_method text NOT NULL,
    CONSTRAINT oauth_clients_pkey PRIMARY KEY (id)
);

-- Table: auth.oauth_consents
CREATE TABLE IF NOT EXISTS auth.oauth_consents (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    client_id uuid NOT NULL,
    scopes text NOT NULL,
    granted_at timestamp with time zone NOT NULL DEFAULT now(),
    revoked_at timestamp with time zone,
    CONSTRAINT oauth_consents_client_id_fkey FOREIGN KEY (client_id) REFERENCES auth.oauth_clients(id),
    CONSTRAINT oauth_consents_pkey PRIMARY KEY (id),
    CONSTRAINT oauth_consents_user_client_unique UNIQUE (client_id),
    CONSTRAINT oauth_consents_user_client_unique UNIQUE (user_id),
    CONSTRAINT oauth_consents_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

-- Table: auth.one_time_tokens
CREATE TABLE IF NOT EXISTS auth.one_time_tokens (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    token_type auth.one_time_token_type NOT NULL,
    token_hash text NOT NULL,
    relates_to text NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    CONSTRAINT one_time_tokens_pkey PRIMARY KEY (id),
    CONSTRAINT one_time_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

-- Table: auth.refresh_tokens
-- Description: Auth: Store of tokens used to refresh JWT tokens once they expire.
CREATE TABLE IF NOT EXISTS auth.refresh_tokens (
    instance_id uuid,
    id bigint NOT NULL DEFAULT nextval('auth.refresh_tokens_id_seq'::regclass),
    token character varying(255),
    user_id character varying(255),
    revoked boolean,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    parent character varying(255),
    session_id uuid,
    CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id),
    CONSTRAINT refresh_tokens_session_id_fkey FOREIGN KEY (session_id) REFERENCES auth.sessions(id),
    CONSTRAINT refresh_tokens_token_unique UNIQUE (token)
);
COMMENT ON TABLE auth.refresh_tokens IS 'Auth: Store of tokens used to refresh JWT tokens once they expire.';

-- Table: auth.saml_providers
-- Description: Auth: Manages SAML Identity Provider connections.
CREATE TABLE IF NOT EXISTS auth.saml_providers (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    entity_id text NOT NULL,
    metadata_xml text NOT NULL,
    metadata_url text,
    attribute_mapping jsonb,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    name_id_format text,
    CONSTRAINT saml_providers_entity_id_key UNIQUE (entity_id),
    CONSTRAINT saml_providers_pkey PRIMARY KEY (id),
    CONSTRAINT saml_providers_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id)
);
COMMENT ON TABLE auth.saml_providers IS 'Auth: Manages SAML Identity Provider connections.';

-- Table: auth.saml_relay_states
-- Description: Auth: Contains SAML Relay State information for each Service Provider initiated login.
CREATE TABLE IF NOT EXISTS auth.saml_relay_states (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    request_id text NOT NULL,
    for_email text,
    redirect_to text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    flow_state_id uuid,
    CONSTRAINT saml_relay_states_flow_state_id_fkey FOREIGN KEY (flow_state_id) REFERENCES auth.flow_state(id),
    CONSTRAINT saml_relay_states_pkey PRIMARY KEY (id),
    CONSTRAINT saml_relay_states_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id)
);
COMMENT ON TABLE auth.saml_relay_states IS 'Auth: Contains SAML Relay State information for each Service Provider initiated login.';

-- Table: auth.schema_migrations
-- Description: Auth: Manages updates to the auth system.
CREATE TABLE IF NOT EXISTS auth.schema_migrations (
    version character varying(255) NOT NULL,
    CONSTRAINT schema_migrations_pkey PRIMARY KEY (version)
);
COMMENT ON TABLE auth.schema_migrations IS 'Auth: Manages updates to the auth system.';

-- Table: auth.sessions
-- Description: Auth: Stores session data associated to a user.
CREATE TABLE IF NOT EXISTS auth.sessions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    factor_id uuid,
    aal auth.aal_level,
    not_after timestamp with time zone,
    refreshed_at timestamp without time zone,
    user_agent text,
    ip inet,
    tag text,
    oauth_client_id uuid,
    refresh_token_hmac_key text,
    refresh_token_counter bigint,
    scopes text,
    CONSTRAINT sessions_oauth_client_id_fkey FOREIGN KEY (oauth_client_id) REFERENCES auth.oauth_clients(id),
    CONSTRAINT sessions_pkey PRIMARY KEY (id),
    CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);
COMMENT ON TABLE auth.sessions IS 'Auth: Stores session data associated to a user.';
COMMENT ON COLUMN auth.sessions.not_after IS 'Auth: Not after is a nullable column that contains a timestamp after which the session should be regarded as expired.';
COMMENT ON COLUMN auth.sessions.refresh_token_hmac_key IS 'Holds a HMAC-SHA256 key used to sign refresh tokens for this session.';
COMMENT ON COLUMN auth.sessions.refresh_token_counter IS 'Holds the ID (counter) of the last issued refresh token.';

-- Table: auth.sso_domains
-- Description: Auth: Manages SSO email address domain mapping to an SSO Identity Provider.
CREATE TABLE IF NOT EXISTS auth.sso_domains (
    id uuid NOT NULL,
    sso_provider_id uuid NOT NULL,
    domain text NOT NULL,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    CONSTRAINT sso_domains_pkey PRIMARY KEY (id),
    CONSTRAINT sso_domains_sso_provider_id_fkey FOREIGN KEY (sso_provider_id) REFERENCES auth.sso_providers(id)
);
COMMENT ON TABLE auth.sso_domains IS 'Auth: Manages SSO email address domain mapping to an SSO Identity Provider.';

-- Table: auth.sso_providers
-- Description: Auth: Manages SSO identity provider information; see saml_providers for SAML.
CREATE TABLE IF NOT EXISTS auth.sso_providers (
    id uuid NOT NULL,
    resource_id text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    disabled boolean,
    CONSTRAINT sso_providers_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.sso_providers IS 'Auth: Manages SSO identity provider information; see saml_providers for SAML.';
COMMENT ON COLUMN auth.sso_providers.resource_id IS 'Auth: Uniquely identifies a SSO provider according to a user-chosen resource ID (case insensitive), useful in infrastructure as code.';

-- Table: auth.users
-- Description: Auth: Stores user login data within a secure schema.
CREATE TABLE IF NOT EXISTS auth.users (
    instance_id uuid,
    id uuid NOT NULL,
    aud character varying(255),
    role character varying(255),
    email character varying(255),
    encrypted_password character varying(255),
    email_confirmed_at timestamp with time zone,
    invited_at timestamp with time zone,
    confirmation_token character varying(255),
    confirmation_sent_at timestamp with time zone,
    recovery_token character varying(255),
    recovery_sent_at timestamp with time zone,
    email_change_token_new character varying(255),
    email_change character varying(255),
    email_change_sent_at timestamp with time zone,
    last_sign_in_at timestamp with time zone,
    raw_app_meta_data jsonb,
    raw_user_meta_data jsonb,
    is_super_admin boolean,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    phone text DEFAULT NULL::character varying,
    phone_confirmed_at timestamp with time zone,
    phone_change text DEFAULT ''::character varying,
    phone_change_token character varying(255) DEFAULT ''::character varying,
    phone_change_sent_at timestamp with time zone,
    confirmed_at timestamp with time zone DEFAULT LEAST(email_confirmed_at, phone_confirmed_at),
    email_change_token_current character varying(255) DEFAULT ''::character varying,
    email_change_confirm_status smallint DEFAULT 0,
    banned_until timestamp with time zone,
    reauthentication_token character varying(255) DEFAULT ''::character varying,
    reauthentication_sent_at timestamp with time zone,
    is_sso_user boolean NOT NULL DEFAULT false,
    deleted_at timestamp with time zone,
    is_anonymous boolean NOT NULL DEFAULT false,
    CONSTRAINT users_phone_key UNIQUE (phone),
    CONSTRAINT users_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE auth.users IS 'Auth: Stores user login data within a secure schema.';
COMMENT ON COLUMN auth.users.is_sso_user IS 'Auth: Set this column to true when the account comes from SSO. These accounts can have duplicate emails.';

-- Table: public.image_categories
-- Description: Категории изображений с промптами для OCR
CREATE TABLE IF NOT EXISTS public.image_categories (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    code text NOT NULL,
    description text,
    system_prompt text NOT NULL DEFAULT ''::text,
    user_prompt text NOT NULL DEFAULT ''::text,
    is_default boolean DEFAULT false,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT image_categories_code_key UNIQUE (code),
    CONSTRAINT image_categories_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.image_categories IS 'Категории изображений с промптами для OCR';
COMMENT ON COLUMN public.image_categories.id IS 'Уникальный идентификатор категории';
COMMENT ON COLUMN public.image_categories.name IS 'Отображаемое название категории';
COMMENT ON COLUMN public.image_categories.code IS 'Уникальный код категории (slug)';
COMMENT ON COLUMN public.image_categories.description IS 'Описание категории для пользователя';
COMMENT ON COLUMN public.image_categories.system_prompt IS 'System/Role промпт для модели';
COMMENT ON COLUMN public.image_categories.user_prompt IS 'User Input промпт для модели';
COMMENT ON COLUMN public.image_categories.is_default IS 'Категория по умолчанию для всех новых IMAGE блоков';
COMMENT ON COLUMN public.image_categories.sort_order IS 'Порядок сортировки в списке';
COMMENT ON COLUMN public.image_categories.created_at IS 'Дата и время создания категории';
COMMENT ON COLUMN public.image_categories.updated_at IS 'Дата и время последнего обновления';

-- Table: public.job_files
-- Description: Файлы связанные с OCR задачами
CREATE TABLE IF NOT EXISTS public.job_files (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL,
    file_type text NOT NULL,
    r2_key text NOT NULL,
    file_name text NOT NULL,
    file_size bigint DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb,
    CONSTRAINT job_files_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id),
    CONSTRAINT job_files_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.job_files IS 'Файлы связанные с OCR задачами';
COMMENT ON COLUMN public.job_files.id IS 'Уникальный идентификатор файла';
COMMENT ON COLUMN public.job_files.job_id IS 'Ссылка на задачу (каскадное удаление при удалении job)';
COMMENT ON COLUMN public.job_files.file_type IS 'Тип файла: pdf, blocks, annotation, result, result_md, ocr_html, crop';
COMMENT ON COLUMN public.job_files.r2_key IS 'Полный путь к файлу в R2 storage';
COMMENT ON COLUMN public.job_files.file_name IS 'Имя файла для отображения';
COMMENT ON COLUMN public.job_files.file_size IS 'Размер файла в байтах';
COMMENT ON COLUMN public.job_files.created_at IS 'Дата и время создания записи';
COMMENT ON COLUMN public.job_files.metadata IS 'Метаданные файла (для кропов: block_id, page_index, coords_norm, block_type)';

-- Table: public.job_settings
-- Description: Настройки моделей для OCR задач
CREATE TABLE IF NOT EXISTS public.job_settings (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL,
    text_model text DEFAULT ''::text,
    table_model text DEFAULT ''::text,
    image_model text DEFAULT ''::text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    stamp_model text DEFAULT ''::text,
    is_correction_mode boolean DEFAULT false,
    CONSTRAINT job_settings_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id),
    CONSTRAINT job_settings_job_id_key UNIQUE (job_id),
    CONSTRAINT job_settings_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.job_settings IS 'Настройки моделей для OCR задач';
COMMENT ON COLUMN public.job_settings.id IS 'Уникальный идентификатор настроек';
COMMENT ON COLUMN public.job_settings.job_id IS 'Ссылка на задачу (один-к-одному)';
COMMENT ON COLUMN public.job_settings.text_model IS 'Модель для распознавания текста';
COMMENT ON COLUMN public.job_settings.table_model IS 'Модель для распознавания таблиц';
COMMENT ON COLUMN public.job_settings.image_model IS 'Модель для распознавания изображений';
COMMENT ON COLUMN public.job_settings.created_at IS 'Дата и время создания настроек';
COMMENT ON COLUMN public.job_settings.updated_at IS 'Дата и время последнего обновления';
COMMENT ON COLUMN public.job_settings.stamp_model IS 'Модель для распознавания штампов (IMAGE блоки с code=stamp)';
COMMENT ON COLUMN public.job_settings.is_correction_mode IS 'Режим корректировки: обновить только указанные блоки';

-- Table: public.jobs
-- Description: OCR задачи обработки документов
CREATE TABLE IF NOT EXISTS public.jobs (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    document_id text NOT NULL,
    document_name text NOT NULL,
    task_name text NOT NULL DEFAULT ''::text,
    status text NOT NULL DEFAULT 'queued'::text,
    progress real DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    error_message text,
    engine text DEFAULT ''::text,
    r2_prefix text,
    node_id uuid,
    status_message text,
    migrated_to_node boolean DEFAULT false,
    migrated_at timestamp with time zone,
    client_id text NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    block_stats jsonb,
    phase_data jsonb,
    retry_count integer DEFAULT 0,
    priority integer NOT NULL DEFAULT 0,
    celery_task_id text,
    CONSTRAINT jobs_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.tree_nodes(id),
    CONSTRAINT jobs_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.jobs IS 'OCR задачи обработки документов';
COMMENT ON COLUMN public.jobs.id IS 'Уникальный идентификатор задачи';
COMMENT ON COLUMN public.jobs.document_id IS 'Хеш PDF файла для идентификации';
COMMENT ON COLUMN public.jobs.document_name IS 'Имя PDF файла (например: report.pdf)';
COMMENT ON COLUMN public.jobs.task_name IS 'Пользовательское название задания';
COMMENT ON COLUMN public.jobs.status IS 'Статус задачи: queued, processing, done, error, paused, draft';
COMMENT ON COLUMN public.jobs.progress IS 'Прогресс выполнения (0.0 - 1.0)';
COMMENT ON COLUMN public.jobs.created_at IS 'Дата и время создания задачи';
COMMENT ON COLUMN public.jobs.updated_at IS 'Дата и время последнего обновления';
COMMENT ON COLUMN public.jobs.error_message IS 'Текст ошибки при status=error';
COMMENT ON COLUMN public.jobs.engine IS 'OCR движок: openrouter или datalab';
COMMENT ON COLUMN public.jobs.r2_prefix IS 'Базовый путь файлов задачи в R2 storage';
COMMENT ON COLUMN public.jobs.node_id IS 'ID узла дерева документа (для связи OCR результатов с деревом проектов)';
COMMENT ON COLUMN public.jobs.status_message IS 'Детальное сообщение о текущей операции (отображается в колонке "Детали")';
COMMENT ON COLUMN public.jobs.migrated_to_node IS 'Флаг: результаты перенесены в node_files';
COMMENT ON COLUMN public.jobs.migrated_at IS 'Время переноса результатов в node_files';
COMMENT ON COLUMN public.jobs.client_id IS 'Идентификатор клиента (из ~/.config/CoreStructure/client_id.txt)';
COMMENT ON COLUMN public.jobs.started_at IS 'Время начала обработки задачи';
COMMENT ON COLUMN public.jobs.completed_at IS 'Время завершения обработки задачи';
COMMENT ON COLUMN public.jobs.block_stats IS 'Статистика обработанных блоков (количество по типам)';
COMMENT ON COLUMN public.jobs.phase_data IS 'Детальная информация о фазах обработки OCR (PASS1, PASS2 strips/images)';
COMMENT ON COLUMN public.jobs.retry_count IS 'Количество попыток выполнения задачи (защита от бесконечного зацикливания)';

-- Table: public.node_files
-- Description: Все файлы привязанные к узлам дерева (PDF, аннотации, markdown, кропы)
CREATE TABLE IF NOT EXISTS public.node_files (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    node_id uuid NOT NULL,
    file_type text NOT NULL,
    r2_key text NOT NULL,
    file_name text NOT NULL,
    file_size bigint DEFAULT 0,
    mime_type text DEFAULT 'application/octet-stream'::text,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT node_files_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.tree_nodes(id),
    CONSTRAINT node_files_node_id_r2_key_unique UNIQUE (node_id),
    CONSTRAINT node_files_node_id_r2_key_unique UNIQUE (r2_key),
    CONSTRAINT node_files_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.node_files IS 'Все файлы привязанные к узлам дерева (PDF, аннотации, markdown, кропы)';
COMMENT ON COLUMN public.node_files.id IS 'Уникальный идентификатор файла';
COMMENT ON COLUMN public.node_files.node_id IS 'Ссылка на узел дерева';
COMMENT ON COLUMN public.node_files.file_type IS 'Тип файла: pdf, annotation, result_md, result_zip, crop, image, ocr_html, result_json, crops_folder, qa_manifest, blocks_index';
COMMENT ON COLUMN public.node_files.r2_key IS 'Ключ объекта в R2 storage';
COMMENT ON COLUMN public.node_files.file_name IS 'Имя файла для отображения';
COMMENT ON COLUMN public.node_files.file_size IS 'Размер файла в байтах';
COMMENT ON COLUMN public.node_files.mime_type IS 'MIME тип файла (например: application/pdf)';
COMMENT ON COLUMN public.node_files.metadata IS 'Метаданные: version, page_index для кропов и т.д.';
COMMENT ON COLUMN public.node_files.created_at IS 'Дата и время создания записи';
COMMENT ON COLUMN public.node_files.updated_at IS 'Дата и время последнего обновления';

-- Table: public.section_types
-- Description: Справочник типов разделов документации (АР, КР, ОВ и др.)
CREATE TABLE IF NOT EXISTS public.section_types (
    id integer NOT NULL DEFAULT nextval('section_types_id_seq'::regclass),
    code text NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0,
    CONSTRAINT section_types_code_key UNIQUE (code),
    CONSTRAINT section_types_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.section_types IS 'Справочник типов разделов документации (АР, КР, ОВ и др.)';
COMMENT ON COLUMN public.section_types.id IS 'Уникальный идентификатор типа раздела';
COMMENT ON COLUMN public.section_types.code IS 'Код раздела (например: АР, КР, ОВ)';
COMMENT ON COLUMN public.section_types.name IS 'Полное название раздела';
COMMENT ON COLUMN public.section_types.sort_order IS 'Порядок сортировки в списке';

-- Table: public.stage_types
-- Description: Справочник типов стадий проекта (ПД, РД)
CREATE TABLE IF NOT EXISTS public.stage_types (
    id integer NOT NULL DEFAULT nextval('stage_types_id_seq'::regclass),
    code text NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0,
    CONSTRAINT stage_types_code_key UNIQUE (code),
    CONSTRAINT stage_types_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.stage_types IS 'Справочник типов стадий проекта (ПД, РД)';
COMMENT ON COLUMN public.stage_types.id IS 'Уникальный идентификатор типа стадии';
COMMENT ON COLUMN public.stage_types.code IS 'Код стадии (например: ПД, РД)';
COMMENT ON COLUMN public.stage_types.name IS 'Полное название стадии';
COMMENT ON COLUMN public.stage_types.sort_order IS 'Порядок сортировки в списке';

-- Table: public.tree_nodes
-- Description: Дерево проектов - иерархическая структура узлов
CREATE TABLE IF NOT EXISTS public.tree_nodes (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    parent_id uuid,
    node_type text NOT NULL,
    name text NOT NULL,
    code text,
    version integer DEFAULT 1,
    status text DEFAULT 'active'::text,
    attributes jsonb DEFAULT '{}'::jsonb,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    pdf_status text DEFAULT 'unknown'::text,
    pdf_status_message text,
    pdf_status_updated_at timestamp with time zone,
    is_locked boolean DEFAULT false,
    path text,
    depth integer DEFAULT 0,
    children_count integer DEFAULT 0,
    descendants_count integer DEFAULT 0,
    files_count integer DEFAULT 0,
    CONSTRAINT tree_nodes_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.tree_nodes(id),
    CONSTRAINT tree_nodes_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE public.tree_nodes IS 'Дерево проектов - иерархическая структура узлов';
COMMENT ON COLUMN public.tree_nodes.id IS 'Уникальный идентификатор узла';
COMMENT ON COLUMN public.tree_nodes.parent_id IS 'Ссылка на родительский узел (NULL для корневых)';
COMMENT ON COLUMN public.tree_nodes.node_type IS 'Тип узла: client, project, section, stage, task, document';
COMMENT ON COLUMN public.tree_nodes.name IS 'Отображаемое имя узла в дереве';
COMMENT ON COLUMN public.tree_nodes.code IS 'Опциональный код узла для идентификации';
COMMENT ON COLUMN public.tree_nodes.version IS 'Номер версии узла для оптимистичной блокировки';
COMMENT ON COLUMN public.tree_nodes.status IS 'Статус узла: active, completed, archived';
COMMENT ON COLUMN public.tree_nodes.attributes IS 'Дополнительные атрибуты узла (JSON)';
COMMENT ON COLUMN public.tree_nodes.sort_order IS 'Порядок сортировки в дереве';
COMMENT ON COLUMN public.tree_nodes.created_at IS 'Дата и время создания узла';
COMMENT ON COLUMN public.tree_nodes.updated_at IS 'Дата и время последнего обновления';
COMMENT ON COLUMN public.tree_nodes.pdf_status IS 'Статус PDF: unknown, processing, done, error';
COMMENT ON COLUMN public.tree_nodes.pdf_status_message IS 'Детальное сообщение о статусе PDF';
COMMENT ON COLUMN public.tree_nodes.pdf_status_updated_at IS 'Время последнего обновления статуса PDF';
COMMENT ON COLUMN public.tree_nodes.is_locked IS 'Флаг блокировки документа от изменений (удаление, переименование, изменение блоков, запуск OCR)';
COMMENT ON COLUMN public.tree_nodes.path IS 'Materialized path: uuid1.uuid2.uuid3 для быстрого поиска предков/потомков';
COMMENT ON COLUMN public.tree_nodes.depth IS 'Глубина узла от корня (0 = корневой проект)';
COMMENT ON COLUMN public.tree_nodes.children_count IS 'Количество прямых дочерних узлов (обновляется триггером)';
COMMENT ON COLUMN public.tree_nodes.descendants_count IS 'Количество всех потомков (обновляется триггером)';
COMMENT ON COLUMN public.tree_nodes.files_count IS 'Количество файлов в node_files для этого узла (обновляется триггером)';

-- Table: realtime.messages_2026_01_16
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_16 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_16_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_16_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_17
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_17 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_17_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_17_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_18
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_18 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_18_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_18_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_19
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_19 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_19_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_19_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_20
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_20 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_20_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_20_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_21
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_21 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_21_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_21_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.messages_2026_01_22
CREATE TABLE IF NOT EXISTS realtime.messages_2026_01_22 (
    topic text NOT NULL,
    extension text NOT NULL,
    payload jsonb,
    event text,
    private boolean DEFAULT false,
    updated_at timestamp without time zone NOT NULL DEFAULT now(),
    inserted_at timestamp without time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    CONSTRAINT messages_2026_01_22_pkey PRIMARY KEY (id),
    CONSTRAINT messages_2026_01_22_pkey PRIMARY KEY (inserted_at)
);

-- Table: realtime.schema_migrations
CREATE TABLE IF NOT EXISTS realtime.schema_migrations (
    version bigint NOT NULL,
    inserted_at timestamp(0) without time zone,
    CONSTRAINT schema_migrations_pkey PRIMARY KEY (version)
);

-- Table: realtime.subscription
CREATE TABLE IF NOT EXISTS realtime.subscription (
    id bigint NOT NULL,
    subscription_id uuid NOT NULL,
    entity regclass NOT NULL,
    filters realtime.user_defined_filter[] NOT NULL DEFAULT '{}'::realtime.user_defined_filter[],
    claims jsonb NOT NULL,
    claims_role regrole NOT NULL DEFAULT realtime.to_regrole((claims ->> 'role'::text)),
    created_at timestamp without time zone NOT NULL DEFAULT timezone('utc'::text, now()),
    action_filter text DEFAULT '*'::text,
    CONSTRAINT pk_subscription PRIMARY KEY (id)
);

-- Table: storage.buckets
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text NOT NULL,
    name text NOT NULL,
    owner uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    public boolean DEFAULT false,
    avif_autodetection boolean DEFAULT false,
    file_size_limit bigint,
    allowed_mime_types text[],
    owner_id text,
    type storage.buckettype NOT NULL DEFAULT 'STANDARD'::storage.buckettype,
    CONSTRAINT buckets_pkey PRIMARY KEY (id)
);
COMMENT ON COLUMN storage.buckets.owner IS 'Field is deprecated, use owner_id instead';

-- Table: storage.buckets_analytics
CREATE TABLE IF NOT EXISTS storage.buckets_analytics (
    name text NOT NULL,
    type storage.buckettype NOT NULL DEFAULT 'ANALYTICS'::storage.buckettype,
    format text NOT NULL DEFAULT 'ICEBERG'::text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    deleted_at timestamp with time zone,
    CONSTRAINT buckets_analytics_pkey PRIMARY KEY (id)
);

-- Table: storage.buckets_vectors
CREATE TABLE IF NOT EXISTS storage.buckets_vectors (
    id text NOT NULL,
    type storage.buckettype NOT NULL DEFAULT 'VECTOR'::storage.buckettype,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT buckets_vectors_pkey PRIMARY KEY (id)
);

-- Table: storage.migrations
CREATE TABLE IF NOT EXISTS storage.migrations (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    hash character varying(40) NOT NULL,
    executed_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT migrations_name_key UNIQUE (name),
    CONSTRAINT migrations_pkey PRIMARY KEY (id)
);

-- Table: storage.objects
CREATE TABLE IF NOT EXISTS storage.objects (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    bucket_id text,
    name text,
    owner uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    last_accessed_at timestamp with time zone DEFAULT now(),
    metadata jsonb,
    path_tokens text[] DEFAULT string_to_array(name, '/'::text),
    version text,
    owner_id text,
    user_metadata jsonb,
    CONSTRAINT objects_bucketId_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id),
    CONSTRAINT objects_pkey PRIMARY KEY (id)
);
COMMENT ON COLUMN storage.objects.owner IS 'Field is deprecated, use owner_id instead';

-- Table: storage.s3_multipart_uploads
CREATE TABLE IF NOT EXISTS storage.s3_multipart_uploads (
    id text NOT NULL,
    in_progress_size bigint NOT NULL DEFAULT 0,
    upload_signature text NOT NULL,
    bucket_id text NOT NULL,
    key text NOT NULL,
    version text NOT NULL,
    owner_id text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    user_metadata jsonb,
    CONSTRAINT s3_multipart_uploads_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id),
    CONSTRAINT s3_multipart_uploads_pkey PRIMARY KEY (id)
);

-- Table: storage.s3_multipart_uploads_parts
CREATE TABLE IF NOT EXISTS storage.s3_multipart_uploads_parts (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    upload_id text NOT NULL,
    size bigint NOT NULL DEFAULT 0,
    part_number integer NOT NULL,
    bucket_id text NOT NULL,
    key text NOT NULL,
    etag text NOT NULL,
    owner_id text,
    version text NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT s3_multipart_uploads_parts_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets(id),
    CONSTRAINT s3_multipart_uploads_parts_pkey PRIMARY KEY (id),
    CONSTRAINT s3_multipart_uploads_parts_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES storage.s3_multipart_uploads(id)
);

-- Table: storage.vector_indexes
CREATE TABLE IF NOT EXISTS storage.vector_indexes (
    id text NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    bucket_id text NOT NULL,
    data_type text NOT NULL,
    dimension integer NOT NULL,
    distance_metric text NOT NULL,
    metadata_configuration jsonb,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT vector_indexes_bucket_id_fkey FOREIGN KEY (bucket_id) REFERENCES storage.buckets_vectors(id),
    CONSTRAINT vector_indexes_pkey PRIMARY KEY (id)
);

-- Table: vault.secrets
-- Description: Table with encrypted `secret` column for storing sensitive information on disk.
CREATE TABLE IF NOT EXISTS vault.secrets (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text,
    description text NOT NULL DEFAULT ''::text,
    secret text NOT NULL,
    key_id uuid,
    nonce bytea DEFAULT vault._crypto_aead_det_noncegen(),
    created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT secrets_pkey PRIMARY KEY (id)
);
COMMENT ON TABLE vault.secrets IS 'Table with encrypted `secret` column for storing sensitive information on disk.';


-- ============================================
-- ENUM TYPES
-- ============================================

CREATE TYPE auth.aal_level AS ENUM ('aal1', 'aal2', 'aal3');

CREATE TYPE auth.code_challenge_method AS ENUM ('s256', 'plain');

CREATE TYPE auth.factor_status AS ENUM ('unverified', 'verified');

CREATE TYPE auth.factor_type AS ENUM ('totp', 'webauthn', 'phone');

CREATE TYPE auth.oauth_authorization_status AS ENUM ('pending', 'approved', 'denied', 'expired');

CREATE TYPE auth.oauth_client_type AS ENUM ('public', 'confidential');

CREATE TYPE auth.oauth_registration_type AS ENUM ('dynamic', 'manual');

CREATE TYPE auth.oauth_response_type AS ENUM ('code');

CREATE TYPE auth.one_time_token_type AS ENUM ('confirmation_token', 'reauthentication_token', 'recovery_token', 'email_change_token_new', 'email_change_token_current', 'phone_change_token');

CREATE TYPE realtime.action AS ENUM ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ERROR');

CREATE TYPE realtime.equality_op AS ENUM ('eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'in');

CREATE TYPE storage.buckettype AS ENUM ('STANDARD', 'ANALYTICS', 'VECTOR');


-- ============================================
-- VIEWS
-- ============================================

-- View: extensions.pg_stat_statements
CREATE OR REPLACE VIEW extensions.pg_stat_statements AS
 SELECT userid,
    dbid,
    toplevel,
    queryid,
    query,
    plans,
    total_plan_time,
    min_plan_time,
    max_plan_time,
    mean_plan_time,
    stddev_plan_time,
    calls,
    total_exec_time,
    min_exec_time,
    max_exec_time,
    mean_exec_time,
    stddev_exec_time,
    rows,
    shared_blks_hit,
    shared_blks_read,
    shared_blks_dirtied,
    shared_blks_written,
    local_blks_hit,
    local_blks_read,
    local_blks_dirtied,
    local_blks_written,
    temp_blks_read,
    temp_blks_written,
    shared_blk_read_time,
    shared_blk_write_time,
    local_blk_read_time,
    local_blk_write_time,
    temp_blk_read_time,
    temp_blk_write_time,
    wal_records,
    wal_fpi,
    wal_bytes,
    jit_functions,
    jit_generation_time,
    jit_inlining_count,
    jit_inlining_time,
    jit_optimization_count,
    jit_optimization_time,
    jit_emission_count,
    jit_emission_time,
    jit_deform_count,
    jit_deform_time,
    stats_since,
    minmax_stats_since
   FROM pg_stat_statements(true) pg_stat_statements(userid, dbid, toplevel, queryid, query, plans, total_plan_time, min_plan_time, max_plan_time, mean_plan_time, stddev_plan_time, calls, total_exec_time, min_exec_time, max_exec_time, mean_exec_time, stddev_exec_time, rows, shared_blks_hit, shared_blks_read, shared_blks_dirtied, shared_blks_written, local_blks_hit, local_blks_read, local_blks_dirtied, local_blks_written, temp_blks_read, temp_blks_written, shared_blk_read_time, shared_blk_write_time, local_blk_read_time, local_blk_write_time, temp_blk_read_time, temp_blk_write_time, wal_records, wal_fpi, wal_bytes, jit_functions, jit_generation_time, jit_inlining_count, jit_inlining_time, jit_optimization_count, jit_optimization_time, jit_emission_count, jit_emission_time, jit_deform_count, jit_deform_time, stats_since, minmax_stats_since);

-- View: extensions.pg_stat_statements_info
CREATE OR REPLACE VIEW extensions.pg_stat_statements_info AS
 SELECT dealloc,
    stats_reset
   FROM pg_stat_statements_info() pg_stat_statements_info(dealloc, stats_reset);

-- View: vault.decrypted_secrets
CREATE OR REPLACE VIEW vault.decrypted_secrets AS
 SELECT id,
    name,
    description,
    secret,
    convert_from(vault._crypto_aead_det_decrypt(message => decode(secret, 'base64'::text), additional => convert_to((id)::text, 'utf8'::name), key_id => (0)::bigint, context => '\x7067736f6469756d'::bytea, nonce => nonce), 'utf8'::name) AS decrypted_secret,
    key_id,
    nonce,
    created_at,
    updated_at
   FROM vault.secrets s;


-- ============================================
-- FUNCTIONS
-- ============================================

-- Function: auth.email
-- Description: Deprecated. Use auth.jwt() -> 'email' instead.
CREATE OR REPLACE FUNCTION auth.email()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.email', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'email')
  )::text
$function$


-- Function: auth.jwt
CREATE OR REPLACE FUNCTION auth.jwt()
 RETURNS jsonb
 LANGUAGE sql
 STABLE
AS $function$
  select 
    coalesce(
        nullif(current_setting('request.jwt.claim', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')
    )::jsonb
$function$


-- Function: auth.role
-- Description: Deprecated. Use auth.jwt() -> 'role' instead.
CREATE OR REPLACE FUNCTION auth.role()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role')
  )::text
$function$


-- Function: auth.uid
-- Description: Deprecated. Use auth.jwt() -> 'sub' instead.
CREATE OR REPLACE FUNCTION auth.uid()
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
  select 
  coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$function$


-- Function: extensions.armor
CREATE OR REPLACE FUNCTION extensions.armor(bytea, text[], text[])
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_armor$function$


-- Function: extensions.armor
CREATE OR REPLACE FUNCTION extensions.armor(bytea)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_armor$function$


-- Function: extensions.crypt
CREATE OR REPLACE FUNCTION extensions.crypt(text, text)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_crypt$function$


-- Function: extensions.dearmor
CREATE OR REPLACE FUNCTION extensions.dearmor(text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_dearmor$function$


-- Function: extensions.decrypt
CREATE OR REPLACE FUNCTION extensions.decrypt(bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_decrypt$function$


-- Function: extensions.decrypt_iv
CREATE OR REPLACE FUNCTION extensions.decrypt_iv(bytea, bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_decrypt_iv$function$


-- Function: extensions.digest
CREATE OR REPLACE FUNCTION extensions.digest(text, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_digest$function$


-- Function: extensions.digest
CREATE OR REPLACE FUNCTION extensions.digest(bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_digest$function$


-- Function: extensions.encrypt
CREATE OR REPLACE FUNCTION extensions.encrypt(bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_encrypt$function$


-- Function: extensions.encrypt_iv
CREATE OR REPLACE FUNCTION extensions.encrypt_iv(bytea, bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_encrypt_iv$function$


-- Function: extensions.gen_random_bytes
CREATE OR REPLACE FUNCTION extensions.gen_random_bytes(integer)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_random_bytes$function$


-- Function: extensions.gen_random_uuid
CREATE OR REPLACE FUNCTION extensions.gen_random_uuid()
 RETURNS uuid
 LANGUAGE c
 PARALLEL SAFE
AS '$libdir/pgcrypto', $function$pg_random_uuid$function$


-- Function: extensions.gen_salt
CREATE OR REPLACE FUNCTION extensions.gen_salt(text, integer)
 RETURNS text
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_gen_salt_rounds$function$


-- Function: extensions.gen_salt
CREATE OR REPLACE FUNCTION extensions.gen_salt(text)
 RETURNS text
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_gen_salt$function$


-- Function: extensions.grant_pg_cron_access
-- Description: Grants access to pg_cron
CREATE OR REPLACE FUNCTION extensions.grant_pg_cron_access()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF EXISTS (
    SELECT
    FROM pg_event_trigger_ddl_commands() AS ev
    JOIN pg_extension AS ext
    ON ev.objid = ext.oid
    WHERE ext.extname = 'pg_cron'
  )
  THEN
    grant usage on schema cron to postgres with grant option;

    alter default privileges in schema cron grant all on tables to postgres with grant option;
    alter default privileges in schema cron grant all on functions to postgres with grant option;
    alter default privileges in schema cron grant all on sequences to postgres with grant option;

    alter default privileges for user supabase_admin in schema cron grant all
        on sequences to postgres with grant option;
    alter default privileges for user supabase_admin in schema cron grant all
        on tables to postgres with grant option;
    alter default privileges for user supabase_admin in schema cron grant all
        on functions to postgres with grant option;

    grant all privileges on all tables in schema cron to postgres with grant option;
    revoke all on table cron.job from postgres;
    grant select on table cron.job to postgres with grant option;
  END IF;
END;
$function$


-- Function: extensions.grant_pg_graphql_access
-- Description: Grants access to pg_graphql
CREATE OR REPLACE FUNCTION extensions.grant_pg_graphql_access()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    func_is_graphql_resolve bool;
BEGIN
    func_is_graphql_resolve = (
        SELECT n.proname = 'resolve'
        FROM pg_event_trigger_ddl_commands() AS ev
        LEFT JOIN pg_catalog.pg_proc AS n
        ON ev.objid = n.oid
    );

    IF func_is_graphql_resolve
    THEN
        -- Update public wrapper to pass all arguments through to the pg_graphql resolve func
        DROP FUNCTION IF EXISTS graphql_public.graphql;
        create or replace function graphql_public.graphql(
            "operationName" text default null,
            query text default null,
            variables jsonb default null,
            extensions jsonb default null
        )
            returns jsonb
            language sql
        as $$
            select graphql.resolve(
                query := query,
                variables := coalesce(variables, '{}'),
                "operationName" := "operationName",
                extensions := extensions
            );
        $$;

        -- This hook executes when `graphql.resolve` is created. That is not necessarily the last
        -- function in the extension so we need to grant permissions on existing entities AND
        -- update default permissions to any others that are created after `graphql.resolve`
        grant usage on schema graphql to postgres, anon, authenticated, service_role;
        grant select on all tables in schema graphql to postgres, anon, authenticated, service_role;
        grant execute on all functions in schema graphql to postgres, anon, authenticated, service_role;
        grant all on all sequences in schema graphql to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on tables to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on functions to postgres, anon, authenticated, service_role;
        alter default privileges in schema graphql grant all on sequences to postgres, anon, authenticated, service_role;

        -- Allow postgres role to allow granting usage on graphql and graphql_public schemas to custom roles
        grant usage on schema graphql_public to postgres with grant option;
        grant usage on schema graphql to postgres with grant option;
    END IF;

END;
$function$


-- Function: extensions.grant_pg_net_access
-- Description: Grants access to pg_net
CREATE OR REPLACE FUNCTION extensions.grant_pg_net_access()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_event_trigger_ddl_commands() AS ev
    JOIN pg_extension AS ext
    ON ev.objid = ext.oid
    WHERE ext.extname = 'pg_net'
  )
  THEN
    IF NOT EXISTS (
      SELECT 1
      FROM pg_roles
      WHERE rolname = 'supabase_functions_admin'
    )
    THEN
      CREATE USER supabase_functions_admin NOINHERIT CREATEROLE LOGIN NOREPLICATION;
    END IF;

    GRANT USAGE ON SCHEMA net TO supabase_functions_admin, postgres, anon, authenticated, service_role;

    IF EXISTS (
      SELECT FROM pg_extension
      WHERE extname = 'pg_net'
      -- all versions in use on existing projects as of 2025-02-20
      -- version 0.12.0 onwards don't need these applied
      AND extversion IN ('0.2', '0.6', '0.7', '0.7.1', '0.8', '0.10.0', '0.11.0')
    ) THEN
      ALTER function net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) SECURITY DEFINER;
      ALTER function net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) SECURITY DEFINER;

      ALTER function net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) SET search_path = net;
      ALTER function net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) SET search_path = net;

      REVOKE ALL ON FUNCTION net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) FROM PUBLIC;
      REVOKE ALL ON FUNCTION net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) FROM PUBLIC;

      GRANT EXECUTE ON FUNCTION net.http_get(url text, params jsonb, headers jsonb, timeout_milliseconds integer) TO supabase_functions_admin, postgres, anon, authenticated, service_role;
      GRANT EXECUTE ON FUNCTION net.http_post(url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds integer) TO supabase_functions_admin, postgres, anon, authenticated, service_role;
    END IF;
  END IF;
END;
$function$


-- Function: extensions.hmac
CREATE OR REPLACE FUNCTION extensions.hmac(bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_hmac$function$


-- Function: extensions.hmac
CREATE OR REPLACE FUNCTION extensions.hmac(text, text, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pg_hmac$function$


-- Function: extensions.pg_stat_statements
CREATE OR REPLACE FUNCTION extensions.pg_stat_statements(showtext boolean, OUT userid oid, OUT dbid oid, OUT toplevel boolean, OUT queryid bigint, OUT query text, OUT plans bigint, OUT total_plan_time double precision, OUT min_plan_time double precision, OUT max_plan_time double precision, OUT mean_plan_time double precision, OUT stddev_plan_time double precision, OUT calls bigint, OUT total_exec_time double precision, OUT min_exec_time double precision, OUT max_exec_time double precision, OUT mean_exec_time double precision, OUT stddev_exec_time double precision, OUT rows bigint, OUT shared_blks_hit bigint, OUT shared_blks_read bigint, OUT shared_blks_dirtied bigint, OUT shared_blks_written bigint, OUT local_blks_hit bigint, OUT local_blks_read bigint, OUT local_blks_dirtied bigint, OUT local_blks_written bigint, OUT temp_blks_read bigint, OUT temp_blks_written bigint, OUT shared_blk_read_time double precision, OUT shared_blk_write_time double precision, OUT local_blk_read_time double precision, OUT local_blk_write_time double precision, OUT temp_blk_read_time double precision, OUT temp_blk_write_time double precision, OUT wal_records bigint, OUT wal_fpi bigint, OUT wal_bytes numeric, OUT jit_functions bigint, OUT jit_generation_time double precision, OUT jit_inlining_count bigint, OUT jit_inlining_time double precision, OUT jit_optimization_count bigint, OUT jit_optimization_time double precision, OUT jit_emission_count bigint, OUT jit_emission_time double precision, OUT jit_deform_count bigint, OUT jit_deform_time double precision, OUT stats_since timestamp with time zone, OUT minmax_stats_since timestamp with time zone)
 RETURNS SETOF record
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_1_11$function$


-- Function: extensions.pg_stat_statements_info
CREATE OR REPLACE FUNCTION extensions.pg_stat_statements_info(OUT dealloc bigint, OUT stats_reset timestamp with time zone)
 RETURNS record
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_info$function$


-- Function: extensions.pg_stat_statements_reset
CREATE OR REPLACE FUNCTION extensions.pg_stat_statements_reset(userid oid DEFAULT 0, dbid oid DEFAULT 0, queryid bigint DEFAULT 0, minmax_only boolean DEFAULT false)
 RETURNS timestamp with time zone
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_reset_1_11$function$


-- Function: extensions.pgp_armor_headers
CREATE OR REPLACE FUNCTION extensions.pgp_armor_headers(text, OUT key text, OUT value text)
 RETURNS SETOF record
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_armor_headers$function$


-- Function: extensions.pgp_key_id
CREATE OR REPLACE FUNCTION extensions.pgp_key_id(bytea)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_key_id_w$function$


-- Function: extensions.pgp_pub_decrypt
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt(bytea, bytea, text)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_text$function$


-- Function: extensions.pgp_pub_decrypt
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt(bytea, bytea)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_text$function$


-- Function: extensions.pgp_pub_decrypt
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt(bytea, bytea, text, text)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_text$function$


-- Function: extensions.pgp_pub_decrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt_bytea(bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_bytea$function$


-- Function: extensions.pgp_pub_decrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt_bytea(bytea, bytea, text, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_bytea$function$


-- Function: extensions.pgp_pub_decrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_pub_decrypt_bytea(bytea, bytea)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_decrypt_bytea$function$


-- Function: extensions.pgp_pub_encrypt
CREATE OR REPLACE FUNCTION extensions.pgp_pub_encrypt(text, bytea, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_encrypt_text$function$


-- Function: extensions.pgp_pub_encrypt
CREATE OR REPLACE FUNCTION extensions.pgp_pub_encrypt(text, bytea)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_encrypt_text$function$


-- Function: extensions.pgp_pub_encrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_pub_encrypt_bytea(bytea, bytea)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_encrypt_bytea$function$


-- Function: extensions.pgp_pub_encrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_pub_encrypt_bytea(bytea, bytea, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_pub_encrypt_bytea$function$


-- Function: extensions.pgp_sym_decrypt
CREATE OR REPLACE FUNCTION extensions.pgp_sym_decrypt(bytea, text, text)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_decrypt_text$function$


-- Function: extensions.pgp_sym_decrypt
CREATE OR REPLACE FUNCTION extensions.pgp_sym_decrypt(bytea, text)
 RETURNS text
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_decrypt_text$function$


-- Function: extensions.pgp_sym_decrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_sym_decrypt_bytea(bytea, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_decrypt_bytea$function$


-- Function: extensions.pgp_sym_decrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_sym_decrypt_bytea(bytea, text, text)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_decrypt_bytea$function$


-- Function: extensions.pgp_sym_encrypt
CREATE OR REPLACE FUNCTION extensions.pgp_sym_encrypt(text, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_encrypt_text$function$


-- Function: extensions.pgp_sym_encrypt
CREATE OR REPLACE FUNCTION extensions.pgp_sym_encrypt(text, text, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_encrypt_text$function$


-- Function: extensions.pgp_sym_encrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_sym_encrypt_bytea(bytea, text, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_encrypt_bytea$function$


-- Function: extensions.pgp_sym_encrypt_bytea
CREATE OR REPLACE FUNCTION extensions.pgp_sym_encrypt_bytea(bytea, text)
 RETURNS bytea
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pgcrypto', $function$pgp_sym_encrypt_bytea$function$


-- Function: extensions.pgrst_ddl_watch
CREATE OR REPLACE FUNCTION extensions.pgrst_ddl_watch()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands()
  LOOP
    IF cmd.command_tag IN (
      'CREATE SCHEMA', 'ALTER SCHEMA'
    , 'CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO', 'ALTER TABLE'
    , 'CREATE FOREIGN TABLE', 'ALTER FOREIGN TABLE'
    , 'CREATE VIEW', 'ALTER VIEW'
    , 'CREATE MATERIALIZED VIEW', 'ALTER MATERIALIZED VIEW'
    , 'CREATE FUNCTION', 'ALTER FUNCTION'
    , 'CREATE TRIGGER'
    , 'CREATE TYPE', 'ALTER TYPE'
    , 'CREATE RULE'
    , 'COMMENT'
    )
    -- don't notify in case of CREATE TEMP table or other objects created on pg_temp
    AND cmd.schema_name is distinct from 'pg_temp'
    THEN
      NOTIFY pgrst, 'reload schema';
    END IF;
  END LOOP;
END; $function$


-- Function: extensions.pgrst_drop_watch
CREATE OR REPLACE FUNCTION extensions.pgrst_drop_watch()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  obj record;
BEGIN
  FOR obj IN SELECT * FROM pg_event_trigger_dropped_objects()
  LOOP
    IF obj.object_type IN (
      'schema'
    , 'table'
    , 'foreign table'
    , 'view'
    , 'materialized view'
    , 'function'
    , 'trigger'
    , 'type'
    , 'rule'
    )
    AND obj.is_temporary IS false -- no pg_temp objects
    THEN
      NOTIFY pgrst, 'reload schema';
    END IF;
  END LOOP;
END; $function$


-- Function: extensions.set_graphql_placeholder
-- Description: Reintroduces placeholder function for graphql_public.graphql
CREATE OR REPLACE FUNCTION extensions.set_graphql_placeholder()
 RETURNS event_trigger
 LANGUAGE plpgsql
AS $function$
    DECLARE
    graphql_is_dropped bool;
    BEGIN
    graphql_is_dropped = (
        SELECT ev.schema_name = 'graphql_public'
        FROM pg_event_trigger_dropped_objects() AS ev
        WHERE ev.schema_name = 'graphql_public'
    );

    IF graphql_is_dropped
    THEN
        create or replace function graphql_public.graphql(
            "operationName" text default null,
            query text default null,
            variables jsonb default null,
            extensions jsonb default null
        )
            returns jsonb
            language plpgsql
        as $$
            DECLARE
                server_version float;
            BEGIN
                server_version = (SELECT (SPLIT_PART((select version()), ' ', 2))::float);

                IF server_version >= 14 THEN
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql extension is not enabled.'
                            )
                        )
                    );
                ELSE
                    RETURN jsonb_build_object(
                        'errors', jsonb_build_array(
                            jsonb_build_object(
                                'message', 'pg_graphql is only available on projects running Postgres 14 onwards.'
                            )
                        )
                    );
                END IF;
            END;
        $$;
    END IF;

    END;
$function$


-- Function: extensions.uuid_generate_v1
CREATE OR REPLACE FUNCTION extensions.uuid_generate_v1()
 RETURNS uuid
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_generate_v1$function$


-- Function: extensions.uuid_generate_v1mc
CREATE OR REPLACE FUNCTION extensions.uuid_generate_v1mc()
 RETURNS uuid
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_generate_v1mc$function$


-- Function: extensions.uuid_generate_v3
CREATE OR REPLACE FUNCTION extensions.uuid_generate_v3(namespace uuid, name text)
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_generate_v3$function$


-- Function: extensions.uuid_generate_v4
CREATE OR REPLACE FUNCTION extensions.uuid_generate_v4()
 RETURNS uuid
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_generate_v4$function$


-- Function: extensions.uuid_generate_v5
CREATE OR REPLACE FUNCTION extensions.uuid_generate_v5(namespace uuid, name text)
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_generate_v5$function$


-- Function: extensions.uuid_nil
CREATE OR REPLACE FUNCTION extensions.uuid_nil()
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_nil$function$


-- Function: extensions.uuid_ns_dns
CREATE OR REPLACE FUNCTION extensions.uuid_ns_dns()
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_ns_dns$function$


-- Function: extensions.uuid_ns_oid
CREATE OR REPLACE FUNCTION extensions.uuid_ns_oid()
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_ns_oid$function$


-- Function: extensions.uuid_ns_url
CREATE OR REPLACE FUNCTION extensions.uuid_ns_url()
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_ns_url$function$


-- Function: extensions.uuid_ns_x500
CREATE OR REPLACE FUNCTION extensions.uuid_ns_x500()
 RETURNS uuid
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/uuid-ossp', $function$uuid_ns_x500$function$


-- Function: graphql._internal_resolve
CREATE OR REPLACE FUNCTION graphql._internal_resolve(query text, variables jsonb DEFAULT '{}'::jsonb, "operationName" text DEFAULT NULL::text, extensions jsonb DEFAULT NULL::jsonb)
 RETURNS jsonb
 LANGUAGE c
AS '$libdir/pg_graphql', $function$resolve_wrapper$function$


-- Function: graphql.comment_directive
CREATE OR REPLACE FUNCTION graphql.comment_directive(comment_ text)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE
AS $function$
    /*
    comment on column public.account.name is '@graphql.name: myField'
    */
    select
        coalesce(
            (
                regexp_match(
                    comment_,
                    '@graphql\((.+)\)'
                )
            )[1]::jsonb,
            jsonb_build_object()
        )
$function$


-- Function: graphql.exception
CREATE OR REPLACE FUNCTION graphql.exception(message text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
begin
    raise exception using errcode='22000', message=message;
end;
$function$


-- Function: graphql.get_schema_version
CREATE OR REPLACE FUNCTION graphql.get_schema_version()
 RETURNS integer
 LANGUAGE sql
 SECURITY DEFINER
AS $function$
    select last_value from graphql.seq_schema_version;
$function$


-- Function: graphql.increment_schema_version
CREATE OR REPLACE FUNCTION graphql.increment_schema_version()
 RETURNS event_trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
begin
    perform pg_catalog.nextval('graphql.seq_schema_version');
end;
$function$


-- Function: graphql.resolve
CREATE OR REPLACE FUNCTION graphql.resolve(query text, variables jsonb DEFAULT '{}'::jsonb, "operationName" text DEFAULT NULL::text, extensions jsonb DEFAULT NULL::jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
declare
    res jsonb;
    message_text text;
begin
  begin
    select graphql._internal_resolve("query" := "query",
                                     "variables" := "variables",
                                     "operationName" := "operationName",
                                     "extensions" := "extensions") into res;
    return res;
  exception
    when others then
    get stacked diagnostics message_text = message_text;
    return
    jsonb_build_object('data', null,
                       'errors', jsonb_build_array(jsonb_build_object('message', message_text)));
  end;
end;
$function$


-- Function: graphql_public.graphql
CREATE OR REPLACE FUNCTION graphql_public.graphql("operationName" text DEFAULT NULL::text, query text DEFAULT NULL::text, variables jsonb DEFAULT NULL::jsonb, extensions jsonb DEFAULT NULL::jsonb)
 RETURNS jsonb
 LANGUAGE sql
AS $function$
            select graphql.resolve(
                query := query,
                variables := coalesce(variables, '{}'),
                "operationName" := "operationName",
                extensions := extensions
            );
        $function$


-- Function: pgbouncer.get_auth
CREATE OR REPLACE FUNCTION pgbouncer.get_auth(p_usename text)
 RETURNS TABLE(username text, password text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
  BEGIN
      RAISE DEBUG 'PgBouncer auth request: %', p_usename;

      RETURN QUERY
      SELECT
          rolname::text,
          CASE WHEN rolvaliduntil < now()
              THEN null
              ELSE rolpassword::text
          END
      FROM pg_authid
      WHERE rolname=$1 and rolcanlogin;
  END;
  $function$


-- Function: public.get_app_setting
CREATE OR REPLACE FUNCTION public.get_app_setting(setting_key text, default_value text DEFAULT NULL::text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    result text;
BEGIN
    SELECT value INTO result FROM public.qa_app_settings WHERE key = setting_key;
    RETURN COALESCE(result, default_value);
END;
$function$


-- Function: public.get_tree_ancestors
CREATE OR REPLACE FUNCTION public.get_tree_ancestors(p_node_id uuid)
 RETURNS TABLE(id uuid, name text, depth integer)
 LANGUAGE plpgsql
AS $function$
DECLARE
    node_path text;
    path_parts text[];
BEGIN
    -- Получаем path узла
    SELECT path INTO node_path FROM tree_nodes WHERE tree_nodes.id = p_node_id;

    IF node_path IS NULL THEN
        RETURN;
    END IF;

    -- Парсим path
    path_parts := string_to_array(node_path, '.');

    -- Возвращаем всех предков (кроме самого узла)
    RETURN QUERY
    SELECT t.id, t.name, t.depth
    FROM tree_nodes t
    WHERE t.id = ANY(path_parts[1:array_length(path_parts, 1) - 1]::uuid[])
    ORDER BY t.depth;
END;
$function$


-- Function: public.get_tree_descendants
CREATE OR REPLACE FUNCTION public.get_tree_descendants(p_node_id uuid)
 RETURNS TABLE(id uuid, name text, node_type text, depth integer, path text)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT t.id, t.name, t.node_type, t.depth, t.path
    FROM tree_nodes t
    WHERE t.path LIKE (
        SELECT tn.path || '.%' FROM tree_nodes tn WHERE tn.id = p_node_id
    )
    ORDER BY t.path;
END;
$function$


-- Function: public.move_tree_node
CREATE OR REPLACE FUNCTION public.move_tree_node(p_node_id uuid, p_new_parent_id uuid)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
DECLARE
    old_path text;
    new_parent_path text;
    new_path text;
    new_depth integer;
BEGIN
    -- Получаем текущий путь узла
    SELECT path INTO old_path FROM tree_nodes WHERE id = p_node_id;

    IF old_path IS NULL THEN
        RETURN false;
    END IF;

    -- Определяем новый путь
    IF p_new_parent_id IS NULL THEN
        new_path := p_node_id::text;
        new_depth := 0;
    ELSE
        SELECT path INTO new_parent_path FROM tree_nodes WHERE id = p_new_parent_id;

        IF new_parent_path IS NULL THEN
            RETURN false;
        END IF;

        -- Проверяем что не перемещаем в собственного потомка
        IF new_parent_path LIKE old_path || '.%' THEN
            RETURN false;
        END IF;

        new_path := new_parent_path || '.' || p_node_id::text;
        SELECT depth + 1 INTO new_depth FROM tree_nodes WHERE id = p_new_parent_id;
    END IF;

    -- Обновляем путь у узла и всех потомков
    UPDATE tree_nodes
    SET
        path = new_path || substring(path from length(old_path) + 1),
        depth = depth - (SELECT depth FROM tree_nodes WHERE id = p_node_id) + new_depth,
        parent_id = CASE WHEN id = p_node_id THEN p_new_parent_id ELSE parent_id END,
        updated_at = now()
    WHERE path = old_path OR path LIKE old_path || '.%';

    RETURN true;
END;
$function$


-- Function: public.recalculate_all_pdf_statuses
-- Description: Пересчитывает статусы всех PDF документов на основе данных node_files
CREATE OR REPLACE FUNCTION public.recalculate_all_pdf_statuses()
 RETURNS TABLE(node_id uuid, old_status text, new_status text, status_message text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    doc RECORD;
    v_status TEXT;
    v_message TEXT;
    v_has_ann_r2 BOOLEAN;
    v_has_ocr_r2 BOOLEAN;
    v_has_res_r2 BOOLEAN;
    v_has_ann_db BOOLEAN;
    v_has_ocr_db BOOLEAN;
    v_has_res_db BOOLEAN;
    v_r2_key TEXT;
    v_ann_key TEXT;
    v_ocr_key TEXT;
    v_res_key TEXT;
BEGIN
    -- Обходим все документы
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
            -- Формируем ключи для связанных файлов
            v_ann_key := regexp_replace(v_r2_key, '\.pdf$', '_annotation.json', 'i');
            v_ocr_key := regexp_replace(v_r2_key, '\.pdf$', '_ocr.html', 'i');
            v_res_key := regexp_replace(v_r2_key, '\.pdf$', '_result.json', 'i');
            
            -- Проверяем наличие в node_files
            SELECT 
                bool_or(file_type = 'annotation') AS has_ann,
                bool_or(file_type = 'ocr_html') AS has_ocr,
                bool_or(file_type = 'result_json') AS has_res
            INTO v_has_ann_db, v_has_ocr_db, v_has_res_db
            FROM node_files
            WHERE node_id = doc.id;
            
            -- Определяем статус на основе наличия файлов в БД
            IF v_has_ann_db AND v_has_ocr_db AND v_has_res_db THEN
                v_status := 'complete';
                v_message := 'Все файлы на месте';
            ELSIF NOT v_has_ann_db THEN
                v_status := 'missing_blocks';
                v_message := 'Отсутствует annotation.json';
            ELSE
                v_status := 'missing_files';
                v_message := '';
                IF NOT v_has_ocr_db THEN
                    v_message := v_message || 'ocr.html не в Supabase; ';
                END IF;
                IF NOT v_has_res_db THEN
                    v_message := v_message || 'result.json не в Supabase; ';
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
$function$


-- Function: public.set_app_setting
CREATE OR REPLACE FUNCTION public.set_app_setting(setting_key text, setting_value text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE public.qa_app_settings
    SET value = setting_value, updated_at = now()
    WHERE key = setting_key;
END;
$function$


-- Function: public.update_ancestors_descendants_count
CREATE OR REPLACE FUNCTION public.update_ancestors_descendants_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    ancestor_ids uuid[];
    delta integer;
    path_parts text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Парсим path для получения предков
        IF NEW.path IS NOT NULL AND NEW.path != NEW.id::text THEN
            path_parts := string_to_array(NEW.path, '.');
            -- Убираем последний элемент (сам узел)
            path_parts := path_parts[1:array_length(path_parts, 1) - 1];

            IF array_length(path_parts, 1) > 0 THEN
                ancestor_ids := path_parts::uuid[];
                UPDATE tree_nodes
                SET descendants_count = descendants_count + 1
                WHERE id = ANY(ancestor_ids);
            END IF;
        END IF;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        -- Уменьшаем счётчики у всех предков
        IF OLD.path IS NOT NULL AND OLD.path != OLD.id::text THEN
            path_parts := string_to_array(OLD.path, '.');
            path_parts := path_parts[1:array_length(path_parts, 1) - 1];

            -- Delta = 1 (сам узел) + его потомки
            delta := 1 + COALESCE(OLD.descendants_count, 0);

            IF array_length(path_parts, 1) > 0 THEN
                ancestor_ids := path_parts::uuid[];
                UPDATE tree_nodes
                SET descendants_count = descendants_count - delta
                WHERE id = ANY(ancestor_ids);
            END IF;
        END IF;
        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$function$


-- Function: public.update_image_categories_updated_at
CREATE OR REPLACE FUNCTION public.update_image_categories_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$


-- Function: public.update_node_files_count
CREATE OR REPLACE FUNCTION public.update_node_files_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE tree_nodes
        SET files_count = files_count + 1
        WHERE id = NEW.node_id;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        UPDATE tree_nodes
        SET files_count = files_count - 1
        WHERE id = OLD.node_id;
        RETURN OLD;

    ELSIF TG_OP = 'UPDATE' THEN
        -- Если изменился node_id
        IF OLD.node_id IS DISTINCT FROM NEW.node_id THEN
            UPDATE tree_nodes
            SET files_count = files_count - 1
            WHERE id = OLD.node_id;

            UPDATE tree_nodes
            SET files_count = files_count + 1
            WHERE id = NEW.node_id;
        END IF;
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$function$


-- Function: public.update_parent_children_count
CREATE OR REPLACE FUNCTION public.update_parent_children_count()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.parent_id IS NOT NULL THEN
            UPDATE tree_nodes
            SET children_count = children_count + 1
            WHERE id = NEW.parent_id;
        END IF;
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.parent_id IS NOT NULL THEN
            UPDATE tree_nodes
            SET children_count = children_count - 1
            WHERE id = OLD.parent_id;
        END IF;
        RETURN OLD;

    ELSIF TG_OP = 'UPDATE' THEN
        -- Если изменился parent_id
        IF OLD.parent_id IS DISTINCT FROM NEW.parent_id THEN
            IF OLD.parent_id IS NOT NULL THEN
                UPDATE tree_nodes
                SET children_count = children_count - 1
                WHERE id = OLD.parent_id;
            END IF;
            IF NEW.parent_id IS NOT NULL THEN
                UPDATE tree_nodes
                SET children_count = children_count + 1
                WHERE id = NEW.parent_id;
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$function$


-- Function: public.update_pdf_status
CREATE OR REPLACE FUNCTION public.update_pdf_status(p_node_id uuid, p_status text, p_message text DEFAULT NULL::text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE tree_nodes
    SET 
        pdf_status = p_status,
        pdf_status_message = p_message,
        pdf_status_updated_at = NOW(),
        updated_at = NOW()
    WHERE id = p_node_id AND node_type = 'document';
END;
$function$


-- Function: public.update_tree_node_path_and_depth
CREATE OR REPLACE FUNCTION public.update_tree_node_path_and_depth()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    parent_path text;
    parent_depth integer;
BEGIN
    IF NEW.parent_id IS NULL THEN
        -- Корневой узел
        NEW.path := NEW.id::text;
        NEW.depth := 0;
    ELSE
        -- Получаем path и depth родителя
        SELECT path, depth INTO parent_path, parent_depth
        FROM tree_nodes
        WHERE id = NEW.parent_id;

        IF parent_path IS NULL THEN
            -- Родитель не найден или ещё не обработан
            NEW.path := NEW.id::text;
            NEW.depth := 0;
        ELSE
            NEW.path := parent_path || '.' || NEW.id::text;
            NEW.depth := parent_depth + 1;
        END IF;
    END IF;

    RETURN NEW;
END;
$function$


-- Function: public.update_updated_at_column
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$


-- Function: realtime.apply_rls
CREATE OR REPLACE FUNCTION realtime.apply_rls(wal jsonb, max_record_bytes integer DEFAULT (1024 * 1024))
 RETURNS SETOF realtime.wal_rls
 LANGUAGE plpgsql
AS $function$
declare
-- Regclass of the table e.g. public.notes
entity_ regclass = (quote_ident(wal ->> 'schema') || '.' || quote_ident(wal ->> 'table'))::regclass;

-- I, U, D, T: insert, update ...
action realtime.action = (
    case wal ->> 'action'
        when 'I' then 'INSERT'
        when 'U' then 'UPDATE'
        when 'D' then 'DELETE'
        else 'ERROR'
    end
);

-- Is row level security enabled for the table
is_rls_enabled bool = relrowsecurity from pg_class where oid = entity_;

subscriptions realtime.subscription[] = array_agg(subs)
    from
        realtime.subscription subs
    where
        subs.entity = entity_
        -- Filter by action early - only get subscriptions interested in this action
        -- action_filter column can be: '*' (all), 'INSERT', 'UPDATE', or 'DELETE'
        and (subs.action_filter = '*' or subs.action_filter = action::text);

-- Subscription vars
roles regrole[] = array_agg(distinct us.claims_role::text)
    from
        unnest(subscriptions) us;

working_role regrole;
claimed_role regrole;
claims jsonb;

subscription_id uuid;
subscription_has_access bool;
visible_to_subscription_ids uuid[] = '{}';

-- structured info for wal's columns
columns realtime.wal_column[];
-- previous identity values for update/delete
old_columns realtime.wal_column[];

error_record_exceeds_max_size boolean = octet_length(wal::text) > max_record_bytes;

-- Primary jsonb output for record
output jsonb;

begin
perform set_config('role', null, true);

columns =
    array_agg(
        (
            x->>'name',
            x->>'type',
            x->>'typeoid',
            realtime.cast(
                (x->'value') #>> '{}',
                coalesce(
                    (x->>'typeoid')::regtype, -- null when wal2json version <= 2.4
                    (x->>'type')::regtype
                )
            ),
            (pks ->> 'name') is not null,
            true
        )::realtime.wal_column
    )
    from
        jsonb_array_elements(wal -> 'columns') x
        left join jsonb_array_elements(wal -> 'pk') pks
            on (x ->> 'name') = (pks ->> 'name');

old_columns =
    array_agg(
        (
            x->>'name',
            x->>'type',
            x->>'typeoid',
            realtime.cast(
                (x->'value') #>> '{}',
                coalesce(
                    (x->>'typeoid')::regtype, -- null when wal2json version <= 2.4
                    (x->>'type')::regtype
                )
            ),
            (pks ->> 'name') is not null,
            true
        )::realtime.wal_column
    )
    from
        jsonb_array_elements(wal -> 'identity') x
        left join jsonb_array_elements(wal -> 'pk') pks
            on (x ->> 'name') = (pks ->> 'name');

for working_role in select * from unnest(roles) loop

    -- Update `is_selectable` for columns and old_columns
    columns =
        array_agg(
            (
                c.name,
                c.type_name,
                c.type_oid,
                c.value,
                c.is_pkey,
                pg_catalog.has_column_privilege(working_role, entity_, c.name, 'SELECT')
            )::realtime.wal_column
        )
        from
            unnest(columns) c;

    old_columns =
            array_agg(
                (
                    c.name,
                    c.type_name,
                    c.type_oid,
                    c.value,
                    c.is_pkey,
                    pg_catalog.has_column_privilege(working_role, entity_, c.name, 'SELECT')
                )::realtime.wal_column
            )
            from
                unnest(old_columns) c;

    if action <> 'DELETE' and count(1) = 0 from unnest(columns) c where c.is_pkey then
        return next (
            jsonb_build_object(
                'schema', wal ->> 'schema',
                'table', wal ->> 'table',
                'type', action
            ),
            is_rls_enabled,
            -- subscriptions is already filtered by entity
            (select array_agg(s.subscription_id) from unnest(subscriptions) as s where claims_role = working_role),
            array['Error 400: Bad Request, no primary key']
        )::realtime.wal_rls;

    -- The claims role does not have SELECT permission to the primary key of entity
    elsif action <> 'DELETE' and sum(c.is_selectable::int) <> count(1) from unnest(columns) c where c.is_pkey then
        return next (
            jsonb_build_object(
                'schema', wal ->> 'schema',
                'table', wal ->> 'table',
                'type', action
            ),
            is_rls_enabled,
            (select array_agg(s.subscription_id) from unnest(subscriptions) as s where claims_role = working_role),
            array['Error 401: Unauthorized']
        )::realtime.wal_rls;

    else
        output = jsonb_build_object(
            'schema', wal ->> 'schema',
            'table', wal ->> 'table',
            'type', action,
            'commit_timestamp', to_char(
                ((wal ->> 'timestamp')::timestamptz at time zone 'utc'),
                'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
            ),
            'columns', (
                select
                    jsonb_agg(
                        jsonb_build_object(
                            'name', pa.attname,
                            'type', pt.typname
                        )
                        order by pa.attnum asc
                    )
                from
                    pg_attribute pa
                    join pg_type pt
                        on pa.atttypid = pt.oid
                where
                    attrelid = entity_
                    and attnum > 0
                    and pg_catalog.has_column_privilege(working_role, entity_, pa.attname, 'SELECT')
            )
        )
        -- Add "record" key for insert and update
        || case
            when action in ('INSERT', 'UPDATE') then
                jsonb_build_object(
                    'record',
                    (
                        select
                            jsonb_object_agg(
                                -- if unchanged toast, get column name and value from old record
                                coalesce((c).name, (oc).name),
                                case
                                    when (c).name is null then (oc).value
                                    else (c).value
                                end
                            )
                        from
                            unnest(columns) c
                            full outer join unnest(old_columns) oc
                                on (c).name = (oc).name
                        where
                            coalesce((c).is_selectable, (oc).is_selectable)
                            and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                    )
                )
            else '{}'::jsonb
        end
        -- Add "old_record" key for update and delete
        || case
            when action = 'UPDATE' then
                jsonb_build_object(
                        'old_record',
                        (
                            select jsonb_object_agg((c).name, (c).value)
                            from unnest(old_columns) c
                            where
                                (c).is_selectable
                                and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                        )
                    )
            when action = 'DELETE' then
                jsonb_build_object(
                    'old_record',
                    (
                        select jsonb_object_agg((c).name, (c).value)
                        from unnest(old_columns) c
                        where
                            (c).is_selectable
                            and ( not error_record_exceeds_max_size or (octet_length((c).value::text) <= 64))
                            and ( not is_rls_enabled or (c).is_pkey ) -- if RLS enabled, we can't secure deletes so filter to pkey
                    )
                )
            else '{}'::jsonb
        end;

        -- Create the prepared statement
        if is_rls_enabled and action <> 'DELETE' then
            if (select 1 from pg_prepared_statements where name = 'walrus_rls_stmt' limit 1) > 0 then
                deallocate walrus_rls_stmt;
            end if;
            execute realtime.build_prepared_statement_sql('walrus_rls_stmt', entity_, columns);
        end if;

        visible_to_subscription_ids = '{}';

        for subscription_id, claims in (
                select
                    subs.subscription_id,
                    subs.claims
                from
                    unnest(subscriptions) subs
                where
                    subs.entity = entity_
                    and subs.claims_role = working_role
                    and (
                        realtime.is_visible_through_filters(columns, subs.filters)
                        or (
                          action = 'DELETE'
                          and realtime.is_visible_through_filters(old_columns, subs.filters)
                        )
                    )
        ) loop

            if not is_rls_enabled or action = 'DELETE' then
                visible_to_subscription_ids = visible_to_subscription_ids || subscription_id;
            else
                -- Check if RLS allows the role to see the record
                perform
                    -- Trim leading and trailing quotes from working_role because set_config
                    -- doesn't recognize the role as valid if they are included
                    set_config('role', trim(both '"' from working_role::text), true),
                    set_config('request.jwt.claims', claims::text, true);

                execute 'execute walrus_rls_stmt' into subscription_has_access;

                if subscription_has_access then
                    visible_to_subscription_ids = visible_to_subscription_ids || subscription_id;
                end if;
            end if;
        end loop;

        perform set_config('role', null, true);

        return next (
            output,
            is_rls_enabled,
            visible_to_subscription_ids,
            case
                when error_record_exceeds_max_size then array['Error 413: Payload Too Large']
                else '{}'
            end
        )::realtime.wal_rls;

    end if;
end loop;

perform set_config('role', null, true);
end;
$function$


-- Function: realtime.broadcast_changes
CREATE OR REPLACE FUNCTION realtime.broadcast_changes(topic_name text, event_name text, operation text, table_name text, table_schema text, new record, old record, level text DEFAULT 'ROW'::text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
    -- Declare a variable to hold the JSONB representation of the row
    row_data jsonb := '{}'::jsonb;
BEGIN
    IF level = 'STATEMENT' THEN
        RAISE EXCEPTION 'function can only be triggered for each row, not for each statement';
    END IF;
    -- Check the operation type and handle accordingly
    IF operation = 'INSERT' OR operation = 'UPDATE' OR operation = 'DELETE' THEN
        row_data := jsonb_build_object('old_record', OLD, 'record', NEW, 'operation', operation, 'table', table_name, 'schema', table_schema);
        PERFORM realtime.send (row_data, event_name, topic_name);
    ELSE
        RAISE EXCEPTION 'Unexpected operation type: %', operation;
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE EXCEPTION 'Failed to process the row: %', SQLERRM;
END;

$function$


-- Function: realtime.build_prepared_statement_sql
CREATE OR REPLACE FUNCTION realtime.build_prepared_statement_sql(prepared_statement_name text, entity regclass, columns realtime.wal_column[])
 RETURNS text
 LANGUAGE sql
AS $function$
      /*
      Builds a sql string that, if executed, creates a prepared statement to
      tests retrive a row from *entity* by its primary key columns.
      Example
          select realtime.build_prepared_statement_sql('public.notes', '{"id"}'::text[], '{"bigint"}'::text[])
      */
          select
      'prepare ' || prepared_statement_name || ' as
          select
              exists(
                  select
                      1
                  from
                      ' || entity || '
                  where
                      ' || string_agg(quote_ident(pkc.name) || '=' || quote_nullable(pkc.value #>> '{}') , ' and ') || '
              )'
          from
              unnest(columns) pkc
          where
              pkc.is_pkey
          group by
              entity
      $function$


-- Function: realtime.cast
CREATE OR REPLACE FUNCTION realtime."cast"(val text, type_ regtype)
 RETURNS jsonb
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
    declare
      res jsonb;
    begin
      execute format('select to_jsonb(%L::'|| type_::text || ')', val)  into res;
      return res;
    end
    $function$


-- Function: realtime.check_equality_op
CREATE OR REPLACE FUNCTION realtime.check_equality_op(op realtime.equality_op, type_ regtype, val_1 text, val_2 text)
 RETURNS boolean
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
      /*
      Casts *val_1* and *val_2* as type *type_* and check the *op* condition for truthiness
      */
      declare
          op_symbol text = (
              case
                  when op = 'eq' then '='
                  when op = 'neq' then '!='
                  when op = 'lt' then '<'
                  when op = 'lte' then '<='
                  when op = 'gt' then '>'
                  when op = 'gte' then '>='
                  when op = 'in' then '= any'
                  else 'UNKNOWN OP'
              end
          );
          res boolean;
      begin
          execute format(
              'select %L::'|| type_::text || ' ' || op_symbol
              || ' ( %L::'
              || (
                  case
                      when op = 'in' then type_::text || '[]'
                      else type_::text end
              )
              || ')', val_1, val_2) into res;
          return res;
      end;
      $function$


-- Function: realtime.is_visible_through_filters
CREATE OR REPLACE FUNCTION realtime.is_visible_through_filters(columns realtime.wal_column[], filters realtime.user_defined_filter[])
 RETURNS boolean
 LANGUAGE sql
 IMMUTABLE
AS $function$
    /*
    Should the record be visible (true) or filtered out (false) after *filters* are applied
    */
        select
            -- Default to allowed when no filters present
            $2 is null -- no filters. this should not happen because subscriptions has a default
            or array_length($2, 1) is null -- array length of an empty array is null
            or bool_and(
                coalesce(
                    realtime.check_equality_op(
                        op:=f.op,
                        type_:=coalesce(
                            col.type_oid::regtype, -- null when wal2json version <= 2.4
                            col.type_name::regtype
                        ),
                        -- cast jsonb to text
                        val_1:=col.value #>> '{}',
                        val_2:=f.value
                    ),
                    false -- if null, filter does not match
                )
            )
        from
            unnest(filters) f
            join unnest(columns) col
                on f.column_name = col.name;
    $function$


-- Function: realtime.list_changes
CREATE OR REPLACE FUNCTION realtime.list_changes(publication name, slot_name name, max_changes integer, max_record_bytes integer)
 RETURNS SETOF realtime.wal_rls
 LANGUAGE sql
 SET log_min_messages TO 'fatal'
AS $function$
      with pub as (
        select
          concat_ws(
            ',',
            case when bool_or(pubinsert) then 'insert' else null end,
            case when bool_or(pubupdate) then 'update' else null end,
            case when bool_or(pubdelete) then 'delete' else null end
          ) as w2j_actions,
          coalesce(
            string_agg(
              realtime.quote_wal2json(format('%I.%I', schemaname, tablename)::regclass),
              ','
            ) filter (where ppt.tablename is not null and ppt.tablename not like '% %'),
            ''
          ) w2j_add_tables
        from
          pg_publication pp
          left join pg_publication_tables ppt
            on pp.pubname = ppt.pubname
        where
          pp.pubname = publication
        group by
          pp.pubname
        limit 1
      ),
      w2j as (
        select
          x.*, pub.w2j_add_tables
        from
          pub,
          pg_logical_slot_get_changes(
            slot_name, null, max_changes,
            'include-pk', 'true',
            'include-transaction', 'false',
            'include-timestamp', 'true',
            'include-type-oids', 'true',
            'format-version', '2',
            'actions', pub.w2j_actions,
            'add-tables', pub.w2j_add_tables
          ) x
      )
      select
        xyz.wal,
        xyz.is_rls_enabled,
        xyz.subscription_ids,
        xyz.errors
      from
        w2j,
        realtime.apply_rls(
          wal := w2j.data::jsonb,
          max_record_bytes := max_record_bytes
        ) xyz(wal, is_rls_enabled, subscription_ids, errors)
      where
        w2j.w2j_add_tables <> ''
        and xyz.subscription_ids[1] is not null
    $function$


-- Function: realtime.quote_wal2json
CREATE OR REPLACE FUNCTION realtime.quote_wal2json(entity regclass)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE STRICT
AS $function$
      select
        (
          select string_agg('' || ch,'')
          from unnest(string_to_array(nsp.nspname::text, null)) with ordinality x(ch, idx)
          where
            not (x.idx = 1 and x.ch = '"')
            and not (
              x.idx = array_length(string_to_array(nsp.nspname::text, null), 1)
              and x.ch = '"'
            )
        )
        || '.'
        || (
          select string_agg('' || ch,'')
          from unnest(string_to_array(pc.relname::text, null)) with ordinality x(ch, idx)
          where
            not (x.idx = 1 and x.ch = '"')
            and not (
              x.idx = array_length(string_to_array(nsp.nspname::text, null), 1)
              and x.ch = '"'
            )
          )
      from
        pg_class pc
        join pg_namespace nsp
          on pc.relnamespace = nsp.oid
      where
        pc.oid = entity
    $function$


-- Function: realtime.send
CREATE OR REPLACE FUNCTION realtime.send(payload jsonb, event text, topic text, private boolean DEFAULT true)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE
  generated_id uuid;
  final_payload jsonb;
BEGIN
  BEGIN
    -- Generate a new UUID for the id
    generated_id := gen_random_uuid();

    -- Check if payload has an 'id' key, if not, add the generated UUID
    IF payload ? 'id' THEN
      final_payload := payload;
    ELSE
      final_payload := jsonb_set(payload, '{id}', to_jsonb(generated_id));
    END IF;

    -- Set the topic configuration
    EXECUTE format('SET LOCAL realtime.topic TO %L', topic);

    -- Attempt to insert the message
    INSERT INTO realtime.messages (id, payload, event, topic, private, extension)
    VALUES (generated_id, final_payload, event, topic, private, 'broadcast');
  EXCEPTION
    WHEN OTHERS THEN
      -- Capture and notify the error
      RAISE WARNING 'ErrorSendingBroadcastMessage: %', SQLERRM;
  END;
END;
$function$


-- Function: realtime.subscription_check_filters
CREATE OR REPLACE FUNCTION realtime.subscription_check_filters()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
    /*
    Validates that the user defined filters for a subscription:
    - refer to valid columns that the claimed role may access
    - values are coercable to the correct column type
    */
    declare
        col_names text[] = coalesce(
                array_agg(c.column_name order by c.ordinal_position),
                '{}'::text[]
            )
            from
                information_schema.columns c
            where
                format('%I.%I', c.table_schema, c.table_name)::regclass = new.entity
                and pg_catalog.has_column_privilege(
                    (new.claims ->> 'role'),
                    format('%I.%I', c.table_schema, c.table_name)::regclass,
                    c.column_name,
                    'SELECT'
                );
        filter realtime.user_defined_filter;
        col_type regtype;

        in_val jsonb;
    begin
        for filter in select * from unnest(new.filters) loop
            -- Filtered column is valid
            if not filter.column_name = any(col_names) then
                raise exception 'invalid column for filter %', filter.column_name;
            end if;

            -- Type is sanitized and safe for string interpolation
            col_type = (
                select atttypid::regtype
                from pg_catalog.pg_attribute
                where attrelid = new.entity
                      and attname = filter.column_name
            );
            if col_type is null then
                raise exception 'failed to lookup type for column %', filter.column_name;
            end if;

            -- Set maximum number of entries for in filter
            if filter.op = 'in'::realtime.equality_op then
                in_val = realtime.cast(filter.value, (col_type::text || '[]')::regtype);
                if coalesce(jsonb_array_length(in_val), 0) > 100 then
                    raise exception 'too many values for `in` filter. Maximum 100';
                end if;
            else
                -- raises an exception if value is not coercable to type
                perform realtime.cast(filter.value, col_type);
            end if;

        end loop;

        -- Apply consistent order to filters so the unique constraint on
        -- (subscription_id, entity, filters) can't be tricked by a different filter order
        new.filters = coalesce(
            array_agg(f order by f.column_name, f.op, f.value),
            '{}'
        ) from unnest(new.filters) f;

        return new;
    end;
    $function$


-- Function: realtime.to_regrole
CREATE OR REPLACE FUNCTION realtime.to_regrole(role_name text)
 RETURNS regrole
 LANGUAGE sql
 IMMUTABLE
AS $function$ select role_name::regrole $function$


-- Function: realtime.topic
CREATE OR REPLACE FUNCTION realtime.topic()
 RETURNS text
 LANGUAGE sql
 STABLE
AS $function$
select nullif(current_setting('realtime.topic', true), '')::text;
$function$


-- Function: storage.can_insert_object
CREATE OR REPLACE FUNCTION storage.can_insert_object(bucketid text, name text, owner uuid, metadata jsonb)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
BEGIN
  INSERT INTO "storage"."objects" ("bucket_id", "name", "owner", "metadata") VALUES (bucketid, name, owner, metadata);
  -- hack to rollback the successful insert
  RAISE sqlstate 'PT200' using
  message = 'ROLLBACK',
  detail = 'rollback successful insert';
END
$function$


-- Function: storage.delete_leaf_prefixes
CREATE OR REPLACE FUNCTION storage.delete_leaf_prefixes(bucket_ids text[], names text[])
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    v_rows_deleted integer;
BEGIN
    LOOP
        WITH candidates AS (
            SELECT DISTINCT
                t.bucket_id,
                unnest(storage.get_prefixes(t.name)) AS name
            FROM unnest(bucket_ids, names) AS t(bucket_id, name)
        ),
        uniq AS (
             SELECT
                 bucket_id,
                 name,
                 storage.get_level(name) AS level
             FROM candidates
             WHERE name <> ''
             GROUP BY bucket_id, name
        ),
        leaf AS (
             SELECT
                 p.bucket_id,
                 p.name,
                 p.level
             FROM storage.prefixes AS p
                  JOIN uniq AS u
                       ON u.bucket_id = p.bucket_id
                           AND u.name = p.name
                           AND u.level = p.level
             WHERE NOT EXISTS (
                 SELECT 1
                 FROM storage.objects AS o
                 WHERE o.bucket_id = p.bucket_id
                   AND o.level = p.level + 1
                   AND o.name COLLATE "C" LIKE p.name || '/%'
             )
             AND NOT EXISTS (
                 SELECT 1
                 FROM storage.prefixes AS c
                 WHERE c.bucket_id = p.bucket_id
                   AND c.level = p.level + 1
                   AND c.name COLLATE "C" LIKE p.name || '/%'
             )
        )
        DELETE
        FROM storage.prefixes AS p
            USING leaf AS l
        WHERE p.bucket_id = l.bucket_id
          AND p.name = l.name
          AND p.level = l.level;

        GET DIAGNOSTICS v_rows_deleted = ROW_COUNT;
        EXIT WHEN v_rows_deleted = 0;
    END LOOP;
END;
$function$


-- Function: storage.enforce_bucket_name_length
CREATE OR REPLACE FUNCTION storage.enforce_bucket_name_length()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
    if length(new.name) > 100 then
        raise exception 'bucket name "%" is too long (% characters). Max is 100.', new.name, length(new.name);
    end if;
    return new;
end;
$function$


-- Function: storage.extension
CREATE OR REPLACE FUNCTION storage.extension(name text)
 RETURNS text
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
DECLARE
    _parts text[];
    _filename text;
BEGIN
    SELECT string_to_array(name, '/') INTO _parts;
    SELECT _parts[array_length(_parts,1)] INTO _filename;
    RETURN reverse(split_part(reverse(_filename), '.', 1));
END
$function$


-- Function: storage.filename
CREATE OR REPLACE FUNCTION storage.filename(name text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
_parts text[];
BEGIN
	select string_to_array(name, '/') into _parts;
	return _parts[array_length(_parts,1)];
END
$function$


-- Function: storage.foldername
CREATE OR REPLACE FUNCTION storage.foldername(name text)
 RETURNS text[]
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
DECLARE
    _parts text[];
BEGIN
    -- Split on "/" to get path segments
    SELECT string_to_array(name, '/') INTO _parts;
    -- Return everything except the last segment
    RETURN _parts[1 : array_length(_parts,1) - 1];
END
$function$


-- Function: storage.get_common_prefix
CREATE OR REPLACE FUNCTION storage.get_common_prefix(p_key text, p_prefix text, p_delimiter text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
SELECT CASE
    WHEN position(p_delimiter IN substring(p_key FROM length(p_prefix) + 1)) > 0
    THEN left(p_key, length(p_prefix) + position(p_delimiter IN substring(p_key FROM length(p_prefix) + 1)))
    ELSE NULL
END;
$function$


-- Function: storage.get_level
CREATE OR REPLACE FUNCTION storage.get_level(name text)
 RETURNS integer
 LANGUAGE sql
 IMMUTABLE STRICT
AS $function$
SELECT array_length(string_to_array("name", '/'), 1);
$function$


-- Function: storage.get_prefix
CREATE OR REPLACE FUNCTION storage.get_prefix(name text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE STRICT
AS $function$
SELECT
    CASE WHEN strpos("name", '/') > 0 THEN
             regexp_replace("name", '[\/]{1}[^\/]+\/?$', '')
         ELSE
             ''
        END;
$function$


-- Function: storage.get_prefixes
CREATE OR REPLACE FUNCTION storage.get_prefixes(name text)
 RETURNS text[]
 LANGUAGE plpgsql
 IMMUTABLE STRICT
AS $function$
DECLARE
    parts text[];
    prefixes text[];
    prefix text;
BEGIN
    -- Split the name into parts by '/'
    parts := string_to_array("name", '/');
    prefixes := '{}';

    -- Construct the prefixes, stopping one level below the last part
    FOR i IN 1..array_length(parts, 1) - 1 LOOP
            prefix := array_to_string(parts[1:i], '/');
            prefixes := array_append(prefixes, prefix);
    END LOOP;

    RETURN prefixes;
END;
$function$


-- Function: storage.get_size_by_bucket
CREATE OR REPLACE FUNCTION storage.get_size_by_bucket()
 RETURNS TABLE(size bigint, bucket_id text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    return query
        select sum((metadata->>'size')::bigint) as size, obj.bucket_id
        from "storage".objects as obj
        group by obj.bucket_id;
END
$function$


-- Function: storage.list_multipart_uploads_with_delimiter
CREATE OR REPLACE FUNCTION storage.list_multipart_uploads_with_delimiter(bucket_id text, prefix_param text, delimiter_param text, max_keys integer DEFAULT 100, next_key_token text DEFAULT ''::text, next_upload_token text DEFAULT ''::text)
 RETURNS TABLE(key text, id text, created_at timestamp with time zone)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY EXECUTE
        'SELECT DISTINCT ON(key COLLATE "C") * from (
            SELECT
                CASE
                    WHEN position($2 IN substring(key from length($1) + 1)) > 0 THEN
                        substring(key from 1 for length($1) + position($2 IN substring(key from length($1) + 1)))
                    ELSE
                        key
                END AS key, id, created_at
            FROM
                storage.s3_multipart_uploads
            WHERE
                bucket_id = $5 AND
                key ILIKE $1 || ''%'' AND
                CASE
                    WHEN $4 != '''' AND $6 = '''' THEN
                        CASE
                            WHEN position($2 IN substring(key from length($1) + 1)) > 0 THEN
                                substring(key from 1 for length($1) + position($2 IN substring(key from length($1) + 1))) COLLATE "C" > $4
                            ELSE
                                key COLLATE "C" > $4
                            END
                    ELSE
                        true
                END AND
                CASE
                    WHEN $6 != '''' THEN
                        id COLLATE "C" > $6
                    ELSE
                        true
                    END
            ORDER BY
                key COLLATE "C" ASC, created_at ASC) as e order by key COLLATE "C" LIMIT $3'
        USING prefix_param, delimiter_param, max_keys, next_key_token, bucket_id, next_upload_token;
END;
$function$


-- Function: storage.list_objects_with_delimiter
CREATE OR REPLACE FUNCTION storage.list_objects_with_delimiter(_bucket_id text, prefix_param text, delimiter_param text, max_keys integer DEFAULT 100, start_after text DEFAULT ''::text, next_token text DEFAULT ''::text, sort_order text DEFAULT 'asc'::text)
 RETURNS TABLE(name text, id uuid, metadata jsonb, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone)
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_peek_name TEXT;
    v_current RECORD;
    v_common_prefix TEXT;

    -- Configuration
    v_is_asc BOOLEAN;
    v_prefix TEXT;
    v_start TEXT;
    v_upper_bound TEXT;
    v_file_batch_size INT;

    -- Seek state
    v_next_seek TEXT;
    v_count INT := 0;

    -- Dynamic SQL for batch query only
    v_batch_query TEXT;

BEGIN
    -- ========================================================================
    -- INITIALIZATION
    -- ========================================================================
    v_is_asc := lower(coalesce(sort_order, 'asc')) = 'asc';
    v_prefix := coalesce(prefix_param, '');
    v_start := CASE WHEN coalesce(next_token, '') <> '' THEN next_token ELSE coalesce(start_after, '') END;
    v_file_batch_size := LEAST(GREATEST(max_keys * 2, 100), 1000);

    -- Calculate upper bound for prefix filtering (bytewise, using COLLATE "C")
    IF v_prefix = '' THEN
        v_upper_bound := NULL;
    ELSIF right(v_prefix, 1) = delimiter_param THEN
        v_upper_bound := left(v_prefix, -1) || chr(ascii(delimiter_param) + 1);
    ELSE
        v_upper_bound := left(v_prefix, -1) || chr(ascii(right(v_prefix, 1)) + 1);
    END IF;

    -- Build batch query (dynamic SQL - called infrequently, amortized over many rows)
    IF v_is_asc THEN
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" >= $2 ' ||
                'AND o.name COLLATE "C" < $3 ORDER BY o.name COLLATE "C" ASC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" >= $2 ' ||
                'ORDER BY o.name COLLATE "C" ASC LIMIT $4';
        END IF;
    ELSE
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" < $2 ' ||
                'AND o.name COLLATE "C" >= $3 ORDER BY o.name COLLATE "C" DESC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND o.name COLLATE "C" < $2 ' ||
                'ORDER BY o.name COLLATE "C" DESC LIMIT $4';
        END IF;
    END IF;

    -- ========================================================================
    -- SEEK INITIALIZATION: Determine starting position
    -- ========================================================================
    IF v_start = '' THEN
        IF v_is_asc THEN
            v_next_seek := v_prefix;
        ELSE
            -- DESC without cursor: find the last item in range
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_prefix AND o.name COLLATE "C" < v_upper_bound
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix <> '' THEN
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_next_seek FROM storage.objects o
                WHERE o.bucket_id = _bucket_id
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            END IF;

            IF v_next_seek IS NOT NULL THEN
                v_next_seek := v_next_seek || delimiter_param;
            ELSE
                RETURN;
            END IF;
        END IF;
    ELSE
        -- Cursor provided: determine if it refers to a folder or leaf
        IF EXISTS (
            SELECT 1 FROM storage.objects o
            WHERE o.bucket_id = _bucket_id
              AND o.name COLLATE "C" LIKE v_start || delimiter_param || '%'
            LIMIT 1
        ) THEN
            -- Cursor refers to a folder
            IF v_is_asc THEN
                v_next_seek := v_start || chr(ascii(delimiter_param) + 1);
            ELSE
                v_next_seek := v_start || delimiter_param;
            END IF;
        ELSE
            -- Cursor refers to a leaf object
            IF v_is_asc THEN
                v_next_seek := v_start || delimiter_param;
            ELSE
                v_next_seek := v_start;
            END IF;
        END IF;
    END IF;

    -- ========================================================================
    -- MAIN LOOP: Hybrid peek-then-batch algorithm
    -- Uses STATIC SQL for peek (hot path) and DYNAMIC SQL for batch
    -- ========================================================================
    LOOP
        EXIT WHEN v_count >= max_keys;

        -- STEP 1: PEEK using STATIC SQL (plan cached, very fast)
        IF v_is_asc THEN
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_next_seek AND o.name COLLATE "C" < v_upper_bound
                ORDER BY o.name COLLATE "C" ASC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" >= v_next_seek
                ORDER BY o.name COLLATE "C" ASC LIMIT 1;
            END IF;
        ELSE
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix <> '' THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek AND o.name COLLATE "C" >= v_prefix
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = _bucket_id AND o.name COLLATE "C" < v_next_seek
                ORDER BY o.name COLLATE "C" DESC LIMIT 1;
            END IF;
        END IF;

        EXIT WHEN v_peek_name IS NULL;

        -- STEP 2: Check if this is a FOLDER or FILE
        v_common_prefix := storage.get_common_prefix(v_peek_name, v_prefix, delimiter_param);

        IF v_common_prefix IS NOT NULL THEN
            -- FOLDER: Emit and skip to next folder (no heap access needed)
            name := rtrim(v_common_prefix, delimiter_param);
            id := NULL;
            updated_at := NULL;
            created_at := NULL;
            last_accessed_at := NULL;
            metadata := NULL;
            RETURN NEXT;
            v_count := v_count + 1;

            -- Advance seek past the folder range
            IF v_is_asc THEN
                v_next_seek := left(v_common_prefix, -1) || chr(ascii(delimiter_param) + 1);
            ELSE
                v_next_seek := v_common_prefix;
            END IF;
        ELSE
            -- FILE: Batch fetch using DYNAMIC SQL (overhead amortized over many rows)
            -- For ASC: upper_bound is the exclusive upper limit (< condition)
            -- For DESC: prefix is the inclusive lower limit (>= condition)
            FOR v_current IN EXECUTE v_batch_query USING _bucket_id, v_next_seek,
                CASE WHEN v_is_asc THEN COALESCE(v_upper_bound, v_prefix) ELSE v_prefix END, v_file_batch_size
            LOOP
                v_common_prefix := storage.get_common_prefix(v_current.name, v_prefix, delimiter_param);

                IF v_common_prefix IS NOT NULL THEN
                    -- Hit a folder: exit batch, let peek handle it
                    v_next_seek := v_current.name;
                    EXIT;
                END IF;

                -- Emit file
                name := v_current.name;
                id := v_current.id;
                updated_at := v_current.updated_at;
                created_at := v_current.created_at;
                last_accessed_at := v_current.last_accessed_at;
                metadata := v_current.metadata;
                RETURN NEXT;
                v_count := v_count + 1;

                -- Advance seek past this file
                IF v_is_asc THEN
                    v_next_seek := v_current.name || delimiter_param;
                ELSE
                    v_next_seek := v_current.name;
                END IF;

                EXIT WHEN v_count >= max_keys;
            END LOOP;
        END IF;
    END LOOP;
END;
$function$


-- Function: storage.operation
CREATE OR REPLACE FUNCTION storage.operation()
 RETURNS text
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN current_setting('storage.operation', true);
END;
$function$


-- Function: storage.protect_delete
CREATE OR REPLACE FUNCTION storage.protect_delete()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Check if storage.allow_delete_query is set to 'true'
    IF COALESCE(current_setting('storage.allow_delete_query', true), 'false') != 'true' THEN
        RAISE EXCEPTION 'Direct deletion from storage tables is not allowed. Use the Storage API instead.'
            USING HINT = 'This prevents accidental data loss from orphaned objects.',
                  ERRCODE = '42501';
    END IF;
    RETURN NULL;
END;
$function$


-- Function: storage.search
CREATE OR REPLACE FUNCTION storage.search(prefix text, bucketname text, limits integer DEFAULT 100, levels integer DEFAULT 1, offsets integer DEFAULT 0, search text DEFAULT ''::text, sortcolumn text DEFAULT 'name'::text, sortorder text DEFAULT 'asc'::text)
 RETURNS TABLE(name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_peek_name TEXT;
    v_current RECORD;
    v_common_prefix TEXT;
    v_delimiter CONSTANT TEXT := '/';

    -- Configuration
    v_limit INT;
    v_prefix TEXT;
    v_prefix_lower TEXT;
    v_is_asc BOOLEAN;
    v_order_by TEXT;
    v_sort_order TEXT;
    v_upper_bound TEXT;
    v_file_batch_size INT;

    -- Dynamic SQL for batch query only
    v_batch_query TEXT;

    -- Seek state
    v_next_seek TEXT;
    v_count INT := 0;
    v_skipped INT := 0;
BEGIN
    -- ========================================================================
    -- INITIALIZATION
    -- ========================================================================
    v_limit := LEAST(coalesce(limits, 100), 1500);
    v_prefix := coalesce(prefix, '') || coalesce(search, '');
    v_prefix_lower := lower(v_prefix);
    v_is_asc := lower(coalesce(sortorder, 'asc')) = 'asc';
    v_file_batch_size := LEAST(GREATEST(v_limit * 2, 100), 1000);

    -- Validate sort column
    CASE lower(coalesce(sortcolumn, 'name'))
        WHEN 'name' THEN v_order_by := 'name';
        WHEN 'updated_at' THEN v_order_by := 'updated_at';
        WHEN 'created_at' THEN v_order_by := 'created_at';
        WHEN 'last_accessed_at' THEN v_order_by := 'last_accessed_at';
        ELSE v_order_by := 'name';
    END CASE;

    v_sort_order := CASE WHEN v_is_asc THEN 'asc' ELSE 'desc' END;

    -- ========================================================================
    -- NON-NAME SORTING: Use path_tokens approach (unchanged)
    -- ========================================================================
    IF v_order_by != 'name' THEN
        RETURN QUERY EXECUTE format(
            $sql$
            WITH folders AS (
                SELECT path_tokens[$1] AS folder
                FROM storage.objects
                WHERE objects.name ILIKE $2 || '%%'
                  AND bucket_id = $3
                  AND array_length(objects.path_tokens, 1) <> $1
                GROUP BY folder
                ORDER BY folder %s
            )
            (SELECT folder AS "name",
                   NULL::uuid AS id,
                   NULL::timestamptz AS updated_at,
                   NULL::timestamptz AS created_at,
                   NULL::timestamptz AS last_accessed_at,
                   NULL::jsonb AS metadata FROM folders)
            UNION ALL
            (SELECT path_tokens[$1] AS "name",
                   id, updated_at, created_at, last_accessed_at, metadata
             FROM storage.objects
             WHERE objects.name ILIKE $2 || '%%'
               AND bucket_id = $3
               AND array_length(objects.path_tokens, 1) = $1
             ORDER BY %I %s)
            LIMIT $4 OFFSET $5
            $sql$, v_sort_order, v_order_by, v_sort_order
        ) USING levels, v_prefix, bucketname, v_limit, offsets;
        RETURN;
    END IF;

    -- ========================================================================
    -- NAME SORTING: Hybrid skip-scan with batch optimization
    -- ========================================================================

    -- Calculate upper bound for prefix filtering
    IF v_prefix_lower = '' THEN
        v_upper_bound := NULL;
    ELSIF right(v_prefix_lower, 1) = v_delimiter THEN
        v_upper_bound := left(v_prefix_lower, -1) || chr(ascii(v_delimiter) + 1);
    ELSE
        v_upper_bound := left(v_prefix_lower, -1) || chr(ascii(right(v_prefix_lower, 1)) + 1);
    END IF;

    -- Build batch query (dynamic SQL - called infrequently, amortized over many rows)
    IF v_is_asc THEN
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" >= $2 ' ||
                'AND lower(o.name) COLLATE "C" < $3 ORDER BY lower(o.name) COLLATE "C" ASC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" >= $2 ' ||
                'ORDER BY lower(o.name) COLLATE "C" ASC LIMIT $4';
        END IF;
    ELSE
        IF v_upper_bound IS NOT NULL THEN
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" < $2 ' ||
                'AND lower(o.name) COLLATE "C" >= $3 ORDER BY lower(o.name) COLLATE "C" DESC LIMIT $4';
        ELSE
            v_batch_query := 'SELECT o.name, o.id, o.updated_at, o.created_at, o.last_accessed_at, o.metadata ' ||
                'FROM storage.objects o WHERE o.bucket_id = $1 AND lower(o.name) COLLATE "C" < $2 ' ||
                'ORDER BY lower(o.name) COLLATE "C" DESC LIMIT $4';
        END IF;
    END IF;

    -- Initialize seek position
    IF v_is_asc THEN
        v_next_seek := v_prefix_lower;
    ELSE
        -- DESC: find the last item in range first (static SQL)
        IF v_upper_bound IS NOT NULL THEN
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_prefix_lower AND lower(o.name) COLLATE "C" < v_upper_bound
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        ELSIF v_prefix_lower <> '' THEN
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_prefix_lower
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        ELSE
            SELECT o.name INTO v_peek_name FROM storage.objects o
            WHERE o.bucket_id = bucketname
            ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
        END IF;

        IF v_peek_name IS NOT NULL THEN
            v_next_seek := lower(v_peek_name) || v_delimiter;
        ELSE
            RETURN;
        END IF;
    END IF;

    -- ========================================================================
    -- MAIN LOOP: Hybrid peek-then-batch algorithm
    -- Uses STATIC SQL for peek (hot path) and DYNAMIC SQL for batch
    -- ========================================================================
    LOOP
        EXIT WHEN v_count >= v_limit;

        -- STEP 1: PEEK using STATIC SQL (plan cached, very fast)
        IF v_is_asc THEN
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_next_seek AND lower(o.name) COLLATE "C" < v_upper_bound
                ORDER BY lower(o.name) COLLATE "C" ASC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" >= v_next_seek
                ORDER BY lower(o.name) COLLATE "C" ASC LIMIT 1;
            END IF;
        ELSE
            IF v_upper_bound IS NOT NULL THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek AND lower(o.name) COLLATE "C" >= v_prefix_lower
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            ELSIF v_prefix_lower <> '' THEN
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek AND lower(o.name) COLLATE "C" >= v_prefix_lower
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            ELSE
                SELECT o.name INTO v_peek_name FROM storage.objects o
                WHERE o.bucket_id = bucketname AND lower(o.name) COLLATE "C" < v_next_seek
                ORDER BY lower(o.name) COLLATE "C" DESC LIMIT 1;
            END IF;
        END IF;

        EXIT WHEN v_peek_name IS NULL;

        -- STEP 2: Check if this is a FOLDER or FILE
        v_common_prefix := storage.get_common_prefix(lower(v_peek_name), v_prefix_lower, v_delimiter);

        IF v_common_prefix IS NOT NULL THEN
            -- FOLDER: Handle offset, emit if needed, skip to next folder
            IF v_skipped < offsets THEN
                v_skipped := v_skipped + 1;
            ELSE
                name := split_part(rtrim(storage.get_common_prefix(v_peek_name, v_prefix, v_delimiter), v_delimiter), v_delimiter, levels);
                id := NULL;
                updated_at := NULL;
                created_at := NULL;
                last_accessed_at := NULL;
                metadata := NULL;
                RETURN NEXT;
                v_count := v_count + 1;
            END IF;

            -- Advance seek past the folder range
            IF v_is_asc THEN
                v_next_seek := lower(left(v_common_prefix, -1)) || chr(ascii(v_delimiter) + 1);
            ELSE
                v_next_seek := lower(v_common_prefix);
            END IF;
        ELSE
            -- FILE: Batch fetch using DYNAMIC SQL (overhead amortized over many rows)
            -- For ASC: upper_bound is the exclusive upper limit (< condition)
            -- For DESC: prefix_lower is the inclusive lower limit (>= condition)
            FOR v_current IN EXECUTE v_batch_query
                USING bucketname, v_next_seek,
                    CASE WHEN v_is_asc THEN COALESCE(v_upper_bound, v_prefix_lower) ELSE v_prefix_lower END, v_file_batch_size
            LOOP
                v_common_prefix := storage.get_common_prefix(lower(v_current.name), v_prefix_lower, v_delimiter);

                IF v_common_prefix IS NOT NULL THEN
                    -- Hit a folder: exit batch, let peek handle it
                    v_next_seek := lower(v_current.name);
                    EXIT;
                END IF;

                -- Handle offset skipping
                IF v_skipped < offsets THEN
                    v_skipped := v_skipped + 1;
                ELSE
                    -- Emit file
                    name := split_part(v_current.name, v_delimiter, levels);
                    id := v_current.id;
                    updated_at := v_current.updated_at;
                    created_at := v_current.created_at;
                    last_accessed_at := v_current.last_accessed_at;
                    metadata := v_current.metadata;
                    RETURN NEXT;
                    v_count := v_count + 1;
                END IF;

                -- Advance seek past this file
                IF v_is_asc THEN
                    v_next_seek := lower(v_current.name) || v_delimiter;
                ELSE
                    v_next_seek := lower(v_current.name);
                END IF;

                EXIT WHEN v_count >= v_limit;
            END LOOP;
        END IF;
    END LOOP;
END;
$function$


-- Function: storage.search_by_timestamp
CREATE OR REPLACE FUNCTION storage.search_by_timestamp(p_prefix text, p_bucket_id text, p_limit integer, p_level integer, p_start_after text, p_sort_order text, p_sort_column text, p_sort_column_after text)
 RETURNS TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_cursor_op text;
    v_query text;
    v_prefix text;
BEGIN
    v_prefix := coalesce(p_prefix, '');

    IF p_sort_order = 'asc' THEN
        v_cursor_op := '>';
    ELSE
        v_cursor_op := '<';
    END IF;

    v_query := format($sql$
        WITH raw_objects AS (
            SELECT
                o.name AS obj_name,
                o.id AS obj_id,
                o.updated_at AS obj_updated_at,
                o.created_at AS obj_created_at,
                o.last_accessed_at AS obj_last_accessed_at,
                o.metadata AS obj_metadata,
                storage.get_common_prefix(o.name, $1, '/') AS common_prefix
            FROM storage.objects o
            WHERE o.bucket_id = $2
              AND o.name COLLATE "C" LIKE $1 || '%%'
        ),
        -- Aggregate common prefixes (folders)
        -- Both created_at and updated_at use MIN(obj_created_at) to match the old prefixes table behavior
        aggregated_prefixes AS (
            SELECT
                rtrim(common_prefix, '/') AS name,
                NULL::uuid AS id,
                MIN(obj_created_at) AS updated_at,
                MIN(obj_created_at) AS created_at,
                NULL::timestamptz AS last_accessed_at,
                NULL::jsonb AS metadata,
                TRUE AS is_prefix
            FROM raw_objects
            WHERE common_prefix IS NOT NULL
            GROUP BY common_prefix
        ),
        leaf_objects AS (
            SELECT
                obj_name AS name,
                obj_id AS id,
                obj_updated_at AS updated_at,
                obj_created_at AS created_at,
                obj_last_accessed_at AS last_accessed_at,
                obj_metadata AS metadata,
                FALSE AS is_prefix
            FROM raw_objects
            WHERE common_prefix IS NULL
        ),
        combined AS (
            SELECT * FROM aggregated_prefixes
            UNION ALL
            SELECT * FROM leaf_objects
        ),
        filtered AS (
            SELECT *
            FROM combined
            WHERE (
                $5 = ''
                OR ROW(
                    date_trunc('milliseconds', %I),
                    name COLLATE "C"
                ) %s ROW(
                    COALESCE(NULLIF($6, '')::timestamptz, 'epoch'::timestamptz),
                    $5
                )
            )
        )
        SELECT
            split_part(name, '/', $3) AS key,
            name,
            id,
            updated_at,
            created_at,
            last_accessed_at,
            metadata
        FROM filtered
        ORDER BY
            COALESCE(date_trunc('milliseconds', %I), 'epoch'::timestamptz) %s,
            name COLLATE "C" %s
        LIMIT $4
    $sql$,
        p_sort_column,
        v_cursor_op,
        p_sort_column,
        p_sort_order,
        p_sort_order
    );

    RETURN QUERY EXECUTE v_query
    USING v_prefix, p_bucket_id, p_level, p_limit, p_start_after, p_sort_column_after;
END;
$function$


-- Function: storage.search_legacy_v1
CREATE OR REPLACE FUNCTION storage.search_legacy_v1(prefix text, bucketname text, limits integer DEFAULT 100, levels integer DEFAULT 1, offsets integer DEFAULT 0, search text DEFAULT ''::text, sortcolumn text DEFAULT 'name'::text, sortorder text DEFAULT 'asc'::text)
 RETURNS TABLE(name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
 LANGUAGE plpgsql
 STABLE
AS $function$
declare
    v_order_by text;
    v_sort_order text;
begin
    case
        when sortcolumn = 'name' then
            v_order_by = 'name';
        when sortcolumn = 'updated_at' then
            v_order_by = 'updated_at';
        when sortcolumn = 'created_at' then
            v_order_by = 'created_at';
        when sortcolumn = 'last_accessed_at' then
            v_order_by = 'last_accessed_at';
        else
            v_order_by = 'name';
        end case;

    case
        when sortorder = 'asc' then
            v_sort_order = 'asc';
        when sortorder = 'desc' then
            v_sort_order = 'desc';
        else
            v_sort_order = 'asc';
        end case;

    v_order_by = v_order_by || ' ' || v_sort_order;

    return query execute
        'with folders as (
           select path_tokens[$1] as folder
           from storage.objects
             where objects.name ilike $2 || $3 || ''%''
               and bucket_id = $4
               and array_length(objects.path_tokens, 1) <> $1
           group by folder
           order by folder ' || v_sort_order || '
     )
     (select folder as "name",
            null as id,
            null as updated_at,
            null as created_at,
            null as last_accessed_at,
            null as metadata from folders)
     union all
     (select path_tokens[$1] as "name",
            id,
            updated_at,
            created_at,
            last_accessed_at,
            metadata
     from storage.objects
     where objects.name ilike $2 || $3 || ''%''
       and bucket_id = $4
       and array_length(objects.path_tokens, 1) = $1
     order by ' || v_order_by || ')
     limit $5
     offset $6' using levels, prefix, search, bucketname, limits, offsets;
end;
$function$


-- Function: storage.search_v2
CREATE OR REPLACE FUNCTION storage.search_v2(prefix text, bucket_name text, limits integer DEFAULT 100, levels integer DEFAULT 1, start_after text DEFAULT ''::text, sort_order text DEFAULT 'asc'::text, sort_column text DEFAULT 'name'::text, sort_column_after text DEFAULT ''::text)
 RETURNS TABLE(key text, name text, id uuid, updated_at timestamp with time zone, created_at timestamp with time zone, last_accessed_at timestamp with time zone, metadata jsonb)
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    v_sort_col text;
    v_sort_ord text;
    v_limit int;
BEGIN
    -- Cap limit to maximum of 1500 records
    v_limit := LEAST(coalesce(limits, 100), 1500);

    -- Validate and normalize sort_order
    v_sort_ord := lower(coalesce(sort_order, 'asc'));
    IF v_sort_ord NOT IN ('asc', 'desc') THEN
        v_sort_ord := 'asc';
    END IF;

    -- Validate and normalize sort_column
    v_sort_col := lower(coalesce(sort_column, 'name'));
    IF v_sort_col NOT IN ('name', 'updated_at', 'created_at') THEN
        v_sort_col := 'name';
    END IF;

    -- Route to appropriate implementation
    IF v_sort_col = 'name' THEN
        -- Use list_objects_with_delimiter for name sorting (most efficient: O(k * log n))
        RETURN QUERY
        SELECT
            split_part(l.name, '/', levels) AS key,
            l.name AS name,
            l.id,
            l.updated_at,
            l.created_at,
            l.last_accessed_at,
            l.metadata
        FROM storage.list_objects_with_delimiter(
            bucket_name,
            coalesce(prefix, ''),
            '/',
            v_limit,
            start_after,
            '',
            v_sort_ord
        ) l;
    ELSE
        -- Use aggregation approach for timestamp sorting
        -- Not efficient for large datasets but supports correct pagination
        RETURN QUERY SELECT * FROM storage.search_by_timestamp(
            prefix, bucket_name, v_limit, levels, start_after,
            v_sort_ord, v_sort_col, sort_column_after
        );
    END IF;
END;
$function$


-- Function: storage.update_updated_at_column
CREATE OR REPLACE FUNCTION storage.update_updated_at_column()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW; 
END;
$function$


-- Function: vault._crypto_aead_det_decrypt
CREATE OR REPLACE FUNCTION vault._crypto_aead_det_decrypt(message bytea, additional bytea, key_id bigint, context bytea DEFAULT '\x7067736f6469756d'::bytea, nonce bytea DEFAULT NULL::bytea)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE
AS '$libdir/supabase_vault', $function$pgsodium_crypto_aead_det_decrypt_by_id$function$


-- Function: vault._crypto_aead_det_encrypt
CREATE OR REPLACE FUNCTION vault._crypto_aead_det_encrypt(message bytea, additional bytea, key_id bigint, context bytea DEFAULT '\x7067736f6469756d'::bytea, nonce bytea DEFAULT NULL::bytea)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE
AS '$libdir/supabase_vault', $function$pgsodium_crypto_aead_det_encrypt_by_id$function$


-- Function: vault._crypto_aead_det_noncegen
CREATE OR REPLACE FUNCTION vault._crypto_aead_det_noncegen()
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE
AS '$libdir/supabase_vault', $function$pgsodium_crypto_aead_det_noncegen$function$


-- Function: vault.create_secret
CREATE OR REPLACE FUNCTION vault.create_secret(new_secret text, new_name text DEFAULT NULL::text, new_description text DEFAULT ''::text, new_key_id uuid DEFAULT NULL::uuid)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
  rec record;
BEGIN
  INSERT INTO vault.secrets (secret, name, description)
  VALUES (
    new_secret,
    new_name,
    new_description
  )
  RETURNING * INTO rec;
  UPDATE vault.secrets s
  SET secret = encode(vault._crypto_aead_det_encrypt(
    message := convert_to(rec.secret, 'utf8'),
    additional := convert_to(s.id::text, 'utf8'),
    key_id := 0,
    context := 'pgsodium'::bytea,
    nonce := rec.nonce
  ), 'base64')
  WHERE id = rec.id;
  RETURN rec.id;
END
$function$


-- Function: vault.update_secret
CREATE OR REPLACE FUNCTION vault.update_secret(secret_id uuid, new_secret text DEFAULT NULL::text, new_name text DEFAULT NULL::text, new_description text DEFAULT NULL::text, new_key_id uuid DEFAULT NULL::uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
  decrypted_secret text := (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE id = secret_id);
BEGIN
  UPDATE vault.secrets s
  SET
    secret = CASE WHEN new_secret IS NULL THEN s.secret
                  ELSE encode(vault._crypto_aead_det_encrypt(
                    message := convert_to(new_secret, 'utf8'),
                    additional := convert_to(s.id::text, 'utf8'),
                    key_id := 0,
                    context := 'pgsodium'::bytea,
                    nonce := s.nonce
                  ), 'base64') END,
    name = coalesce(new_name, s.name),
    description = coalesce(new_description, s.description),
    updated_at = now()
  WHERE s.id = secret_id;
END
$function$



-- ============================================
-- TRIGGERS
-- ============================================

-- Trigger: trigger_image_categories_updated_at on public.image_categories
CREATE TRIGGER trigger_image_categories_updated_at BEFORE UPDATE ON public.image_categories FOR EACH ROW EXECUTE FUNCTION update_image_categories_updated_at()

-- Trigger: update_job_settings_updated_at on public.job_settings
CREATE TRIGGER update_job_settings_updated_at BEFORE UPDATE ON public.job_settings FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()

-- Trigger: update_jobs_updated_at on public.jobs
CREATE TRIGGER update_jobs_updated_at BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()

-- Trigger: tr_node_files_count on public.node_files
CREATE TRIGGER tr_node_files_count AFTER INSERT OR DELETE OR UPDATE OF node_id ON public.node_files FOR EACH ROW EXECUTE FUNCTION update_node_files_count()

-- Trigger: update_node_files_updated_at on public.node_files
CREATE TRIGGER update_node_files_updated_at BEFORE UPDATE ON public.node_files FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()

-- Trigger: tr_tree_nodes_children_count on public.tree_nodes
CREATE TRIGGER tr_tree_nodes_children_count AFTER INSERT OR DELETE OR UPDATE OF parent_id ON public.tree_nodes FOR EACH ROW EXECUTE FUNCTION update_parent_children_count()

-- Trigger: tr_tree_nodes_descendants_count on public.tree_nodes
CREATE TRIGGER tr_tree_nodes_descendants_count AFTER INSERT OR DELETE ON public.tree_nodes FOR EACH ROW EXECUTE FUNCTION update_ancestors_descendants_count()

-- Trigger: tr_tree_nodes_path_insert on public.tree_nodes
CREATE TRIGGER tr_tree_nodes_path_insert BEFORE INSERT ON public.tree_nodes FOR EACH ROW EXECUTE FUNCTION update_tree_node_path_and_depth()

-- Trigger: tr_tree_nodes_path_update on public.tree_nodes
CREATE TRIGGER tr_tree_nodes_path_update BEFORE UPDATE OF parent_id ON public.tree_nodes FOR EACH ROW WHEN ((old.parent_id IS DISTINCT FROM new.parent_id)) EXECUTE FUNCTION update_tree_node_path_and_depth()

-- Trigger: update_tree_nodes_updated_at on public.tree_nodes
CREATE TRIGGER update_tree_nodes_updated_at BEFORE UPDATE ON public.tree_nodes FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()

-- Trigger: tr_check_filters on realtime.subscription
CREATE TRIGGER tr_check_filters BEFORE INSERT OR UPDATE ON realtime.subscription FOR EACH ROW EXECUTE FUNCTION realtime.subscription_check_filters()

-- Trigger: enforce_bucket_name_length_trigger on storage.buckets
CREATE TRIGGER enforce_bucket_name_length_trigger BEFORE INSERT OR UPDATE OF name ON storage.buckets FOR EACH ROW EXECUTE FUNCTION storage.enforce_bucket_name_length()

-- Trigger: protect_buckets_delete on storage.buckets
CREATE TRIGGER protect_buckets_delete BEFORE DELETE ON storage.buckets FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete()

-- Trigger: protect_objects_delete on storage.objects
CREATE TRIGGER protect_objects_delete BEFORE DELETE ON storage.objects FOR EACH STATEMENT EXECUTE FUNCTION storage.protect_delete()

-- Trigger: update_objects_updated_at on storage.objects
CREATE TRIGGER update_objects_updated_at BEFORE UPDATE ON storage.objects FOR EACH ROW EXECUTE FUNCTION storage.update_updated_at_column()


-- ============================================
-- INDEXES
-- ============================================

-- Index on auth.audit_log_entries
CREATE INDEX audit_logs_instance_id_idx ON auth.audit_log_entries USING btree (instance_id);

-- Index on auth.flow_state
CREATE INDEX flow_state_created_at_idx ON auth.flow_state USING btree (created_at DESC);

-- Index on auth.flow_state
CREATE INDEX idx_auth_code ON auth.flow_state USING btree (auth_code);

-- Index on auth.flow_state
CREATE INDEX idx_user_id_auth_method ON auth.flow_state USING btree (user_id, authentication_method);

-- Index on auth.identities
CREATE INDEX identities_email_idx ON auth.identities USING btree (email text_pattern_ops);

-- Index on auth.identities
CREATE UNIQUE INDEX identities_provider_id_provider_unique ON auth.identities USING btree (provider_id, provider);

-- Index on auth.identities
CREATE INDEX identities_user_id_idx ON auth.identities USING btree (user_id);

-- Index on auth.mfa_amr_claims
CREATE UNIQUE INDEX amr_id_pk ON auth.mfa_amr_claims USING btree (id);

-- Index on auth.mfa_challenges
CREATE INDEX mfa_challenge_created_at_idx ON auth.mfa_challenges USING btree (created_at DESC);

-- Index on auth.mfa_factors
CREATE INDEX factor_id_created_at_idx ON auth.mfa_factors USING btree (user_id, created_at);

-- Index on auth.mfa_factors
CREATE UNIQUE INDEX mfa_factors_last_challenged_at_key ON auth.mfa_factors USING btree (last_challenged_at);

-- Index on auth.mfa_factors
CREATE UNIQUE INDEX mfa_factors_user_friendly_name_unique ON auth.mfa_factors USING btree (friendly_name, user_id) WHERE (TRIM(BOTH FROM friendly_name) <> ''::text);

-- Index on auth.mfa_factors
CREATE INDEX mfa_factors_user_id_idx ON auth.mfa_factors USING btree (user_id);

-- Index on auth.mfa_factors
CREATE UNIQUE INDEX unique_phone_factor_per_user ON auth.mfa_factors USING btree (user_id, phone);

-- Index on auth.oauth_authorizations
CREATE INDEX oauth_auth_pending_exp_idx ON auth.oauth_authorizations USING btree (expires_at) WHERE (status = 'pending'::auth.oauth_authorization_status);

-- Index on auth.oauth_authorizations
CREATE UNIQUE INDEX oauth_authorizations_authorization_code_key ON auth.oauth_authorizations USING btree (authorization_code);

-- Index on auth.oauth_authorizations
CREATE UNIQUE INDEX oauth_authorizations_authorization_id_key ON auth.oauth_authorizations USING btree (authorization_id);

-- Index on auth.oauth_client_states
CREATE INDEX idx_oauth_client_states_created_at ON auth.oauth_client_states USING btree (created_at);

-- Index on auth.oauth_clients
CREATE INDEX oauth_clients_deleted_at_idx ON auth.oauth_clients USING btree (deleted_at);

-- Index on auth.oauth_consents
CREATE INDEX oauth_consents_active_client_idx ON auth.oauth_consents USING btree (client_id) WHERE (revoked_at IS NULL);

-- Index on auth.oauth_consents
CREATE INDEX oauth_consents_active_user_client_idx ON auth.oauth_consents USING btree (user_id, client_id) WHERE (revoked_at IS NULL);

-- Index on auth.oauth_consents
CREATE UNIQUE INDEX oauth_consents_user_client_unique ON auth.oauth_consents USING btree (user_id, client_id);

-- Index on auth.oauth_consents
CREATE INDEX oauth_consents_user_order_idx ON auth.oauth_consents USING btree (user_id, granted_at DESC);

-- Index on auth.one_time_tokens
CREATE INDEX one_time_tokens_relates_to_hash_idx ON auth.one_time_tokens USING hash (relates_to);

-- Index on auth.one_time_tokens
CREATE INDEX one_time_tokens_token_hash_hash_idx ON auth.one_time_tokens USING hash (token_hash);

-- Index on auth.one_time_tokens
CREATE UNIQUE INDEX one_time_tokens_user_id_token_type_key ON auth.one_time_tokens USING btree (user_id, token_type);

-- Index on auth.refresh_tokens
CREATE INDEX refresh_tokens_instance_id_idx ON auth.refresh_tokens USING btree (instance_id);

-- Index on auth.refresh_tokens
CREATE INDEX refresh_tokens_instance_id_user_id_idx ON auth.refresh_tokens USING btree (instance_id, user_id);

-- Index on auth.refresh_tokens
CREATE INDEX refresh_tokens_parent_idx ON auth.refresh_tokens USING btree (parent);

-- Index on auth.refresh_tokens
CREATE INDEX refresh_tokens_session_id_revoked_idx ON auth.refresh_tokens USING btree (session_id, revoked);

-- Index on auth.refresh_tokens
CREATE UNIQUE INDEX refresh_tokens_token_unique ON auth.refresh_tokens USING btree (token);

-- Index on auth.refresh_tokens
CREATE INDEX refresh_tokens_updated_at_idx ON auth.refresh_tokens USING btree (updated_at DESC);

-- Index on auth.saml_providers
CREATE UNIQUE INDEX saml_providers_entity_id_key ON auth.saml_providers USING btree (entity_id);

-- Index on auth.saml_providers
CREATE INDEX saml_providers_sso_provider_id_idx ON auth.saml_providers USING btree (sso_provider_id);

-- Index on auth.saml_relay_states
CREATE INDEX saml_relay_states_created_at_idx ON auth.saml_relay_states USING btree (created_at DESC);

-- Index on auth.saml_relay_states
CREATE INDEX saml_relay_states_for_email_idx ON auth.saml_relay_states USING btree (for_email);

-- Index on auth.saml_relay_states
CREATE INDEX saml_relay_states_sso_provider_id_idx ON auth.saml_relay_states USING btree (sso_provider_id);

-- Index on auth.sessions
CREATE INDEX sessions_not_after_idx ON auth.sessions USING btree (not_after DESC);

-- Index on auth.sessions
CREATE INDEX sessions_oauth_client_id_idx ON auth.sessions USING btree (oauth_client_id);

-- Index on auth.sessions
CREATE INDEX sessions_user_id_idx ON auth.sessions USING btree (user_id);

-- Index on auth.sessions
CREATE INDEX user_id_created_at_idx ON auth.sessions USING btree (user_id, created_at);

-- Index on auth.sso_domains
CREATE UNIQUE INDEX sso_domains_domain_idx ON auth.sso_domains USING btree (lower(domain));

-- Index on auth.sso_domains
CREATE INDEX sso_domains_sso_provider_id_idx ON auth.sso_domains USING btree (sso_provider_id);

-- Index on auth.sso_providers
CREATE UNIQUE INDEX sso_providers_resource_id_idx ON auth.sso_providers USING btree (lower(resource_id));

-- Index on auth.sso_providers
CREATE INDEX sso_providers_resource_id_pattern_idx ON auth.sso_providers USING btree (resource_id text_pattern_ops);

-- Index on auth.users
CREATE UNIQUE INDEX confirmation_token_idx ON auth.users USING btree (confirmation_token) WHERE ((confirmation_token)::text !~ '^[0-9 ]*$'::text);

-- Index on auth.users
CREATE UNIQUE INDEX email_change_token_current_idx ON auth.users USING btree (email_change_token_current) WHERE ((email_change_token_current)::text !~ '^[0-9 ]*$'::text);

-- Index on auth.users
CREATE UNIQUE INDEX email_change_token_new_idx ON auth.users USING btree (email_change_token_new) WHERE ((email_change_token_new)::text !~ '^[0-9 ]*$'::text);

-- Index on auth.users
CREATE UNIQUE INDEX reauthentication_token_idx ON auth.users USING btree (reauthentication_token) WHERE ((reauthentication_token)::text !~ '^[0-9 ]*$'::text);

-- Index on auth.users
CREATE UNIQUE INDEX recovery_token_idx ON auth.users USING btree (recovery_token) WHERE ((recovery_token)::text !~ '^[0-9 ]*$'::text);

-- Index on auth.users
CREATE UNIQUE INDEX users_email_partial_key ON auth.users USING btree (email) WHERE (is_sso_user = false);

-- Index on auth.users
CREATE INDEX users_instance_id_email_idx ON auth.users USING btree (instance_id, lower((email)::text));

-- Index on auth.users
CREATE INDEX users_instance_id_idx ON auth.users USING btree (instance_id);

-- Index on auth.users
CREATE INDEX users_is_anonymous_idx ON auth.users USING btree (is_anonymous);

-- Index on auth.users
CREATE UNIQUE INDEX users_phone_key ON auth.users USING btree (phone);

-- Index on public.image_categories
CREATE INDEX idx_image_categories_code ON public.image_categories USING btree (code);

-- Index on public.image_categories
CREATE INDEX idx_image_categories_is_default ON public.image_categories USING btree (is_default) WHERE (is_default = true);

-- Index on public.image_categories
CREATE UNIQUE INDEX image_categories_code_key ON public.image_categories USING btree (code);

-- Index on public.job_files
CREATE INDEX idx_job_files_block_id ON public.job_files USING btree (((metadata ->> 'block_id'::text))) WHERE (file_type = 'crop'::text);

-- Index on public.job_files
CREATE INDEX idx_job_files_job_id ON public.job_files USING btree (job_id);

-- Index on public.job_files
CREATE INDEX idx_job_files_metadata ON public.job_files USING gin (metadata);

-- Index on public.job_files
CREATE INDEX idx_job_files_type ON public.job_files USING btree (file_type);

-- Index on public.job_settings
CREATE INDEX idx_job_settings_job_id ON public.job_settings USING btree (job_id);

-- Index on public.job_settings
CREATE UNIQUE INDEX job_settings_job_id_key ON public.job_settings USING btree (job_id);

-- Index on public.jobs
CREATE INDEX idx_jobs_active_status ON public.jobs USING btree (status, updated_at DESC) WHERE (status = ANY (ARRAY['queued'::text, 'processing'::text]));

-- Index on public.jobs
CREATE INDEX idx_jobs_client_id ON public.jobs USING btree (client_id);

-- Index on public.jobs
CREATE INDEX idx_jobs_created_at ON public.jobs USING btree (created_at DESC);

-- Index on public.jobs
CREATE INDEX idx_jobs_document_id ON public.jobs USING btree (document_id);

-- Index on public.jobs
CREATE INDEX idx_jobs_node_id ON public.jobs USING btree (node_id);

-- Index on public.jobs
CREATE INDEX idx_jobs_status ON public.jobs USING btree (status);

-- Index on public.jobs
CREATE INDEX idx_jobs_updated_at ON public.jobs USING btree (updated_at DESC);

-- Index on public.node_files
CREATE INDEX idx_node_files_node_id ON public.node_files USING btree (node_id);

-- Index on public.node_files
CREATE INDEX idx_node_files_node_type ON public.node_files USING btree (node_id, file_type);

-- Index on public.node_files
CREATE INDEX idx_node_files_r2_key ON public.node_files USING btree (r2_key);

-- Index on public.node_files
CREATE INDEX idx_node_files_type ON public.node_files USING btree (file_type);

-- Index on public.node_files
CREATE UNIQUE INDEX node_files_node_id_r2_key_unique ON public.node_files USING btree (node_id, r2_key);

-- Index on public.section_types
CREATE UNIQUE INDEX section_types_code_key ON public.section_types USING btree (code);

-- Index on public.stage_types
CREATE UNIQUE INDEX stage_types_code_key ON public.stage_types USING btree (code);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_depth ON public.tree_nodes USING btree (depth);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_folders ON public.tree_nodes USING btree (parent_id, sort_order) WHERE (node_type = 'folder'::text);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_is_locked ON public.tree_nodes USING btree (is_locked) WHERE ((node_type = 'document'::text) AND (is_locked = true));

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_parent_id ON public.tree_nodes USING btree (parent_id);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_parent_sort ON public.tree_nodes USING btree (parent_id, sort_order, created_at);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_path ON public.tree_nodes USING btree (path text_pattern_ops);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_pdf_status ON public.tree_nodes USING btree (pdf_status) WHERE (node_type = 'document'::text);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_roots ON public.tree_nodes USING btree (sort_order) WHERE (parent_id IS NULL);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_sort ON public.tree_nodes USING btree (parent_id, sort_order);

-- Index on public.tree_nodes
CREATE INDEX idx_tree_nodes_type ON public.tree_nodes USING btree (node_type);

-- Index on realtime.messages
CREATE INDEX messages_inserted_at_topic_index ON ONLY realtime.messages USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_16
CREATE INDEX messages_2026_01_16_inserted_at_topic_idx ON realtime.messages_2026_01_16 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_17
CREATE INDEX messages_2026_01_17_inserted_at_topic_idx ON realtime.messages_2026_01_17 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_18
CREATE INDEX messages_2026_01_18_inserted_at_topic_idx ON realtime.messages_2026_01_18 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_19
CREATE INDEX messages_2026_01_19_inserted_at_topic_idx ON realtime.messages_2026_01_19 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_20
CREATE INDEX messages_2026_01_20_inserted_at_topic_idx ON realtime.messages_2026_01_20 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_21
CREATE INDEX messages_2026_01_21_inserted_at_topic_idx ON realtime.messages_2026_01_21 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.messages_2026_01_22
CREATE INDEX messages_2026_01_22_inserted_at_topic_idx ON realtime.messages_2026_01_22 USING btree (inserted_at DESC, topic) WHERE ((extension = 'broadcast'::text) AND (private IS TRUE));

-- Index on realtime.subscription
CREATE INDEX ix_realtime_subscription_entity ON realtime.subscription USING btree (entity);

-- Index on realtime.subscription
CREATE UNIQUE INDEX pk_subscription ON realtime.subscription USING btree (id);

-- Index on realtime.subscription
CREATE UNIQUE INDEX subscription_subscription_id_entity_filters_action_filter_key ON realtime.subscription USING btree (subscription_id, entity, filters, action_filter);

-- Index on storage.buckets
CREATE UNIQUE INDEX bname ON storage.buckets USING btree (name);

-- Index on storage.buckets_analytics
CREATE UNIQUE INDEX buckets_analytics_unique_name_idx ON storage.buckets_analytics USING btree (name) WHERE (deleted_at IS NULL);

-- Index on storage.migrations
CREATE UNIQUE INDEX migrations_name_key ON storage.migrations USING btree (name);

-- Index on storage.objects
CREATE UNIQUE INDEX bucketid_objname ON storage.objects USING btree (bucket_id, name);

-- Index on storage.objects
CREATE INDEX idx_objects_bucket_id_name ON storage.objects USING btree (bucket_id, name COLLATE "C");

-- Index on storage.objects
CREATE INDEX idx_objects_bucket_id_name_lower ON storage.objects USING btree (bucket_id, lower(name) COLLATE "C");

-- Index on storage.objects
CREATE INDEX name_prefix_search ON storage.objects USING btree (name text_pattern_ops);

-- Index on storage.s3_multipart_uploads
CREATE INDEX idx_multipart_uploads_list ON storage.s3_multipart_uploads USING btree (bucket_id, key, created_at);

-- Index on storage.vector_indexes
CREATE UNIQUE INDEX vector_indexes_name_bucket_id_idx ON storage.vector_indexes USING btree (name, bucket_id);

-- Index on vault.secrets
CREATE UNIQUE INDEX secrets_name_idx ON vault.secrets USING btree (name) WHERE (name IS NOT NULL);


-- ============================================
-- ROLES AND PRIVILEGES
-- ============================================

-- Role: anon
CREATE ROLE anon;

-- Role: authenticated
CREATE ROLE authenticated;

-- Role: authenticator
CREATE ROLE authenticator WITH LOGIN NOINHERIT;

-- Role: dashboard_user
CREATE ROLE dashboard_user WITH CREATEDB CREATEROLE REPLICATION;

-- Role: postgres
CREATE ROLE postgres WITH CREATEDB CREATEROLE LOGIN REPLICATION BYPASSRLS;

-- Role: service_role
CREATE ROLE service_role WITH BYPASSRLS;

-- Role: supabase_admin
CREATE ROLE supabase_admin WITH SUPERUSER CREATEDB CREATEROLE LOGIN REPLICATION BYPASSRLS;

-- Role: supabase_auth_admin
CREATE ROLE supabase_auth_admin WITH CREATEROLE LOGIN NOINHERIT;

-- Role: supabase_etl_admin
CREATE ROLE supabase_etl_admin WITH LOGIN REPLICATION BYPASSRLS;

-- Role: supabase_read_only_user
CREATE ROLE supabase_read_only_user WITH LOGIN BYPASSRLS;

-- Role: supabase_realtime_admin
CREATE ROLE supabase_realtime_admin WITH NOINHERIT;

-- Role: supabase_replication_admin
CREATE ROLE supabase_replication_admin WITH LOGIN REPLICATION;

-- Role: supabase_storage_admin
CREATE ROLE supabase_storage_admin WITH CREATEROLE LOGIN NOINHERIT;
