import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { createApiClient, type ApiClient, type ApiRequest } from '../../shared/api/http';
import { getMe } from '../../shared/api/me';
import { tokenViewSchema, type Me } from '../../shared/api/schemas';

export interface AuthSession {
  token: string;
  me: Me;
  expiresAt: number;
}

interface AuthContextValue {
  api: ApiClient;
  authenticateWithToken(token: string, expiresIn: number, signal?: AbortSignal): Promise<void>;
  exchangeHandoff(tenantId: string, handoff: string, signal?: AbortSignal): Promise<void>;
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
  const sessionRef = useRef<AuthSession | null>(null);
  const recoveryRef = useRef<{ token: string; promise: Promise<void> } | null>(null);
  const queryClient = useQueryClient();

  const updateSession = useCallback((nextSession: AuthSession | null) => {
    sessionRef.current = nextSession;
    setSession(nextSession);
  }, []);

  const handleUnauthorized = useCallback(
    (requestToken: string | null) => {
      if (!requestToken || sessionRef.current?.token !== requestToken) return Promise.resolve();
      if (recoveryRef.current?.token === requestToken) return recoveryRef.current.promise;

      const recovery = Promise.resolve().then(async () => {
        try {
          await beforeSessionClear?.();
        } finally {
          if (sessionRef.current?.token === requestToken) {
            queryClient.clear();
            updateSession(null);
          }
        }
      });
      recoveryRef.current = { token: requestToken, promise: recovery };
      const clearRecovery = () => {
        if (recoveryRef.current?.promise === recovery) recoveryRef.current = null;
      };
      void recovery.then(clearRecovery, clearRecovery);
      return recovery;
    },
    [beforeSessionClear, queryClient, updateSession],
  );

  const api = useMemo(
    (): ApiClient => ({
      request: <T,>(request: ApiRequest<T>) => {
        const requestToken = session?.token ?? null;
        return createApiClient({
          getToken: () => requestToken,
          onUnauthorized: handleUnauthorized,
        }).request(request);
      },
    }),
    [handleUnauthorized, session?.token],
  );

  const authenticateWithToken = useCallback(
    async (token: string, expiresIn: number, signal?: AbortSignal) => {
      const tokenApi = createApiClient({
        getToken: () => token,
        onUnauthorized: handleUnauthorized,
      });
      const me = await getMe(tokenApi, signal);
      if (signal?.aborted) return;
      updateSession({ token, me, expiresAt: Date.now() + expiresIn * 1_000 });
    },
    [handleUnauthorized, updateSession],
  );

  const exchangeHandoff = useCallback(
    async (tenantId: string, handoff: string, signal?: AbortSignal) => {
      const anonymousApi = createApiClient();
      const token = await anonymousApi.request({
        path: `/v1/auth/org/${encodeURIComponent(tenantId)}/handoff`,
        method: 'POST',
        body: { handoff },
        schema: tokenViewSchema,
        signal,
      });
      if (signal?.aborted) return;
      await authenticateWithToken(token.accessToken, token.expiresIn, signal);
    },
    [authenticateWithToken],
  );

  const logout = useCallback(async () => {
    try {
      await api.request({ path: '/v1/auth/logout', method: 'POST', schema: z.undefined() });
    } finally {
      queryClient.clear();
      updateSession(null);
    }
  }, [api, queryClient, updateSession]);

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
