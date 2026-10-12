-- A close call may legitimately occupy the full 120 second Gateway timeout.
-- Keep a 30 second safety margin before another worker may take ownership.
ALTER TABLE survey_preview_session ADD COLUMN close_lease_until timestamptz;

CREATE INDEX survey_preview_session_close_lease
    ON survey_preview_session (tenant_id, close_lease_until)
    WHERE status = 'closing';
