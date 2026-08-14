import { useEffect } from 'react';
import {
  BrowserRouter,
  MemoryRouter,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import type { ProductRepository } from '../domain/contracts';
import { safeReturnTo } from '../domain/formatting';
import { HistoricalGatePage } from '../pages/HistoricalGatePage';
import { InsightsPage } from '../pages/InsightsPage';
import { LoginPage } from '../pages/LoginPage';
import { SavedPage } from '../pages/SavedPage';
import { ThemeDetailPage } from '../pages/ThemeDetailPage';
import { TodayPage } from '../pages/TodayPage';
import { ErrorState, LoadingState } from '../shared/StatePanel';
import { useAsyncResource } from '../shared/useAsyncResource';
import { RepositoryProvider, useRepository } from './RepositoryContext';

const navigation = [
  { to: '/today', label: '오늘', symbol: '●' },
  { to: '/insights', label: '인사이트', symbol: '◫' },
  { to: '/saved', label: '관심', symbol: '♡' },
] as const;

function AppShell({ onLogout }: { onLogout: () => Promise<void> }) {
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="DAYJAVIEW">
        <NavLink className="wordmark" to="/today" aria-label="DAYJAVIEW 오늘로 이동">
          <span className="brand-mark brand-mark--small" aria-hidden="true">D</span>
          <span>DAYJAVIEW</span>
        </NavLink>
        <nav className="primary-navigation" aria-label="데스크톱 주요 메뉴">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'is-active' : undefined)}>
              <span aria-hidden="true">{item.symbol}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <button className="logout-button" type="button" onClick={onLogout}>로그아웃</button>
      </aside>
      <main className="app-content" id="main-content">
        <Outlet />
      </main>
      <nav className="bottom-navigation" aria-label="모바일 주요 메뉴">
        {navigation.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'is-active' : undefined)}>
            <span aria-hidden="true">{item.symbol}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function NotFoundPage() {
  return (
    <div className="page page--gate">
      <div className="state-panel">
        <p className="state-panel__title">페이지를 찾을 수 없습니다</p>
        <p>주소를 확인하거나 오늘 화면으로 돌아가 주세요.</p>
        <NavLink className="button button--secondary" to="/today">오늘로 이동</NavLink>
      </div>
    </div>
  );
}

function AuthenticatedRoutes({ onLogout }: { onLogout: () => Promise<void> }) {
  return (
    <Routes>
      <Route element={<AppShell onLogout={onLogout} />}>
        <Route index element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/saved" element={<SavedPage />} />
        <Route path="/themes/:themeId/events/:eventId" element={<ThemeDetailPage />} />
        <Route path="/themes/:themeId/events/:eventId/similar" element={<HistoricalGatePage />} />
        <Route path="/events/:matchedEventId" element={<HistoricalGatePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

function AuthGate() {
  const repository = useRepository();
  const location = useLocation();
  const navigate = useNavigate();
  const session = useAsyncResource(() => repository.getSession(), [repository]);

  useEffect(
    () => repository.subscribe('session', session.retry),
    [repository, session.retry],
  );

  if (session.status === 'loading') {
    return (
      <main className="login-page">
        <LoadingState label="로그인 상태를 확인하는 중입니다" />
      </main>
    );
  }

  if (session.status === 'error') {
    return (
      <main className="login-page">
        <ErrorState error={session.error} retry={session.retry} />
      </main>
    );
  }

  const authenticated = session.data.authenticated;

  if (!authenticated) {
    const returnTo = safeReturnTo(`${location.pathname}${location.search}${location.hash}`);
    return (
      <LoginPage
        onLogin={async () => {
          const next = await repository.startGoogleLogin(returnTo);
          if (next.authenticated) {
            session.retry();
            navigate(returnTo, { replace: true });
          }
        }}
      />
    );
  }

  return (
    <AuthenticatedRoutes
      onLogout={async () => {
        await repository.logout();
        session.retry();
      }}
    />
  );
}

export function App({
  repository,
  initialEntries,
}: {
  repository: ProductRepository;
  initialEntries?: string[];
}) {
  const content = (
    <RepositoryProvider repository={repository}>
      <AuthGate />
    </RepositoryProvider>
  );

  return initialEntries ? (
    <MemoryRouter initialEntries={initialEntries}>{content}</MemoryRouter>
  ) : (
    <BrowserRouter>{content}</BrowserRouter>
  );
}
