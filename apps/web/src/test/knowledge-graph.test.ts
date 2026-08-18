import { describe, expect, it } from 'vitest';
import {
  createKnowledgeGraph,
  graphNodeKinds,
  KNOWLEDGE_GRAPH_NODE_COUNT,
} from '../domain/knowledgeGraph';

describe('발표용 지식 그래프', () => {
  it('정확히 40,004개 노드와 여섯 단계 질문 추적 경로를 만든다', () => {
    const graph = createKnowledgeGraph();

    expect(graph.x).toHaveLength(KNOWLEDGE_GRAPH_NODE_COUNT);
    expect(graph.y).toHaveLength(KNOWLEDGE_GRAPH_NODE_COUNT);
    expect(graph.radius).toHaveLength(KNOWLEDGE_GRAPH_NODE_COUNT);
    expect(graph.kind).toHaveLength(KNOWLEDGE_GRAPH_NODE_COUNT);
    expect(graph.parent).toHaveLength(KNOWLEDGE_GRAPH_NODE_COUNT);
    expect(graph.focusNodes).toHaveLength(6);
    expect(graphNodeKinds.reduce((sum, nodeKind) => sum + nodeKind.count, 0)).toBe(
      KNOWLEDGE_GRAPH_NODE_COUNT,
    );
  });

  it('같은 시드로 녹화할 때 항상 동일한 배치를 만든다', () => {
    const first = createKnowledgeGraph();
    const second = createKnowledgeGraph();

    expect(Array.from(first.x.slice(0, 20))).toEqual(Array.from(second.x.slice(0, 20)));
    expect(Array.from(first.y.slice(-20))).toEqual(Array.from(second.y.slice(-20)));
  });
});
