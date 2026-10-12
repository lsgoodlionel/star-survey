-- Preview SID must be registered before activation. Closing is claimed by one owner.
ALTER TABLE survey_preview_session ADD COLUMN close_owner uuid;

CREATE INDEX survey_preview_session_creating_reconcile
    ON survey_preview_session (tenant_id, updated_at)
    WHERE status = 'creating';

DROP TRIGGER response_projection_reject_preview ON response_projection;
DROP FUNCTION reject_preview_response_projection();

-- Returning NULL makes EngineEventInbox.record report false, so projection and outbox are both skipped.
CREATE FUNCTION reject_preview_engine_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM survey_preview_session p
         WHERE p.tenant_id = NEW.tenant_id
           AND p.engine_instance_id = NEW.engine_instance_id
           AND p.engine_sid = NEW.survey_id
    ) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER engine_event_reject_preview
BEFORE INSERT ON engine_event_inbox
FOR EACH ROW EXECUTE FUNCTION reject_preview_engine_event();
