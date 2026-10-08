import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../shared/api/errors';
import { useAuth } from './AuthProvider';

export function AuthCallbackPage() {
  const { exchangeHandoff } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const started = useRef(false);
  const tenantId = params.get('tenant');
  const handoff = params.get('handoff');
  const invalidCallback = !tenantId || !handoff;
  const [error, setError] = useState<string | null>(
    invalidCallback ? '登录回调无效，请重新登录' : null,
  );

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!tenantId || !handoff) return;

    void exchangeHandoff(tenantId, handoff)
      .then(() => navigate('/', { replace: true }))
      .catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason.message : '登录失败，请重新登录');
      });
  }, [exchangeHandoff, handoff, navigate, tenantId]);

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
