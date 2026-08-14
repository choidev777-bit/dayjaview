import { useState } from 'react';

export function LoginPage({ onLogin }: { onLogin: () => Promise<void> }) {
  const [status, setStatus] = useState<'idle' | 'pending' | 'error'>('idle');

  async function handleLogin() {
    setStatus('pending');
    try {
      await onLogin();
    } catch {
      setStatus('error');
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-mark" aria-hidden="true">
          D
        </div>
        <p className="eyebrow">국내 주식 테마 분석</p>
        <h1 id="login-title">DAYJAVIEW</h1>
        <p className="login-card__intro">오늘 강해지는 테마와 확인된 근거를 한곳에서 살펴보세요.</p>
        <button
          className="button button--google"
          type="button"
          onClick={handleLogin}
          disabled={status === 'pending'}
        >
          <span aria-hidden="true">G</span>
          {status === 'pending' ? 'Google 로그인 연결 중' : 'Google로 계속하기'}
        </button>
        {status === 'error' ? (
          <p className="login-card__error" role="alert">
            로그인하지 못했습니다. 잠시 후 다시 시도해 주세요.
          </p>
        ) : null}
        <p className="login-card__notice">로그인 후에만 제품 데이터를 제공합니다.</p>
      </section>
    </main>
  );
}
