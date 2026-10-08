import { useLayoutEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { ApiError } from '../../shared/api/errors';
import { useAuth } from './AuthProvider';

export function AuthCallbackPage() {
  const { exchangeHandoff } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [callback] = useState(() => {
    const params = new URLSearchParams(location.search);
    return callbackSchema.safeParse({
      tenantId: params.get('tenant'),
      handoff: params.get('handoff'),
    });
  });
  const [error, setError] = useState<string | null>(
    callback.success ? null : '登录回调无效，请重新登录',
  );

  useLayoutEffect(() => {
    window.history.replaceState(window.history.state, '', location.pathname);
    if (!callback.success) return;

    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted) return;

      void exchangeHandoff(callback.data.tenantId, callback.data.handoff, controller.signal)
        .then(() => {
          if (!controller.signal.aborted) navigate('/', { replace: true });
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(reason instanceof ApiError ? reason.message : '登录失败，请重新登录');
          }
        });
    });

    return () => controller.abort();
  }, [callback, exchangeHandoff, location.pathname, navigate]);

  if (error) {
    return (
      <main className="auth-page">
        <h1>无法完成登录</h1>
        <p role="alert">{error}</p>
      </main>
    );
  }

  return (
    <main className="auth-page" aria-busy="true">
      <h1>正在完成登录</h1>
      <p>请稍候</p>
    </main>
  );
}

const callbackSchema = z.object({
  tenantId: z.string().trim().min(1),
  handoff: z.string().trim().min(1),
});
