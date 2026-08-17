import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository, type RankingFixture } from '../adapters/fixtureRepository';
import { RepositoryError } from '../domain/repositoryErrors';
import { DataStatusBar } from '../shared/DataStatusBar';

describe('시장 데이터 상태 matrix', () => {
  it.each<[RankingFixture, string]>([
    ['live', '실시간'],
    ['delayed', '수신 지연'],
    ['degraded', '일부 데이터 지연'],
    ['closed', '장 마감'],
    // 홈은 순위만 보여주기로 해서 상태 바가 빠졌다. 상태 표현 자체는 화면이 아니라
    // 계약 fixture의 marketContext를 기준으로 확인한다.
  ])('%s 계약 fixture를 %s 상태로 표현한다', async (ranking, label) => {
    const { meta } = await createFixtureRepository({ ranking }).getRankings();
    render(<DataStatusBar context={meta.marketContext} />);

    expect(screen.getByText(label, { selector: '.data-status strong' })).toBeInTheDocument();
  });

  it('Coverage 미달 metric을 0%로 바꾸지 않고 계산 불가로 표시한다', async () => {
    render(<App repository={createFixtureRepository({ ranking: 'unavailable' })} initialEntries={['/today']} />);

    expect(await screen.findByText('데이터 갱신 중')).toBeInTheDocument();
    expect(screen.getByText('현재 0 / 0종목 반영')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument();
  });

  it('서버의 실제 0은 null과 구분해 0.0%로 표시한다', async () => {
    const fixture = createFixtureRepository();
    const repository = {
      ...fixture,
      async getRankings() {
        const response = await fixture.getRankings();
        const actualZero = structuredClone(response);
        actualZero.data.items[0].weightedReturn = 0;
        return actualZero;
      },
    };
    render(<App repository={repository} initialEntries={['/today']} />);

    expect(await screen.findByText('0.0%')).toBeInTheDocument();
    expect(screen.queryByText('—')).not.toBeInTheDocument();
  });

  it('오늘 empty 상태를 한국어 다음 행동 문구로 표현한다', async () => {
    render(<App repository={createFixtureRepository({ ranking: 'empty' })} initialEntries={['/today']} />);

    expect(await screen.findByText('현재 조건을 충족한 테마가 없습니다')).toBeInTheDocument();
  });

  it('retry 가능한 contract 오류를 전체 error 상태로 표현한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ failures: ['rankings'] })}
        initialEntries={['/today']}
      />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('데이터를 불러오지 못했습니다');
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument();
  });

  it('느린 응답 동안 화면별 loading 상태를 유지한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ latencyMs: 40 })}
        initialEntries={['/today']}
      />,
    );

    expect(await screen.findByText('오늘의 테마를 불러오는 중입니다', {}, { timeout: 300 })).toBeInTheDocument();
    expect(await screen.findByText('원전수출')).toBeInTheDocument();
  });
});

describe('근거 상태 matrix', () => {
  it('검색 중에는 근거 없는 상승 이유를 만들지 않는다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'searching', evidence: 'searching' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    expect((await screen.findAllByText('상승 이유 확인 중')).length).toBeGreaterThan(0);
    expect(
      await screen.findByText('확인된 근거가 생기기 전에는 상승 이유를 만들지 않습니다.'),
    ).toBeInTheDocument();
  });

  it('확인된 신규 소재 없음과 source 장애를 검색 중 상태와 구분한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'searching', evidence: 'none' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    expect((await screen.findAllByText('확인된 신규 소재 없음')).length).toBeGreaterThan(0);
  });

  it('뉴스 수집 지연을 확인된 신규 소재 없음과 구분해 안내한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'searching', evidence: 'degraded' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    expect(await screen.findByText(/뉴스 수집이 지연되고 있습니다/)).toHaveTextContent(
      '마지막 정상 수집 10:58',
    );
    expect(
      screen.getByText('수집이 지연되는 동안에는 확인된 신규 소재가 없다고 단정하지 않습니다.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('확인된 신규 소재 없음')).not.toBeInTheDocument();
  });

  it('출처마다 매체·발행 시각·원문 링크·연결 기준을 제공한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'single', evidence: 'single' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    // 매체·시각·원문은 기사 제목 옆 물음표 안에 접혀 있다 (screen_spec 8.3의 `영역 선택`).
    const tip = await screen.findByRole('button', { name: /출처 정보/ });
    expect(screen.queryByRole('link', { name: /새 창에서 원문 보기/ })).not.toBeInTheDocument();

    await userEvent.click(tip);

    const link = screen.getByRole('link', { name: /새 창에서 원문 보기/ });
    expect(link).toHaveAttribute('href', 'https://example.com/news/123');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
    expect(screen.getByText(/예시 언론사 · 10:17/)).toBeInTheDocument();
  });

  it('장후 확정 근거에 장중 이력 보존을 안내한다', async () => {
    const fixture = createFixtureRepository({ detail: 'closed', evidence: 'multi' });
    const repository = {
      ...fixture,
      async getEvidence(eventId: string) {
        const response = await fixture.getEvidence(eventId);
        const confirmed = structuredClone(response);
        confirmed.data.evidenceStatus = 'AFTER_CLOSE_CONFIRMED';
        return confirmed;
      },
    };
    render(<App repository={repository} initialEntries={['/themes/thm_nuclear/events/evt_current']} />);

    // 이력 보존은 탭으로 안내한다. 상태 설명은 기사 옆 물음표 안에 접혀 있다.
    await userEvent.click((await screen.findAllByRole('button', { name: /출처 정보/ }))[0]);
    expect(screen.getByText(/장 마감 후 인포스탁 기준으로 확정된 사유입니다\./)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '장중 분석 이력' })).toBeInTheDocument();
  });

  it('복수 근거 확정과 출처를 함께 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'multi', evidence: 'multi' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    // 근거 목록이 먼저 그려지고, 상태와 출처 수는 제목 옆 물음표 안에 함께 있다.
    expect(await screen.findAllByRole('button', { name: /출처 정보/ })).not.toHaveLength(0);

    await userEvent.click((await screen.findAllByRole('button', { name: /출처 정보/ }))[1]);
    expect(screen.getByText(/복수 뉴스 확인/)).toBeInTheDocument();
    expect(screen.getByText('두번째 예시 언론사', { exact: false })).toBeInTheDocument();
  });

  it('장후 확정 상태를 event lifecycle과 별도 문구로 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'closed', evidence: 'multi' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    // 사건 수명 상태(`장후 확정`)와 근거 상태는 서로 다른 자리에 다른 문구로 적는다.
    expect(await screen.findByText('장후 확정', { selector: '.status-chip' })).toBeInTheDocument();

    await userEvent.click((await screen.findAllByRole('button', { name: /출처 정보/ }))[0]);
    expect(screen.getByText(/복수 뉴스 확인/)).toBeInTheDocument();
    expect(screen.queryByText('장후 확정', { selector: '.info-tip__panel b' })).not.toBeInTheDocument();
  });

  it('검색 중에는 저장된 요약 문장이 있어도 상승 이유를 만들지 않는다', async () => {
    const fixture = createFixtureRepository({ detail: 'searching', evidence: 'searching' });
    const repository = {
      ...fixture,
      async getThemeDetail(themeId: string, eventId: string) {
        const response = await fixture.getThemeDetail(themeId, eventId);
        const injected = structuredClone(response);
        injected.data.evidenceSummary.summary = '근거 없이 남아 있던 이유 문장';
        return injected;
      },
    };
    render(<App repository={repository} initialEntries={['/themes/thm_nuclear/events/evt_current']} />);

    expect((await screen.findAllByText('상승 이유 확인 중')).length).toBeGreaterThan(0);
    expect(screen.queryByText('근거 없이 남아 있던 이유 문장')).not.toBeInTheDocument();
  });

  it('기사별 품질 flag와 발행 시각 미확인을 문구로 표시한다', async () => {
    const fixture = createFixtureRepository({ detail: 'single', evidence: 'single' });
    const repository = {
      ...fixture,
      async getEvidence(eventId: string) {
        const response = await fixture.getEvidence(eventId);
        const flagged = structuredClone(response);
        flagged.data.items[0].publishedAt = null;
        flagged.data.items[0].qualityFlags = ['PUBLISHED_AT_MISSING', 'RIGHTS_LIMITED'];
        return flagged;
      },
    };
    render(<App repository={repository} initialEntries={['/themes/thm_nuclear/events/evt_current']} />);

    expect(await screen.findByText('발행 시각 미확인')).toBeInTheDocument();
    expect(screen.getByText('원문 제공 범위 제한')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /출처 정보/ }));
    expect(screen.getByText(/발행 시각 미확인 · 수집 10:17/)).toBeInTheDocument();
  });

  it('다음 page cursor가 있으면 이전 근거를 이어서 불러온다', async () => {
    const user = userEvent.setup();
    const fixture = createFixtureRepository({ detail: 'multi', evidence: 'multi' });
    const repository = {
      ...fixture,
      async getEvidence(eventId: string, cursor?: string | null) {
        const response = await fixture.getEvidence(eventId);
        const paged = structuredClone(response);
        if (!cursor) {
          paged.data.page = { nextCursor: 'news_2', hasMore: true, limit: 20 };
          return paged;
        }
        const older = paged.data.items[0];
        older.newsId = 'news_3';
        older.title = '이전 시간대 보도';
        paged.data.items = [older];
        paged.data.page = { nextCursor: null, hasMore: false, limit: 20 };
        return paged;
      },
    };
    render(<App repository={repository} initialEntries={['/themes/thm_nuclear/events/evt_current']} />);

    expect(await screen.findByText('체코 신규 원전 관련 보도')).toBeInTheDocument();
    expect(screen.queryByText('이전 시간대 보도')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '이전 근거 더 불러오기' }));

    expect(await screen.findByText('이전 시간대 보도')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '이전 근거 더 불러오기' })).not.toBeInTheDocument();
  });

  it('장후 확정으로 바뀌면 장중에 표시했던 근거를 이력 탭으로 남긴다', async () => {
    const user = userEvent.setup();
    const fixture = createFixtureRepository({ detail: 'multi', evidence: 'multi' });
    const listeners = new Set<() => void>();
    let afterClose = false;
    const repository = {
      ...fixture,
      subscribe(resource: Parameters<typeof fixture.subscribe>[0], listener: () => void) {
        if (resource !== 'evidence') return fixture.subscribe(resource, listener);
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      async getEvidence(eventId: string) {
        const response = await fixture.getEvidence(eventId);
        if (!afterClose) return response;
        const confirmed = structuredClone(response);
        confirmed.data.evidenceStatus = 'AFTER_CLOSE_CONFIRMED';
        confirmed.data.items = confirmed.data.items.slice(0, 1);
        return confirmed;
      },
    };
    render(<App repository={repository} initialEntries={['/themes/thm_nuclear/events/evt_current']} />);

    expect(await screen.findByText('신규 원전 협상 진전 보도')).toBeInTheDocument();

    afterClose = true;
    await act(async () => {
      listeners.forEach((listener) => listener());
    });

    expect(
      await screen.findByText('장중에 표시했던 내용과 달라졌습니다. 확정 사유를 기준으로 안내합니다.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('신규 원전 협상 진전 보도')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '장중 분석 이력' }));

    expect(await screen.findByText('신규 원전 협상 진전 보도')).toBeInTheDocument();
    expect(
      screen.getByText('장중에 표시했던 내용이며 현재 기준은 장 마감 후 분석입니다.'),
    ).toBeInTheDocument();
  });
});

describe('인사이트와 권한 상태', () => {
  it('403 권한 거부를 일반 데이터 오류와 구분해 한국어로 안내한다', async () => {
    const fixture = createFixtureRepository();
    const repository = {
      ...fixture,
      async getSaved() {
        throw new RepositoryError({
          kind: 'permission',
          status: 403,
          code: 'FEATURE_NOT_ENTITLED',
          message: '권한이 없습니다.',
        });
      },
    };
    render(<App repository={repository} initialEntries={['/saved']} />);

    expect(await screen.findByText('접근 권한이 없습니다')).toBeInTheDocument();
    expect(screen.getByText('현재 계정으로는 이 데이터에 접근할 수 없습니다.')).toBeInTheDocument();
  });

  it('Coverage 미달 테마는 0% 타일 없이 empty 상태로 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ treemap: 'excluded' })}
        initialEntries={['/insights']}
      />,
    );

    expect(await screen.findByText('현재 조건을 충족한 테마가 없습니다')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /테마 수익률/ })).not.toBeInTheDocument();
  });

  it('권한 제한 저장 Event에 상세 진입점을 만들지 않는다', async () => {
    render(
      <App
        repository={createFixtureRepository({ saved: 'unavailable' })}
        initialEntries={['/saved?type=EVENT']}
      />,
    );

    expect(await screen.findByText('현재 확인할 수 없음')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '상세 보기' })).not.toBeInTheDocument();
  });
});
