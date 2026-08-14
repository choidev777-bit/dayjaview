import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFixtureRepository, type DetailFixture, type EvidenceFixture } from '../adapters/fixtureRepository';
import { App } from '../app/App';

const detailPath = '/themes/thm_nuclear/events/evt_current';

describe('근거 lifecycle UI', () => {
  it.each<[DetailFixture, EvidenceFixture, string]>([
    ['searching', 'searching', '상승 이유 확인 중'],
    ['single', 'single', '뉴스 기반 추정'],
    ['multi', 'multi', '복수 뉴스 확인'],
    ['none', 'none', '확인된 신규 소재 없음'],
    ['reemergence', 'reemergence', '기존 소재 재부각'],
    ['closed', 'afterClose', '인포스탁 기준 확정'],
  ])('%s/%s fixture를 %s 상태로 투영한다', async (detail, evidence, label) => {
    render(
      <App
        repository={createFixtureRepository({ detail, evidence })}
        initialEntries={[detailPath]}
      />,
    );

    const section = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect(await within(section).findByText(label, { selector: '.status-chip' })).toBeInTheDocument();
  });

  it('근거 0건이면 contract에 없는 원인 문장을 표시하지 않는다', async () => {
    const fixture = createFixtureRepository({ detail: 'searching', evidence: 'searching' });
    const repository = {
      ...fixture,
      async getThemeDetail(themeId: string, eventId: string) {
        const response = await fixture.getThemeDetail(themeId, eventId);
        response.data.evidenceSummary.summary = '근거 없이 주가만 보고 만든 원인';
        response.data.evidenceSummary.sourceCount = 0;
        return response;
      },
    };

    render(<App repository={repository} initialEntries={[detailPath]} />);

    const section = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect(within(section).getByText('상승 이유 확인 중', { selector: '.state-panel__title' })).toBeInTheDocument();
    expect(screen.queryByText('근거 없이 주가만 보고 만든 원인')).not.toBeInTheDocument();
    expect(screen.queryByText(/LLM|provider/i)).not.toBeInTheDocument();
  });

  it('source title·매체·발행/수신 시각·상태와 안전한 원문 링크를 함께 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'single', evidence: 'single' })}
        initialEntries={[detailPath]}
      />,
    );

    const section = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect(within(section).getByRole('heading', { name: '체코 신규 원전 관련 보도' })).toBeInTheDocument();
    expect(within(section).getByText('예시 언론사')).toBeInTheDocument();
    expect(within(section).getByText('정상 수신')).toBeInTheDocument();
    expect(within(section).getByText('DAYJAVIEW 요약 · 원문이 아닙니다')).toBeInTheDocument();
    expect(within(section).getByText('테마 · 종목 · 시각')).toBeInTheDocument();
    expect(within(section).getByText(/목록 생성 2026\.08\.14 10:18/)).toBeInTheDocument();

    const link = within(section).getByRole('link', {
      name: '예시 언론사 원문 보기: 체코 신규 원전 관련 보도',
    });
    expect(link).toHaveAttribute('href', 'https://example.com/news/123');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
  });

  it('안전하지 않은 URL과 미확인 발행 시각을 원문 링크나 추정 시각으로 만들지 않는다', async () => {
    const fixture = createFixtureRepository({ detail: 'single', evidence: 'single' });
    const repository = {
      ...fixture,
      async getEvidence(eventId: string) {
        const response = await fixture.getEvidence(eventId);
        response.data.items[0].originalUrl = 'javascript:alert(1)';
        response.data.items[0].publishedAt = null;
        response.data.items[0].qualityFlags = ['PUBLISHED_TIME_UNKNOWN'];
        return response;
      },
    };

    render(<App repository={repository} initialEntries={[detailPath]} />);

    const section = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect((await within(section).findAllByText('발행 시각 미확인')).length).toBeGreaterThan(0);
    expect(within(section).getByText('원문 링크 제공 안 됨')).toBeInTheDocument();
    expect(within(section).queryByRole('link', { name: /원문 보기/ })).not.toBeInTheDocument();
  });

  it('장후 확정에서 lifecycle, evidence status, 분류 revision과 시점을 분리한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'closed', evidence: 'afterClose' })}
        initialEntries={[detailPath]}
      />,
    );

    expect(await screen.findByText('장후 확정', { selector: '.detail-hero .status-chip' })).toBeInTheDocument();
    expect(screen.getByText(/분류 revision 2 · 변경 2026\.08\.14 16:12/)).toBeInTheDocument();
    const section = screen.getByRole('region', { name: '확인된 기사 근거' });
    expect(await within(section).findByText('인포스탁 기준 확정', { selector: '.status-chip' })).toBeInTheDocument();
    expect(within(section).getByText(/목록 생성 2026\.08\.14 16:12/)).toBeInTheDocument();
  });
});

describe('근거 제공 품질·예외 UI', () => {
  it('느린 evidence 응답을 전용 loading 상태로 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ latencyMs: 40 })}
        initialEntries={[detailPath]}
      />,
    );

    expect(await screen.findByText('기사 근거를 확인하는 중입니다', {}, { timeout: 500 })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: /예시 언론사 원문 보기/ })).toBeInTheDocument();
  });

  it('DELAYED는 마지막 정상 시각과 함께 기존 출처를 표시한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'single', evidence: 'delayed' })}
        initialEntries={[detailPath]}
      />,
    );

    const notice = await screen.findByRole('status', { name: '근거 데이터 제공 상태' });
    expect(notice).toHaveTextContent('근거 데이터 수신 지연');
    expect(notice).toHaveTextContent('마지막 정상 2026.08.14 10:18');
    expect(screen.getByRole('link', { name: /예시 언론사 원문 보기/ })).toBeInTheDocument();
  });

  it('DEGRADED와 STALE_NEWS_DATA를 no-evidence 상태와 함께 구분한다', async () => {
    render(
      <App
        repository={createFixtureRepository({ detail: 'searching', evidence: 'degraded' })}
        initialEntries={[detailPath]}
      />,
    );

    const notice = await screen.findByRole('status', { name: '근거 데이터 제공 상태' });
    expect(notice).toHaveTextContent('일부 출처 수집 지연');
    expect(notice).toHaveTextContent('근거 데이터가 오래되었습니다');
    expect(screen.getByText('수집 상태가 정상화되기 전에는 상승 이유를 확정하거나 만들지 않습니다.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /원문 보기/ })).not.toBeInTheDocument();
  });

  it('출처 배열이 비어 있으면 확인 상태와 별개로 빈 출처를 사실대로 표시한다', async () => {
    const fixture = createFixtureRepository({ detail: 'single', evidence: 'single' });
    const repository = {
      ...fixture,
      async getEvidence(eventId: string) {
        const response = await fixture.getEvidence(eventId);
        response.data.items = [];
        return response;
      },
    };

    render(<App repository={repository} initialEntries={[detailPath]} />);

    expect(await screen.findByText('표시 가능한 원문 출처가 없습니다. 출처 데이터가 없으면 상승 이유를 새로 만들지 않습니다.')).toBeInTheDocument();
  });

  it('evidence 장애와 permission을 서로 다른 전용 오류 상태로 표시한다', async () => {
    const failed = render(
      <App
        repository={createFixtureRepository({ failures: ['evidence'] })}
        initialEntries={[detailPath]}
      />,
    );
    const failedSection = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect(await within(failedSection).findByRole('alert')).toHaveTextContent('데이터를 불러오지 못했습니다');
    failed.unmount();

    render(
      <App
        repository={createFixtureRepository({ permissions: ['evidence'] })}
        initialEntries={[detailPath]}
      />,
    );
    const permissionSection = await screen.findByRole('region', { name: '확인된 기사 근거' });
    expect(await within(permissionSection).findByRole('alert')).toHaveTextContent('접근 권한이 없습니다');
  });
});
