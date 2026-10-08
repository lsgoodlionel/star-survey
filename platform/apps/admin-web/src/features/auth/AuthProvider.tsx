import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { z } from 'zod';
import { createApiClient, type ApiClient } from '../../shared/api/http';
import { getMe } from '../../shared/api/me';
import { tokenViewSchema, type Me } from '../../shared/api/schemas';

export interface AuthSession {
  token: string;
  me: Me;
  expiresAt: number;
}

interface AuthContextValue {
  api: ApiClient;
  authenticateWithToken(token: string, expiresIn: number): Promise<void>;
  exchangeHandoff(tenantId: string, handoff: string): Promise<void>;
  logout(): Promise<void>;
  session: AuthSession | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
  beforeSessionClear?: () => void | Promise<void>;
}

export function AuthProvider({ children, beforeSessionClear }: AuthProviderProps) {
  const [session, setSession] = useState<AuthSession | null>(null);

  const handleUnauthorized = useCallback(async () => {
    try {
      await beforeSessionClear?.();
    } finally {
      setSession(null);
    }
  }, [beforeSessionClear]);

  const api = useMemo(
    () =>
      createApiClient({
        getToken: () => session?.token ?? null,
        onUnauthorized: handleUnauthorized,
      }),
    [handleUnauthorized, session?.token],
  );

  const authenticateWithToken = useCallback(
    async (token: string, expiresIn: number) => {
      const tokenApi = createApiClient({
        getToken: () => token,
        onUnauthorized: handleUnauthorized,
      });
      const me = await getMe(tokenApi);
      setSession({ token, me, expiresAt: Date.now() + expiresIn * 1_000 });
    },
    [handleUnauthorized],
  );

  const exchangeHandoff = useCallback(
    async (tenantId: string, handoff: string) => {
      const anonymousApi = createApiClient();
      const token = await anonymousApi.request({
        path: `/v1/auth/org/${encodeURIComponent(tenantId)}/handoff`,
        method: 'POST',
        body: { handoff },
        schema: tokenViewSchema,
      });
      await authenticateWithToken(token.accessToken, token.expiresIn);
    },
    [authenticateWithToken],
  );

  const logout = useCallback(async () => {
    try {
      await api.request({ path: '/v1/auth/logout', method: 'POST', schema: z.undefined() });
    } finally {
      setSession(null);
    }
  }, [api]);

  const value = useMemo(
    () => ({ api, authenticateWithToken, exchangeHandoff, logout, session }),
    [api, authenticateWithToken, exchangeHandoff, logout, session],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('AuthProvider is required');
  return value;
}
