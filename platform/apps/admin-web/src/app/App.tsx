import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LogOut } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import { AuthProvider, useAuth } from '../features/auth/AuthProvider';
import { captureEditorRecovery } from '../features/editor/recovery';

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, staleTime: 30_000 },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider beforeSessionClear={captureEditorRecovery}>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

export function AppShell() {
  const { logout, session } = useAuth();

  return (
    <div className="app-shell">
      <header className="app-header">
        <strong>问卷管理台</strong>
        <div className="account-actions">
          <span>{session?.me.actorId}</span>
          <button type="button" title="退出登录" aria-label="退出登录" onClick={() => void logout()}>
            <LogOut aria-hidden="true" size={18} />
          </button>
        </div>
      </header>
      <main className="app-content">
        <Outlet />
      </main>
    </div>
  );
}
