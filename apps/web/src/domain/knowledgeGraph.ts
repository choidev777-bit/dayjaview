export const KNOWLEDGE_GRAPH_NODE_COUNT = 40_004;
export const KNOWLEDGE_GRAPH_EDGE_COUNT = KNOWLEDGE_GRAPH_NODE_COUNT - 1;
export const KNOWLEDGE_GRAPH_WIDTH = 2_600;
export const KNOWLEDGE_GRAPH_HEIGHT = 1_700;

export const graphNodeKinds = [
  { label: '테마', color: '#f2b84b', count: 280 },
  { label: '소재', color: '#f15f64', count: 8_000 },
  { label: '테마 반응', color: '#35c4df', count: 28_000 },
  { label: '관련 종목', color: '#42d39b', count: 3_000 },
  { label: '근거', color: '#d6d94a', count: 724 },
] as const;

export type GraphNodeKind = 0 | 1 | 2 | 3 | 4;

export type GraphFocusNode = {
  index: number;
  title: string;
  subtitle: string;
  step: string;
};

export type KnowledgeGraph = {
  x: Float32Array;
  y: Float32Array;
  radius: Float32Array;
  kind: Uint8Array;
  parent: Uint16Array;
  focusNodes: GraphFocusNode[];
};

const THEME_COUNT = graphNodeKinds[0].count;

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

function kindAt(index: number): GraphNodeKind {
  let boundary = 0;
  for (let kind = 0; kind < graphNodeKinds.length; kind += 1) {
    boundary += graphNodeKinds[kind].count;
    if (index < boundary) return kind as GraphNodeKind;
  }
  return 4;
}

export function createKnowledgeGraph(): KnowledgeGraph {
  const random = seededRandom(20_260_818);
  const x = new Float32Array(KNOWLEDGE_GRAPH_NODE_COUNT);
  const y = new Float32Array(KNOWLEDGE_GRAPH_NODE_COUNT);
  const radius = new Float32Array(KNOWLEDGE_GRAPH_NODE_COUNT);
  const kind = new Uint8Array(KNOWLEDGE_GRAPH_NODE_COUNT);
  const parent = new Uint16Array(KNOWLEDGE_GRAPH_NODE_COUNT);
  const centerX = KNOWLEDGE_GRAPH_WIDTH / 2;
  const centerY = KNOWLEDGE_GRAPH_HEIGHT / 2;
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));

  for (let index = 0; index < THEME_COUNT; index += 1) {
    const normalized = index === 0 ? 0 : Math.sqrt(index / (THEME_COUNT - 1));
    const angle = index * goldenAngle + (random() - 0.5) * 0.2;
    const radial = normalized * 700;
    x[index] = centerX + Math.cos(angle) * radial * 1.55;
    y[index] = centerY + Math.sin(angle) * radial * 0.94;
    radius[index] = 5.5 + random() * 5;
    kind[index] = 0;
    parent[index] = index;
  }

  for (let index = THEME_COUNT; index < KNOWLEDGE_GRAPH_NODE_COUNT; index += 1) {
    const nodeKind = kindAt(index);
    const themeIndex = (index - THEME_COUNT) % THEME_COUNT;
    const clusterScale = 26 + ((themeIndex * 17) % 72);
    const distance = Math.sqrt(random()) * clusterScale;
    const angle = random() * Math.PI * 2;
    const horizontalJitter = (random() - 0.5) * 9;
    const verticalJitter = (random() - 0.5) * 9;

    x[index] = x[themeIndex] + Math.cos(angle) * distance * 1.12 + horizontalJitter;
    y[index] = y[themeIndex] + Math.sin(angle) * distance * 0.78 + verticalJitter;
    radius[index] =
      nodeKind === 1
        ? 2.4 + random() * 2.4
        : nodeKind === 2
          ? 1.05 + random() * 1.25
          : nodeKind === 3
            ? 1.8 + random() * 1.8
            : 1.55 + random() * 1.4;
    kind[index] = nodeKind;
    parent[index] = themeIndex;
  }

  const catalystStart = graphNodeKinds[0].count;
  const reactionStart = catalystStart + graphNodeKinds[1].count;
  const companyStart = reactionStart + graphNodeKinds[2].count;
  const evidenceStart = companyStart + graphNodeKinds[3].count;

  return {
    x,
    y,
    radius,
    kind,
    parent,
    focusNodes: [
      { index: 0, title: '로봇', subtitle: 'Theme', step: '질문에서 테마 식별' },
      {
        index: catalystStart,
        title: '휴머노이드 산업 지원 정책',
        subtitle: 'Catalyst',
        step: '유사한 상승 소재 탐색',
      },
      {
        index: reactionStart,
        title: '과거 유사 이벤트 14건',
        subtitle: 'ThemeReaction',
        step: '과거 테마 반응 연결',
      },
      {
        index: companyStart,
        title: '당시 주도 종목군',
        subtitle: 'CompanyRole',
        step: '주도 종목 역할 확인',
      },
      {
        index: reactionStart + THEME_COUNT,
        title: 'T+5 중앙수익률 +4.8%',
        subtitle: 'MarketOutcome',
        step: '사건 이후 반응 계산',
      },
      {
        index: evidenceStart,
        title: '정책 발표 원문 · 테마 시황',
        subtitle: 'Evidence',
        step: '답변 근거 검증',
      },
    ],
  };
}
