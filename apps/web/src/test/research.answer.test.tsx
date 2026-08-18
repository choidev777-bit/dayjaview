import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { RepositoryProvider } from '../app/RepositoryContext';
import type { ProductRepository, ResearchAnswerResponse } from '../domain/contracts';
import { ResearchPage } from '../pages/ResearchPage';

const HEADLINE = '中 LK-99 복제 성공 주장 소식에 상승';
const PARAGRAPH_1 = '▷일부 언론에 따르면, 중국 연구소가 상온 초전도체를 발견했다는 소식이 전해짐.';
const PARAGRAPH_2 = '▷이 같은 소식 속 신성델타테크 등 초전도체 테마가 상승.';
const PARAGRAPH_3 = '▷한편 관련 종목의 거래량이 크게 늘었음.';

/** 값칸과 근거가 같은 문장을 드는 STOCK_DAY_REASON 모양 + 잘림 신호가 있는 답. */
const ANSWER: ResearchAnswerResponse = {
  data: {
    status: 'ANSWERED',
    answer: {
      queryType: 'STOCK_DAY_REASON',
      countUnit: 'DAILY_SECTION',
      countUnitLabelKo: 'Daily 섹션',
      interpretation: {},
      summaryKo: '신성델타테크은 2024-01-04 특징테마 1개 섹션에 등장했습니다.',
      metrics: [],
      rows: [
        {
          label: '초전도체 · 초전도체',
          values: {
            themeName: '초전도체',
            sectionHeadline: HEADLINE,
            closePrice: 56200,
            changeRate: '+29.94%',
            details: [PARAGRAPH_1, PARAGRAPH_2, PARAGRAPH_3],
            detailTotal: 5,
          },
          evidence: [
            {
              sourceKind: 'INFOSTOCK_DAILY_DESCRIPTION',
              labelKo: '2024-01-04 특징테마 · 초전도체',
              occurredOn: '2024-01-04',
              excerpt: HEADLINE,
              start: null,
              end: null,
            },
            {
              sourceKind: 'INFOSTOCK_DAILY_DESCRIPTION',
              labelKo: '2024-01-04 상세 문단',
              occurredOn: '2024-01-04',
              excerpt: PARAGRAPH_1,
              start: null,
              end: null,
            },
            {
              sourceKind: 'INFOSTOCK_DAILY_DESCRIPTION',
              labelKo: '2024-01-04 상세 문단',
              occurredOn: '2024-01-04',
              excerpt: PARAGRAPH_2,
              start: null,
              end: null,
            },
          ],
        },
      ],
      exclusions: [{ code: 'DISPLAY_LIMIT', labelKo: '화면 표시 한도로 생략', count: 3 }],
      sampleSize: 11,
      evidenceCoverage: 1,
      humanVerified: true,
      notesKo: [],
      versions: {},
      contractVersion: 'research-answer/1.0.0',
    },
  },
  meta: {
    requestId: 'test',
    apiVersion: 'test',
    schemaVersion: 'test',
    generatedAt: '2026-08-18T00:00:00+09:00',
  },
};

function repositoryWith(answer: ResearchAnswerResponse): ProductRepository {
  return { answerResearchQuestion: () => Promise.resolve(answer) } as unknown as ProductRepository;
}

async function ask(): Promise<void> {
  render(
    <RepositoryProvider repository={repositoryWith(ANSWER)}>
      <ResearchPage />
    </RepositoryProvider>,
  );
  await userEvent.type(screen.getByLabelText('질문'), '신성델타테크 왜 올랐어?');
  await userEvent.click(screen.getByRole('button', { name: '질문하기' }));
  await screen.findByText(ANSWER.data.status === 'ANSWERED' ? ANSWER.data.answer.summaryKo : '');
}

describe('리서치 답변 겹침 제거와 잘림 표시', () => {
  it('근거에 있는 문장은 값칸에 다시 나오지 않아 화면에 한 번만 보인다', async () => {
    await ask();
    // 특징테마 문구·상세 1~2문단은 근거가 들고 있으므로 값칸에서 빠져 한 번씩만 남는다.
    expect(screen.getAllByText(HEADLINE)).toHaveLength(1);
    expect(screen.getAllByText(PARAGRAPH_1)).toHaveLength(1);
    expect(screen.getAllByText(PARAGRAPH_2)).toHaveLength(1);
    // 근거에 없는 3번째 문단은 값칸에 남는다.
    expect(screen.getAllByText(PARAGRAPH_3)).toHaveLength(1);
  });

  it('서버가 잘라낸 개수를 안내한다', async () => {
    await ask();
    // detailTotal 5 - 받은 3 = 원문에 2개 더.
    expect(screen.getByText('원문에 문단 2개 더 있음')).toBeInTheDocument();
    // 행 한도 초과는 "답에서 뺀 것"으로 나온다.
    expect(screen.getByText(/화면 표시 한도로 생략 3건/)).toBeInTheDocument();
  });
});
