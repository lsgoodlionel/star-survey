import { LogIn } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthProvider';

interface DevTokenForm {
  token: string;
}

export function DevTokenPage({ mode = 'development' }: { mode?: 'development' | 'e2e' }) {
  const { authenticateWithToken } = useAuth();
  const navigate = useNavigate();
  const { formState, handleSubmit, register, setError } = useForm<DevTokenForm>();

  const submit = handleSubmit(async ({ token }) => {
    try {
      await authenticateWithToken(token.trim(), 600);
      navigate('/', { replace: true });
    } catch {
      setError('token', { message: '令牌无效或服务不可用' });
    }
  });

  return (
    <main className="auth-page">
      <form onSubmit={(event) => void submit(event)}>
        <h1>{mode === 'e2e' ? '端到端测试登录' : '开发环境登录'}</h1>
        <label htmlFor="dev-token">{mode === 'e2e' ? '测试令牌' : '开发令牌'}</label>
        <textarea
          id="dev-token"
          aria-describedby={formState.errors.token ? 'dev-token-error' : undefined}
          autoComplete="off"
          rows={5}
          {...register('token', { required: '请输入开发令牌' })}
        />
        {formState.errors.token ? (
          <p id="dev-token-error" role="alert">
            {formState.errors.token.message}
          </p>
        ) : null}
        <button type="submit" disabled={formState.isSubmitting}>
          <LogIn aria-hidden="true" size={18} />
          登录
        </button>
      </form>
    </main>
  );
}
