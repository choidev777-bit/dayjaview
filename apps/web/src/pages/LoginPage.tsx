import { useState } from 'react';

export function LoginPage({
  returnTo,
  onLogin,
}: {
  returnTo: string;
  onLogin: () => Promise<void>;
}) {
  const [status, setStatus] = useState<'idle' | 'pending' | 'error'>('idle');
  const savedIntent = returnTo.startsWith('/saved');

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
        <span className="login-mark" role="img" aria-label="DAYJAVIEW" />
        <div className="login-card__copy">
          <small>DAY JA VIEW</small>
          <h1 id="login-title">{savedIntent ? '저장한 분석을 확인하세요' : '오늘 강한 테마를 확인하세요'}</h1>
          <p>
            {savedIntent
              ? '저장해 둔 테마와 분석 기록을 이어서 살펴보세요.'
              : '로그인하면 오늘 강해지는 테마와 확인된 근거를 볼 수 있어요.'}
          </p>
        </div>
        <button
          className="button button--primary"
          type="button"
          onClick={handleLogin}
          disabled={status === 'pending'}
        >
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
