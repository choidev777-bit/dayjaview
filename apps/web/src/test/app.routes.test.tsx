import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository } from '../adapters/fixtureRepository';
import { safeReturnTo } from '../domain/formatting';

describe('인증과 route shell', () => {
  it('비로그인 사용자는 제품 데이터 없이 로그인 화면만 본다', async () => {
    render(<App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/today']} />);

    expect(await screen.findByRole('heading', { name: 'DAYJAVIEW' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Google로 계속하기' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByText('원전수출')).not.toBeInTheDocument();
  });

  it('fixture Google 로그인 뒤 원래의 안전한 내부 route로 돌아간다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/saved']} />);

    await user.click(await screen.findByRole('button', { name: 'Google로 계속하기' }));

    expect(await screen.findByRole('heading', { name: '관심' })).toBeInTheDocument();
  });

  it.each([
    ['https://attacker.example/path', '/today'],
    ['//attacker.example/path', '/today'],
    ['\\\\attacker.example', '/today'],
    ['/themes/thm_nuclear/events/evt_current?tab=summary', '/themes/thm_nuclear/events/evt_current?tab=summary'],
  ])('returnTo %s를 안전하게 정규화한다', (input, expected) => {
    expect(safeReturnTo(input)).toBe(expected);
  });

  it('오늘 카드의 Space 키 선택으로 동일 themeId·eventId 상세에 이동한다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository()} initialEntries={['/today']} />);

    const card = await screen.findByRole('link', { name: /1위 원전수출/ });
    card.focus();
    await user.keyboard(' ');

    expect(await screen.findByRole('heading', { level: 1, name: '원전수출' })).toBeInTheDocument();
    expect(screen.getByText(/뉴스 기반 추정/, { selector: '.section-note' })).toBeInTheDocument();
  });

  it('인사이트 타일은 접근 가능한 이름과 canonical 상세 이동을 제공한다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/insights']} />);

    const tile = await screen.findByRole('link', { name: /원전수출, 테마 수익률 \+2\.7%/ });
    expect(tile).toHaveAttribute('href', '/themes/thm_nuclear/events/evt_current');
  });

  it('역사 gate가 닫히면 직접 route에서도 과거 데이터 대신 gated 상태만 보인다', async () => {
    render(
      <App
        repository={createFixtureRepository()}
        initialEntries={['/themes/thm_nuclear/events/evt_current/similar']}
      />,
    );

    expect(await screen.findByText('이 기능은 아직 제공되지 않습니다')).toBeInTheDocument();
    expect(screen.queryByText('마이크로 LED 양산 발표')).not.toBeInTheDocument();
    expect(screen.queryByText(/14건 중/)).not.toBeInTheDocument();
  });

  it('일반 사용자 router와 navigation에 operator surface가 없다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/operator']} />);

    expect(await screen.findByText('페이지를 찾을 수 없습니다')).toBeInTheDocument();
    expect(screen.queryByText(/운영자/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /operator/i })).not.toBeInTheDocument();
  });
});

describe('관심 route shell', () => {
  it('계약 fixture의 저장 목록과 접근 제한 항목을 함께 구분한다', async () => {
    render(<App repository={createFixtureRepository({ saved: 'mixed' })} initialEntries={['/saved']} />);

    expect(await screen.findByText('스페이스X(SpaceX)')).toBeInTheDocument();
    expect(screen.getByText('접근 제한 과거 이벤트')).toBeInTheDocument();
    expect(screen.getByText('현재 확인할 수 없음')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '상세 보기' })).toBeInTheDocument();
  });

  it('필터 tablist는 방향키로 이동하고 URL 상태에 맞춰 빈 상태를 표시한다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository()} initialEntries={['/saved']} />);

    const allTab = await screen.findByRole('tab', { name: '전체' });
    allTab.focus();
    await user.keyboard('{ArrowRight}');

    await waitFor(() => expect(screen.getByRole('tab', { name: '테마' })).toHaveAttribute('aria-selected', 'true'));
    await user.keyboard('{ArrowRight}');
    expect(await screen.findByText('저장한 항목이 없습니다')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '종목' })).toHaveFocus();
  });

  it('저장 해제는 fixture adapter 상태를 갱신한다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository({ saved: 'library' })} initialEntries={['/saved']} />);

    await user.click(await screen.findByRole('button', { name: '저장 해제' }));

    expect(await screen.findByText('저장한 항목이 없습니다')).toBeInTheDocument();
  });
});
