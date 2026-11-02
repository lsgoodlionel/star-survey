-- Platform personnel grants are separate from tenant grants. The runtime has no direct table write access.
CREATE TABLE platform_admin_grant (
    identity_source text NOT NULL DEFAULT 'org-session-v1' CHECK (identity_source = 'org-session-v1'),
    identity_tenant uuid NOT NULL,
    subject uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (identity_source, identity_tenant, subject),
    FOREIGN KEY (identity_tenant, subject) REFERENCES identity_binding (tenant_id, principal_id)
);
CREATE TABLE platform_admin_audit (
    id bigserial PRIMARY KEY,
    actor_source text CHECK (actor_source IS NULL OR actor_source = 'org-session-v1'),
    actor_tenant uuid,
    actor_subject uuid,
    target_source text NOT NULL DEFAULT 'org-session-v1' CHECK (target_source = 'org-session-v1'),
    target_tenant uuid NOT NULL,
    target_subject uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('bootstrap', 'grant', 'revoke')),
    trace_id text NOT NULL,
    operator_db_user text NOT NULL,
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
    occurred_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON platform_admin_grant, platform_admin_audit FROM PUBLIC, platform_app;
REVOKE ALL ON SEQUENCE platform_admin_audit_id_seq FROM PUBLIC, platform_app;

-- RLS-protected binding and revocation checks run under the target tenant and restore caller context.
CREATE FUNCTION platform_admin_identity_usable(p_tenant uuid, p_subject uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE previous_tenant text; usable boolean;
BEGIN
    previous_tenant := current_setting('app.tenant_id', true);
    PERFORM set_config('app.tenant_id', p_tenant::text, true);
    SELECT EXISTS (
        SELECT 1 FROM public.identity_binding b JOIN public.tenant t ON t.id = b.tenant_id
        JOIN public.access_member m ON m.tenant_id = b.tenant_id AND m.actor_id = b.principal_id::text
        WHERE b.tenant_id = p_tenant AND b.principal_id = p_subject AND t.status = 'active' AND m.status = 'active'
          AND EXISTS (SELECT 1 FROM public.org_connection c WHERE c.tenant_id = b.tenant_id
                      AND c.provider = b.provider AND c.corp_id = b.app_id AND c.enabled)
          AND NOT EXISTS (SELECT 1 FROM public.identity_binding_revocation r
              WHERE r.tenant_id = p_tenant AND r.principal_id = p_subject AND r.reinstated_at IS NULL)
    ) INTO usable;
    PERFORM set_config('app.tenant_id', coalesce(previous_tenant, ''), true);
    RETURN usable;
EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('app.tenant_id', coalesce(previous_tenant, ''), true);
    RAISE;
END $$;
REVOKE ALL ON FUNCTION platform_admin_identity_usable(uuid, uuid) FROM PUBLIC, platform_app;

CREATE FUNCTION platform_admin_active(p_tenant uuid, p_subject uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT EXISTS (SELECT 1 FROM public.platform_admin_grant WHERE identity_source = 'org-session-v1' AND identity_tenant = p_tenant AND subject = p_subject AND active)
      AND public.platform_admin_identity_usable(p_tenant, p_subject) $$;
REVOKE ALL ON FUNCTION platform_admin_active(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_admin_active(uuid, uuid) TO platform_app;

-- Only the database owner can run the initial bootstrap; it cannot add a second identity.
CREATE FUNCTION platform_admin_bootstrap(p_tenant uuid, p_subject uuid, p_trace text, p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_tenant IS NULL OR p_subject IS NULL OR p_trace IS NULL OR btrim(p_trace) = ''
        OR p_reason IS NULL OR btrim(p_reason) = '' OR length(p_reason) > 1000 THEN
        RAISE EXCEPTION 'invalid bootstrap';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    IF NOT public.platform_admin_identity_usable(p_tenant, p_subject) THEN
        RAISE EXCEPTION 'bootstrap identity unavailable';
    END IF;
    IF EXISTS (SELECT 1 FROM public.platform_admin_grant) THEN
        IF EXISTS (SELECT 1 FROM public.platform_admin_grant WHERE identity_source = 'org-session-v1' AND identity_tenant = p_tenant AND subject = p_subject AND active) THEN
            RETURN false;
        END IF;
        RAISE EXCEPTION 'bootstrap already completed';
    END IF;
    INSERT INTO public.platform_admin_grant(identity_tenant, subject) VALUES (p_tenant, p_subject);
    INSERT INTO public.platform_admin_audit(target_tenant, target_subject, action, trace_id, operator_db_user, reason)
        VALUES (p_tenant, p_subject, 'bootstrap', p_trace, session_user, p_reason);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_bootstrap(uuid, uuid, text, text) FROM PUBLIC, platform_app;

-- Application must pass the independently authenticated actor, never request-supplied actor fields.
CREATE FUNCTION platform_admin_change(p_actor_tenant uuid, p_actor uuid, p_target_tenant uuid,
        p_target uuid, p_grant boolean, p_trace text, p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE existing boolean;
BEGIN
    IF p_actor_tenant IS NULL OR p_actor IS NULL OR p_target_tenant IS NULL OR p_target IS NULL
        OR p_grant IS NULL OR p_trace IS NULL OR btrim(p_trace) = ''
        OR p_reason IS NULL OR btrim(p_reason) = '' OR length(p_reason) > 1000 THEN
        RAISE EXCEPTION 'invalid grant change';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    IF NOT public.platform_admin_active(p_actor_tenant, p_actor) THEN
        RAISE EXCEPTION 'platform administrator required' USING ERRCODE = '42501';
    END IF;
    IF p_grant AND NOT public.platform_admin_identity_usable(p_target_tenant, p_target) THEN
        RAISE EXCEPTION 'target identity unavailable';
    END IF;
    SELECT active INTO existing FROM public.platform_admin_grant WHERE identity_source = 'org-session-v1' AND identity_tenant = p_target_tenant AND subject = p_target;
    IF coalesce(existing, false) = p_grant THEN RETURN false; END IF;
    IF NOT p_grant AND public.platform_admin_identity_usable(p_target_tenant, p_target)
        AND (SELECT count(*) FROM public.platform_admin_grant g WHERE g.active
             AND public.platform_admin_identity_usable(g.identity_tenant, g.subject)) <= 1 THEN
        RAISE EXCEPTION 'cannot revoke last usable administrator';
    END IF;
    INSERT INTO public.platform_admin_grant(identity_tenant, subject, active)
        VALUES (p_target_tenant, p_target, p_grant)
        ON CONFLICT (identity_source, identity_tenant, subject) DO UPDATE SET active = EXCLUDED.active, updated_at = now();
    INSERT INTO public.platform_admin_audit(actor_source, actor_tenant, actor_subject, target_tenant, target_subject, action, trace_id, operator_db_user, reason)
        VALUES ('org-session-v1', p_actor_tenant, p_actor, p_target_tenant, p_target, CASE WHEN p_grant THEN 'grant' ELSE 'revoke' END, p_trace, session_user, p_reason);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_change(uuid, uuid, uuid, uuid, boolean, text, text) FROM PUBLIC, platform_app;
