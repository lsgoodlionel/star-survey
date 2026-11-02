-- Platform personnel grants are separate from tenant grants. The runtime has no direct table write access.
CREATE TABLE platform_admin_grant (
    identity_tenant uuid NOT NULL,
    subject uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (identity_tenant, subject),
    FOREIGN KEY (identity_tenant, subject) REFERENCES identity_binding (tenant_id, principal_id)
);
CREATE TABLE platform_admin_audit (
    id bigserial PRIMARY KEY,
    actor_tenant uuid,
    actor_subject uuid,
    target_tenant uuid NOT NULL,
    target_subject uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('bootstrap', 'grant', 'revoke')),
    trace_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON platform_admin_grant, platform_admin_audit FROM PUBLIC, platform_app;
REVOKE ALL ON SEQUENCE platform_admin_audit_id_seq FROM PUBLIC, platform_app;

CREATE FUNCTION platform_admin_active(p_tenant uuid, p_subject uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT EXISTS (SELECT 1 FROM public.platform_admin_grant WHERE identity_tenant = p_tenant AND subject = p_subject AND active) $$;
REVOKE ALL ON FUNCTION platform_admin_active(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_admin_active(uuid, uuid) TO platform_app;

-- Only the database owner can run the initial bootstrap; it cannot add a second identity.
CREATE FUNCTION platform_admin_bootstrap(p_tenant uuid, p_subject uuid, p_trace text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_tenant IS NULL OR p_subject IS NULL OR p_trace IS NULL OR btrim(p_trace) = '' THEN
        RAISE EXCEPTION 'invalid bootstrap';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    IF EXISTS (SELECT 1 FROM public.platform_admin_grant) THEN
        IF EXISTS (SELECT 1 FROM public.platform_admin_grant WHERE identity_tenant = p_tenant AND subject = p_subject AND active) THEN
            RETURN false;
        END IF;
        RAISE EXCEPTION 'bootstrap already completed';
    END IF;
    INSERT INTO public.platform_admin_grant(identity_tenant, subject) VALUES (p_tenant, p_subject);
    INSERT INTO public.platform_admin_audit(target_tenant, target_subject, action, trace_id)
        VALUES (p_tenant, p_subject, 'bootstrap', p_trace);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_bootstrap(uuid, uuid, text) FROM PUBLIC, platform_app;

-- Application must pass the independently authenticated actor, never request-supplied actor fields.
CREATE FUNCTION platform_admin_change(p_actor_tenant uuid, p_actor uuid, p_target_tenant uuid,
        p_target uuid, p_grant boolean, p_trace text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE existing boolean;
BEGIN
    IF p_actor_tenant IS NULL OR p_actor IS NULL OR p_target_tenant IS NULL OR p_target IS NULL
        OR p_grant IS NULL OR p_trace IS NULL OR btrim(p_trace) = '' THEN
        RAISE EXCEPTION 'invalid grant change';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    IF NOT EXISTS (SELECT 1 FROM public.platform_admin_grant WHERE identity_tenant = p_actor_tenant AND subject = p_actor AND active) THEN
        RAISE EXCEPTION 'platform administrator required' USING ERRCODE = '42501';
    END IF;
    SELECT active INTO existing FROM public.platform_admin_grant WHERE identity_tenant = p_target_tenant AND subject = p_target;
    IF coalesce(existing, false) = p_grant THEN RETURN false; END IF;
    IF NOT p_grant AND (SELECT count(*) FROM public.platform_admin_grant WHERE active) <= 1 THEN
        RAISE EXCEPTION 'cannot revoke last administrator';
    END IF;
    INSERT INTO public.platform_admin_grant(identity_tenant, subject, active)
        VALUES (p_target_tenant, p_target, p_grant)
        ON CONFLICT (identity_tenant, subject) DO UPDATE SET active = EXCLUDED.active, updated_at = now();
    INSERT INTO public.platform_admin_audit(actor_tenant, actor_subject, target_tenant, target_subject, action, trace_id)
        VALUES (p_actor_tenant, p_actor, p_target_tenant, p_target, CASE WHEN p_grant THEN 'grant' ELSE 'revoke' END, p_trace);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_change(uuid, uuid, uuid, uuid, boolean, text) FROM PUBLIC, platform_app;
