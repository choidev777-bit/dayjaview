import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository } from '../adapters/fixtureRepository';
import { safeReturnTo } from '../domain/formatting';

describe('인증과 route shell', () => {
  it('비로그인 사용자는 제품 데이터 없이 로그인 화면만 본다', async () => {
    render(<App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/today']} />);

    expect(
      await screen.findByRole('heading', { name: '오늘 강한 테마를 확인하세요' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Google로 계속하기' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    expect(screen.queryByText('원전수출')).not.toBeInTheDocument();
  });

  it('fixture Google 로그인 뒤 원래의 안전한 내부 route로 돌아간다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository({ authenticated: false })} initialEntries={['/saved']} />);

    expect(
      await screen.findByRole('heading', { name: '저장한 분석을 확인하세요' }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Google로 계속하기' }));

    expect(await screen.findByRole('heading', { name: '저장' })).toBeInTheDocument();
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
    // 출처는 제목 아래 옅은 한 줄로 있다.
    expect(await screen.findByRole('link', { name: /예시 언론사/ })).toBeInTheDocument();
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

  it('gate가 열리면 기간별 유효 분모를 따로 표시하고 결측을 0%로 적지 않는다', async () => {
    render(
      <App
        repository={createFixtureRepository({ similar: 'available' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current/similar']}
      />,
    );

    // 기간마다 분모가 다르다(14·14·12). 한 분모로 합치면 이 표기가 무너진다 (screen_spec 8.8).
    expect(await screen.findByRole('heading', { name: /비슷했던 과거 14건/ })).toBeInTheDocument();
    expect(screen.getByText('10 / 14건 상승')).toBeInTheDocument();
    expect(screen.getByText('6 / 12건 상승')).toBeInTheDocument();
    // fixture 사례에는 T+5 결과가 없다. 0%가 아니라 결측으로 적어야 한다.
    expect(screen.getByText('기록 없음')).toBeInTheDocument();
    expect(screen.queryByText(/확률|적중률|성공률/)).not.toBeInTheDocument();
  });

  it('과거 이벤트 상세는 당시 주도 종목과 선택 배제 고지를 함께 보여준다', async () => {
    render(
      <App repository={createFixtureRepository()} initialEntries={['/events/evt_historical']} />,
    );

    // 제목 자리는 `어느 테마에서 열었는가`가 차지하고, 사건 사유는 그 아래 문단이다.
    expect(await screen.findByText('마이크로 LED 양산 발표')).toBeInTheDocument();
    expect(screen.getByText('과거 예시 종목')).toBeInTheDocument();
    expect(screen.getByText(/미래 결과는 유사사례를 고를 때 사용하지 않았습니다/)).toBeInTheDocument();
  });

  it('일반 사용자 router와 navigation에 operator surface가 없다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/operator']} />);

    expect(await screen.findByText('페이지를 찾을 수 없습니다')).toBeInTheDocument();
    expect(screen.queryByText(/운영자/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /operator/i })).not.toBeInTheDocument();
  });
});

describe('하단 탭과 즐겨찾기 route shell', () => {
  it('하단 탭은 시안 구성 4개를 유지하고 데스크톱 사이드바를 두지 않는다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/today']} />);

    const navigation = await screen.findByRole('navigation', { name: '주요 메뉴' });
    expect(within(navigation).getAllByRole('link').map((link) => link.textContent)).toEqual([
      '홈',
      '실시간',
      '즐겨찾기',
      '리서치',
    ]);
    expect(screen.getAllByRole('navigation')).toHaveLength(1);
  });

  it('리서치 탭은 화면 자리만 두고 답변을 만들지 않는다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/research']} />);

    expect(await screen.findByRole('heading', { name: '무엇이 궁금하세요?' })).toBeInTheDocument();
    expect(screen.getByText('준비 중인 화면이에요')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('발행 전 날짜를 열면 대체 사실을 알리고 직전 거래일 결과를 보여준다', async () => {
    render(<App repository={createFixtureRepository()} initialEntries={['/movers']} />);

    expect(
      await screen.findByRole('heading', { name: '이날 뭐가 움직였나요?' }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/특징테마가 발행되지 않았어요/)).toBeInTheDocument();
    expect(screen.getByText(/직전 거래일/)).toBeInTheDocument();
    expect(screen.getByText('+3.26%')).toBeInTheDocument();
  });

  it('발행된 날짜는 원문 문단과 상승·하락을 나눠 보여준다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository()} initialEntries={['/movers']} />);

    const input = await screen.findByLabelText('날짜');
    await user.clear(input);
    await user.type(input, '2026-06-29');

    expect(await screen.findByText('오른 테마')).toBeInTheDocument();
    expect(screen.getByText('빠진 테마')).toBeInTheDocument();
    expect(
      screen.getByText(/중국 업체들의 저가 공세와 전기차 수요 축소/),
    ).toBeInTheDocument();
    expect(screen.getByText('+21.00%')).toBeInTheDocument();
    expect(screen.getByText('-3.26%')).toBeInTheDocument();
    expect(screen.getByText('34,000원')).toBeInTheDocument();
    expect(screen.queryByText(/발행되지 않았어요/)).not.toBeInTheDocument();
  });

  it('홈 순위 휠은 목록 의미와 방향키 이동을 제공한다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository()} initialEntries={['/today']} />);

    const wheel = await screen.findByRole('list', { name: /오늘 많이 오른 테마 순위, 총 1개/ });
    const card = within(wheel).getByRole('link', { name: /1위 원전수출/ });
    card.focus();
    await user.keyboard('{ArrowDown}');

    expect(card).toHaveFocus();
  });
});

describe('관심 route shell', () => {
  it('테마 상세에서 저장을 추가하고 같은 fixture 저장 상태로 동기화한다', async () => {
    const user = userEvent.setup();
    render(
      <App
        repository={createFixtureRepository({ saved: 'library' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    await user.click(await screen.findByRole('button', { name: '관심에 저장' }));
    expect(await screen.findByRole('button', { name: '관심에서 저장 해제' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('계약 fixture의 저장 목록과 접근 제한 항목을 함께 구분한다', async () => {
    render(<App repository={createFixtureRepository({ saved: 'mixed' })} initialEntries={['/saved']} />);

    expect(await screen.findByText('스페이스X(SpaceX)')).toBeInTheDocument();
    expect(screen.getByText('접근 제한 과거 이벤트')).toBeInTheDocument();
    expect(screen.getByText('현재 확인할 수 없음')).toBeInTheDocument();
    // 카드 전체가 이동 링크다. 열 수 있는 항목만 링크가 되고 접근 제한 항목은 링크가 아니다.
    expect(screen.getByRole('link', { name: /스페이스X/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /접근 제한 과거 이벤트/ })).not.toBeInTheDocument();
  });

  it('필터 tablist는 방향키로 이동하고 URL 상태에 맞춰 빈 상태를 표시한다', async () => {
    const user = userEvent.setup();
    // 이벤트만 저장된 fixture라 `테마` 탭이 빈 상태가 된다.
    render(<App repository={createFixtureRepository({ saved: 'unavailable' })} initialEntries={['/saved']} />);

    const allTab = await screen.findByRole('tab', { name: '전체' });
    allTab.focus();
    await user.keyboard('{ArrowRight}');

    await waitFor(() => expect(screen.getByRole('tab', { name: '테마' })).toHaveAttribute('aria-selected', 'true'));
    expect(await screen.findByText('저장한 항목이 없습니다')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '테마' })).toHaveFocus();

    // 종목 저장은 없앴다. 테마 다음은 이벤트다.
    await user.keyboard('{ArrowRight}');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: '이벤트' })).toHaveAttribute('aria-selected', 'true'),
    );
    expect(screen.queryByRole('tab', { name: '종목' })).not.toBeInTheDocument();
  });

  it('저장 해제는 fixture adapter 상태를 갱신한다', async () => {
    const user = userEvent.setup();
    render(<App repository={createFixtureRepository({ saved: 'library' })} initialEntries={['/saved']} />);

    await user.click(await screen.findByRole('button', { name: /저장 해제/ }));

    expect(await screen.findByText('저장한 항목이 없습니다')).toBeInTheDocument();
  });
});
