import { useEffect, useState } from 'react';
import {
  IconGridLine,
  IconHouseLine,
  IconMagnifyingglassLine,
  IconStarLine,
} from '@karrotmarket/react-monochrome-icon';
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
import { CatalystDetailPage } from '../pages/CatalystDetailPage';
import { HistoricalEventPage } from '../pages/HistoricalEventPage';
import { InsightsPage } from '../pages/InsightsPage';
import { LoginPage } from '../pages/LoginPage';
import { ResearchPage } from '../pages/ResearchPage';
import { SavedPage } from '../pages/SavedPage';
import { SimilarEventsPage } from '../pages/SimilarEventsPage';
import { ThemeDetailPage } from '../pages/ThemeDetailPage';
import { TodayPage } from '../pages/TodayPage';
import { ScrollMemory } from '../shared/ScrollMemory';
import { ErrorState, SplashScreen } from '../shared/StatePanel';
import { useAsyncResource } from '../shared/useAsyncResource';
import { RepositoryProvider, useRepository } from './RepositoryContext';

const navigation = [
  { to: '/today', label: '홈', Icon: IconHouseLine },
  { to: '/insights', label: '실시간', Icon: IconGridLine },
  { to: '/saved', label: '즐겨찾기', Icon: IconStarLine },
  { to: '/research', label: '테마 서치', Icon: IconMagnifyingglassLine },
] as const;

function AppShell() {
  return (
    <div className="app-shell">
      <ScrollMemory />
      <main className="app-content" id="main-content">
        <Outlet />
      </main>
      <nav className="bottom-navigation" aria-label="주요 메뉴">
        {navigation.map(({ to, label, Icon }) => (
          <NavLink key={to} to={to} className={({ isActive }) => (isActive ? 'is-active' : undefined)}>
            <Icon className="nav-icon" size={20} aria-hidden="true" />
            <small>{label}</small>
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
        <p>주소를 확인하거나 홈으로 돌아가 주세요.</p>
        <NavLink className="button button--secondary" to="/today">홈으로 이동</NavLink>
      </div>
    </div>
  );
}

function AuthenticatedRoutes({ onLogout }: { onLogout: () => Promise<void> }) {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/saved" element={<SavedPage onLogout={onLogout} />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/themes/:themeId/events/:eventId" element={<ThemeDetailPage />} />
        {/* 조건부 route. 게이트가 닫혀 있으면 각 화면이 제한 안내로 닫는다 (adaptation plan §5.2).
            production adapter는 게이트를 GATED로 고정하므로 배포에서는 진입점도 화면도 열리지 않는다. */}
        <Route path="/themes/:themeId/events/:eventId/similar" element={<SimilarEventsPage />} />
        <Route path="/events/:matchedEventId" element={<HistoricalEventPage />} />
        <Route path="/catalysts/:catalystId" element={<CatalystDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

/**
 * 첫 진입 스플래시 길이. 로고 sweep과 진행 바가 이 시간 동안 한 바퀴 돈다.
 * 시안은 4초였는데 그만큼 첫 화면이 늦어져 3초로 줄였다. CSS 애니메이션도 같은 값을 쓴다.
 */
const SPLASH_MS = 3000;

function AuthGate({ splashMs }: { splashMs: number }) {
  const repository = useRepository();
  const location = useLocation();
  const navigate = useNavigate();
  const session = useAsyncResource(() => repository.getSession(), [repository]);
  // 세션 확인이 먼저 끝나도 진행 바가 다 찰 때까지는 스플래시를 유지한다.
  const [splashHeld, setSplashHeld] = useState(splashMs > 0);

  useEffect(() => {
    if (splashMs <= 0) return undefined;
    const timer = window.setTimeout(() => setSplashHeld(false), splashMs);
    return () => window.clearTimeout(timer);
  }, [splashMs]);

  useEffect(
    () => repository.subscribe('session', session.retry),
    [repository, session.retry],
  );

  // 앱이 처음 열릴 때는 시안의 스플래시를 쓴다. 세션 확인은 이 뒤에서 끝난다.
  if (session.status === 'loading' || splashHeld) return <SplashScreen durationMs={splashMs || SPLASH_MS} />;

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
        returnTo={returnTo}
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
  // initialEntries는 MemoryRouter로 띄우는 경우(테스트·임베드)에만 온다. 그때는 장식용
  // 대기를 걸지 않는다. 실제 앱은 BrowserRouter로 뜨므로 시안과 같은 4초를 유지한다.
  const content = (
    <RepositoryProvider repository={repository}>
      <AuthGate splashMs={initialEntries ? 0 : SPLASH_MS} />
    </RepositoryProvider>
  );

  return initialEntries ? (
    <MemoryRouter initialEntries={initialEntries}>{content}</MemoryRouter>
  ) : (
    <BrowserRouter>{content}</BrowserRouter>
  );
}
