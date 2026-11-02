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
    action text NOT NULL CHECK (action IN ('bootstrap', 'grant', 'revoke', 'identity_invalidated', 'recovery')),
    trace_id text NOT NULL,
    operator_db_user text NOT NULL,
    operator_asserted text,
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

-- Take the shared grant lock before PostgreSQL acquires any affected identity rows.
-- In particular, an AFTER ROW trigger alone would invert the order under concurrency.
CREATE FUNCTION platform_admin_lock_identity_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(1979110201);
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION platform_admin_lock_identity_change() FROM PUBLIC, platform_app;
CREATE TRIGGER platform_admin_tenant_identity_lock BEFORE UPDATE OF status ON tenant
    FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_lock_identity_change();
CREATE TRIGGER platform_admin_member_identity_lock BEFORE UPDATE OF status OR DELETE ON access_member
    FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_lock_identity_change();
CREATE TRIGGER platform_admin_connection_identity_lock BEFORE UPDATE OF enabled, provider, corp_id OR DELETE ON org_connection
    FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_lock_identity_change();
CREATE TRIGGER platform_admin_binding_identity_lock BEFORE UPDATE ON identity_binding
    FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_lock_identity_change();
CREATE TRIGGER platform_admin_revocation_identity_lock BEFORE INSERT OR UPDATE ON identity_binding_revocation
    FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_lock_identity_change();

-- Identity changes and grant changes share one transaction lock. Invalid grants remain
-- inactive after an identity is restored; an explicit new grant is required.
CREATE FUNCTION platform_admin_invalidate_unusable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE affected_tenant uuid; g record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        affected_tenant := OLD.tenant_id;
    ELSIF TG_TABLE_NAME = 'tenant' THEN
        affected_tenant := NEW.id;
    ELSE
        affected_tenant := NEW.tenant_id;
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    FOR g IN SELECT identity_source, identity_tenant, subject FROM public.platform_admin_grant
             WHERE identity_tenant = affected_tenant AND active
    LOOP
        IF NOT public.platform_admin_identity_usable(g.identity_tenant, g.subject) THEN
            UPDATE public.platform_admin_grant SET active = false, updated_at = now()
              WHERE identity_source = g.identity_source AND identity_tenant = g.identity_tenant AND subject = g.subject AND active;
            IF FOUND THEN
                INSERT INTO public.platform_admin_audit(target_source, target_tenant, target_subject, action,
                    trace_id, operator_db_user, reason)
                  VALUES (g.identity_source, g.identity_tenant, g.subject, 'identity_invalidated',
                    'identity-state-change', session_user, TG_TABLE_NAME || ' identity became unavailable');
            END IF;
        END IF;
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION platform_admin_invalidate_unusable() FROM PUBLIC, platform_app;
CREATE TRIGGER platform_admin_tenant_identity_change AFTER UPDATE OF status ON tenant
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_member_identity_change AFTER UPDATE OF status ON access_member
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_member_deleted AFTER DELETE ON access_member
    FOR EACH ROW EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_connection_identity_change AFTER UPDATE OF enabled, provider, corp_id ON org_connection
    FOR EACH ROW EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_connection_deleted AFTER DELETE ON org_connection
    FOR EACH ROW EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_binding_identity_change AFTER UPDATE ON identity_binding
    FOR EACH ROW EXECUTE FUNCTION platform_admin_invalidate_unusable();
CREATE TRIGGER platform_admin_binding_revoked AFTER INSERT OR UPDATE ON identity_binding_revocation
    FOR EACH ROW EXECUTE FUNCTION platform_admin_invalidate_unusable();

-- Only the database owner can run the initial bootstrap; it cannot add a second identity.
CREATE FUNCTION platform_admin_bootstrap(p_tenant uuid, p_subject uuid, p_trace text, p_reason text, p_operator text) RETURNS boolean
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
    INSERT INTO public.platform_admin_audit(target_tenant, target_subject, action, trace_id, operator_db_user, operator_asserted, reason)
        VALUES (p_tenant, p_subject, 'bootstrap', p_trace, session_user, p_operator, p_reason);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_bootstrap(uuid, uuid, text, text, text) FROM PUBLIC, platform_app;
CREATE FUNCTION platform_admin_bootstrap(p_tenant uuid, p_subject uuid, p_trace text, p_reason text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT public.platform_admin_bootstrap(p_tenant, p_subject, p_trace, p_reason, NULL) $$;
REVOKE ALL ON FUNCTION platform_admin_bootstrap(uuid, uuid, text, text) FROM PUBLIC, platform_app;

-- Owner-only recovery after all identities became unusable. This is not bootstrap:
-- an earlier grant must exist, and no currently usable grant may remain.
CREATE FUNCTION platform_admin_recover(p_tenant uuid, p_subject uuid, p_trace text, p_reason text, p_operator text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_tenant IS NULL OR p_subject IS NULL OR p_trace IS NULL OR btrim(p_trace) = ''
        OR p_reason IS NULL OR btrim(p_reason) = '' OR length(p_reason) > 1000 THEN
        RAISE EXCEPTION 'invalid recovery';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    IF NOT EXISTS (SELECT 1 FROM public.platform_admin_grant) THEN
        RAISE EXCEPTION 'bootstrap not completed';
    END IF;
    IF public.platform_admin_active(p_tenant, p_subject) THEN RETURN false; END IF;
    IF EXISTS (SELECT 1 FROM public.platform_admin_grant g WHERE g.active
               AND public.platform_admin_identity_usable(g.identity_tenant, g.subject)) THEN
        RAISE EXCEPTION 'recovery requires zero usable administrators';
    END IF;
    IF NOT public.platform_admin_identity_usable(p_tenant, p_subject) THEN
        RAISE EXCEPTION 'recovery identity unavailable';
    END IF;
    INSERT INTO public.platform_admin_grant(identity_tenant, subject, active)
        VALUES (p_tenant, p_subject, true)
        ON CONFLICT (identity_source, identity_tenant, subject) DO UPDATE SET active = true, updated_at = now();
    INSERT INTO public.platform_admin_audit(target_tenant, target_subject, action, trace_id, operator_db_user, operator_asserted, reason)
        VALUES (p_tenant, p_subject, 'recovery', p_trace, session_user, p_operator, p_reason);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION platform_admin_recover(uuid, uuid, text, text, text) FROM PUBLIC, platform_app;
CREATE FUNCTION platform_admin_recover(p_tenant uuid, p_subject uuid, p_trace text, p_reason text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT public.platform_admin_recover(p_tenant, p_subject, p_trace, p_reason, NULL) $$;
REVOKE ALL ON FUNCTION platform_admin_recover(uuid, uuid, text, text) FROM PUBLIC, platform_app;

-- A deployer login can be granted only these wrappers by a deployment DBA.
-- session_user is database-authenticated; operator_asserted is an explicit claim,
-- whose mapping to a person must be verified by the deployment's identity process.
CREATE FUNCTION platform_admin_bootstrap_attributed(p_tenant uuid, p_subject uuid, p_trace text,
        p_reason text, p_operator text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed boolean;
BEGIN
    IF p_operator IS NULL OR p_operator !~ '^[A-Za-z0-9][A-Za-z0-9@._:-]{1,127}$' THEN
        RAISE EXCEPTION 'explicit deployment operator required';
    END IF;
    changed := public.platform_admin_bootstrap(p_tenant, p_subject, p_trace, p_reason, p_operator);
    RETURN changed;
END $$;
REVOKE ALL ON FUNCTION platform_admin_bootstrap_attributed(uuid, uuid, text, text, text) FROM PUBLIC, platform_app;

CREATE FUNCTION platform_admin_recover_attributed(p_tenant uuid, p_subject uuid, p_trace text,
        p_reason text, p_operator text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE changed boolean;
BEGIN
    IF p_operator IS NULL OR p_operator !~ '^[A-Za-z0-9][A-Za-z0-9@._:-]{1,127}$' THEN
        RAISE EXCEPTION 'explicit deployment operator required';
    END IF;
    changed := public.platform_admin_recover(p_tenant, p_subject, p_trace, p_reason, p_operator);
    RETURN changed;
END $$;
REVOKE ALL ON FUNCTION platform_admin_recover_attributed(uuid, uuid, text, text, text) FROM PUBLIC, platform_app;

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

-- The trusted application authenticates actor/sid from SecurityContext; the
-- database serializes and rechecks the current session, grant and target.
CREATE FUNCTION platform_admin_change_session(p_actor_tenant uuid, p_actor uuid, p_session uuid,
        p_target_tenant uuid, p_target uuid, p_grant boolean, p_trace text, p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE previous_tenant text; session_live boolean; changed boolean;
BEGIN
    IF p_actor_tenant IS NULL OR p_actor IS NULL OR p_session IS NULL THEN
        RAISE EXCEPTION 'verified session required' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(1979110201);
    previous_tenant := current_setting('app.tenant_id', true);
    PERFORM set_config('app.tenant_id', p_actor_tenant::text, true);
    SELECT true INTO session_live FROM public.org_login_session s
       WHERE s.id = p_session AND s.tenant_id = p_actor_tenant AND s.principal_id = p_actor
         AND s.revoked_at IS NULL AND s.expires_at > clock_timestamp()
         AND NOT EXISTS (SELECT 1 FROM public.identity_binding_revocation r
             WHERE r.tenant_id = p_actor_tenant AND r.principal_id = p_actor AND r.reinstated_at IS NULL)
       FOR UPDATE OF s;
    IF NOT coalesce(session_live, false) THEN
        RAISE EXCEPTION 'verified session unavailable' USING ERRCODE = '42501';
    END IF;
    -- A row lock may have waited across the expiry instant; check wall time again.
    SELECT s.revoked_at IS NULL AND s.expires_at > clock_timestamp() INTO session_live
      FROM public.org_login_session s WHERE s.id = p_session AND s.tenant_id = p_actor_tenant
        AND s.principal_id = p_actor;
    IF NOT coalesce(session_live, false) THEN
        RAISE EXCEPTION 'verified session unavailable' USING ERRCODE = '42501';
    END IF;
    changed := public.platform_admin_change(p_actor_tenant, p_actor, p_target_tenant, p_target,
                                            p_grant, p_trace, p_reason);
    PERFORM set_config('app.tenant_id', coalesce(previous_tenant, ''), true);
    RETURN changed;
EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('app.tenant_id', coalesce(previous_tenant, ''), true);
    RAISE;
END $$;
REVOKE ALL ON FUNCTION platform_admin_change_session(uuid, uuid, uuid, uuid, uuid, boolean, text, text)
    FROM PUBLIC, platform_app;
GRANT EXECUTE ON FUNCTION platform_admin_change_session(uuid, uuid, uuid, uuid, uuid, boolean, text, text)
    TO platform_app;
