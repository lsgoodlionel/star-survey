-- Platform catalog mutations are audited independently of customer tenant data.
CREATE TABLE platform_plan_audit (
    id bigserial PRIMARY KEY,
    actor_tenant uuid NOT NULL,
    actor_subject text NOT NULL,
    plan_id uuid NOT NULL REFERENCES plan_version(id),
    plan_code text NOT NULL,
    plan_version integer NOT NULL,
    trace_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON platform_plan_audit FROM PUBLIC, platform_app;
GRANT SELECT, INSERT ON platform_plan_audit TO platform_app;
GRANT USAGE, SELECT ON SEQUENCE platform_plan_audit_id_seq TO platform_app;
