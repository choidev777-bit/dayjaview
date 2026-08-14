import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { createFixtureRepository, type RankingFixture } from '../adapters/fixtureRepository';
import { RepositoryError } from '../domain/repositoryErrors';

describe('시장 데이터 상태 matrix', () => {
  it.each<[RankingFixture, string]>([
    ['live', '실시간'],
    ['delayed', '수신 지연'],
    ['degraded', '일부 데이터 지연'],
    ['closed', '장 마감'],
  ])('%s 계약 fixture를 %s 상태로 표현한다', async (ranking, label) => {
    render(<App repository={createFixtureRepository({ ranking })} initialEntries={['/today']} />);

    expect(await screen.findByText(label, { selector: '.data-status strong' })).toBeInTheDocument();
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

  it('복수 근거 확정과 출처를 함께 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'multi', evidence: 'multi' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    expect((await screen.findAllByText('복수 뉴스 확인')).length).toBeGreaterThan(0);
    expect(screen.getByText('두번째 예시 언론사', { exact: false })).toBeInTheDocument();
  });

  it('장후 확정 상태를 event lifecycle과 별도 문구로 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'closed', evidence: 'multi' })}
        initialEntries={['/themes/thm_nuclear/events/evt_current']}
      />,
    );

    expect((await screen.findAllByText(/인포스탁 기준 확정/)).length).toBeGreaterThan(0);
    expect(screen.getByText('장후 확정', { selector: '.status-chip' })).toBeInTheDocument();
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
