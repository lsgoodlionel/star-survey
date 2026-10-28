-- 隔离运行时预览。预览 SID 从不登记为正式发布版本或公开路由。
CREATE TABLE survey_preview_session (
    tenant_id          uuid        NOT NULL,
    id                 uuid        NOT NULL,
    request_id         uuid        NOT NULL,
    survey_id          uuid        NOT NULL,
    draft_version      integer     NOT NULL CHECK (draft_version > 0),
    definition         jsonb       NOT NULL,
    requested_by       text        NOT NULL,
    engine_instance_id text        NOT NULL,
    engine_sid         integer CHECK (engine_sid > 0),
    generation         text        NOT NULL CHECK (generation ~ '^preview-[a-z0-9][a-z0-9-]{2,55}$'),
    preview_url        text,
    expires_at         timestamptz NOT NULL,
    status             text        NOT NULL CHECK (status IN
                           ('creating', 'ready', 'closing', 'closed', 'failed', 'cleanup_failed')),
    failure            text,
    cleanup_attempts   integer     NOT NULL DEFAULT 0 CHECK (cleanup_attempts >= 0),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    closed_at          timestamptz,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, request_id),
    UNIQUE (tenant_id, engine_instance_id, engine_sid),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id),
    FOREIGN KEY (tenant_id, engine_instance_id) REFERENCES engine_instance (tenant_id, id)
);
CREATE UNIQUE INDEX survey_preview_session_public_id ON survey_preview_session (id);
CREATE INDEX survey_preview_session_cleanup
    ON survey_preview_session (tenant_id, expires_at)
    WHERE status IN ('ready', 'cleanup_failed');

ALTER TABLE survey_preview_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_preview_session FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_preview_session
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
REVOKE DELETE ON survey_preview_session FROM platform_app;

-- Engine 事件仍可到达 inbox，但 preview SID 的作答绝不能进入正式答卷投影。
CREATE FUNCTION reject_preview_response_projection() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM survey_preview_session p
         WHERE p.tenant_id = NEW.tenant_id
           AND p.engine_instance_id = NEW.engine_instance_id
           AND p.engine_sid = NEW.survey_id
    ) THEN
        DELETE FROM response_projection
         WHERE tenant_id = NEW.tenant_id
           AND engine_instance_id = NEW.engine_instance_id
           AND survey_id = NEW.survey_id
           AND generation = NEW.generation
           AND response_id = NEW.response_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER response_projection_reject_preview
AFTER INSERT ON response_projection
FOR EACH ROW EXECUTE FUNCTION reject_preview_response_projection();
