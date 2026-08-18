import {
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  createKnowledgeGraph,
  graphNodeKinds,
  KNOWLEDGE_GRAPH_EDGE_COUNT,
  KNOWLEDGE_GRAPH_HEIGHT,
  KNOWLEDGE_GRAPH_NODE_COUNT,
  KNOWLEDGE_GRAPH_WIDTH,
  type KnowledgeGraph,
} from '../domain/knowledgeGraph';

type Camera = { x: number; y: number; zoom: number };
type RunState = 'idle' | 'running' | 'answered';

const DEFAULT_QUESTION = '비슷한 재료가 나왔을 때 보통 하루 만에 끝났어, 며칠 이어졌어?';
const FULL_CAMERA: Camera = {
  x: KNOWLEDGE_GRAPH_WIDTH / 2,
  y: KNOWLEDGE_GRAPH_HEIGHT / 2,
  zoom: 1,
};
const STEP_DELAY_MS = 920;

function formatNumber(value: number): string {
  return new Intl.NumberFormat('ko-KR').format(value);
}

function paintStaticGraph(graph: KnowledgeGraph): HTMLCanvasElement {
  const layer = document.createElement('canvas');
  layer.width = KNOWLEDGE_GRAPH_WIDTH;
  layer.height = KNOWLEDGE_GRAPH_HEIGHT;
  const context = layer.getContext('2d');
  if (!context) return layer;

  const background = context.createRadialGradient(
    KNOWLEDGE_GRAPH_WIDTH * 0.48,
    KNOWLEDGE_GRAPH_HEIGHT * 0.47,
    40,
    KNOWLEDGE_GRAPH_WIDTH * 0.5,
    KNOWLEDGE_GRAPH_HEIGHT * 0.5,
    KNOWLEDGE_GRAPH_WIDTH * 0.65,
  );
  background.addColorStop(0, '#111b31');
  background.addColorStop(0.58, '#0a1120');
  background.addColorStop(1, '#050914');
  context.fillStyle = background;
  context.fillRect(0, 0, layer.width, layer.height);

  context.beginPath();
  for (let index = graphNodeKinds[0].count; index < KNOWLEDGE_GRAPH_NODE_COUNT; index += 2) {
    const parent = graph.parent[index];
    context.moveTo(graph.x[index], graph.y[index]);
    context.lineTo(graph.x[parent], graph.y[parent]);
  }
  context.strokeStyle = 'rgba(49, 81, 132, .18)';
  context.lineWidth = 0.62;
  context.stroke();

  for (let nodeKind = 0; nodeKind < graphNodeKinds.length; nodeKind += 1) {
    context.beginPath();
    for (let index = 0; index < KNOWLEDGE_GRAPH_NODE_COUNT; index += 1) {
      if (graph.kind[index] !== nodeKind) continue;
      context.moveTo(graph.x[index] + graph.radius[index], graph.y[index]);
      context.arc(graph.x[index], graph.y[index], graph.radius[index], 0, Math.PI * 2);
    }
    context.fillStyle = graphNodeKinds[nodeKind].color;
    context.globalAlpha = nodeKind === 2 ? 0.82 : 0.92;
    context.fill();
  }
  context.globalAlpha = 1;

  return layer;
}

function cameraTransform(width: number, height: number, camera: Camera) {
  const fit = Math.min(width / KNOWLEDGE_GRAPH_WIDTH, height / KNOWLEDGE_GRAPH_HEIGHT) * 0.96;
  const scale = fit * camera.zoom;
  return {
    scale,
    offsetX: width / 2 - camera.x * scale,
    offsetY: height / 2 - camera.y * scale,
  };
}

function drawFocusPath(
  context: CanvasRenderingContext2D,
  graph: KnowledgeGraph,
  focusStep: number,
  time: number,
) {
  if (focusStep < 0) return;
  const visible = graph.focusNodes.slice(0, focusStep + 1);

  context.beginPath();
  visible.forEach((focus, index) => {
    if (index === 0) context.moveTo(graph.x[focus.index], graph.y[focus.index]);
    else context.lineTo(graph.x[focus.index], graph.y[focus.index]);
  });
  context.strokeStyle = 'rgba(255, 139, 70, .72)';
  context.lineWidth = 2.1;
  context.setLineDash([8, 8]);
  context.lineDashOffset = -(time / 28);
  context.stroke();
  context.setLineDash([]);

  visible.forEach((focus, index) => {
    const active = index === visible.length - 1;
    const pulse = active ? 1 + Math.sin(time / 150) * 0.12 : 1;
    const nodeIndex = focus.index;
    const halo = context.createRadialGradient(
      graph.x[nodeIndex],
      graph.y[nodeIndex],
      2,
      graph.x[nodeIndex],
      graph.y[nodeIndex],
      active ? 36 * pulse : 16,
    );
    halo.addColorStop(0, active ? 'rgba(255, 128, 47, .95)' : 'rgba(77, 213, 239, .78)');
    halo.addColorStop(0.3, active ? 'rgba(255, 103, 24, .44)' : 'rgba(53, 196, 223, .24)');
    halo.addColorStop(1, 'rgba(0, 0, 0, 0)');
    context.beginPath();
    context.fillStyle = halo;
    context.arc(graph.x[nodeIndex], graph.y[nodeIndex], active ? 36 * pulse : 16, 0, Math.PI * 2);
    context.fill();
    context.beginPath();
    context.fillStyle = active ? '#ff7a2f' : '#c6f6ff';
    context.arc(graph.x[nodeIndex], graph.y[nodeIndex], active ? 6.8 : 4, 0, Math.PI * 2);
    context.fill();
  });
}

export function KnowledgeGraphPage() {
  const graph = useMemo(() => createKnowledgeGraph(), []);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const camera = useRef<Camera>({ ...FULL_CAMERA });
  const cameraTarget = useRef<Camera>({ ...FULL_CAMERA });
  const dimensions = useRef({ width: 1, height: 1 });
  const focusStepRef = useRef(-1);
  const runStateRef = useRef<RunState>('idle');
  const drag = useRef<{ x: number; y: number; cameraX: number; cameraY: number } | null>(null);
  const timers = useRef<number[]>([]);
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [runState, setRunState] = useState<RunState>('idle');
  const [focusStep, setFocusStep] = useState(-1);
  const [displayCount, setDisplayCount] = useState(0);

  useEffect(() => {
    focusStepRef.current = focusStep;
  }, [focusStep]);

  useEffect(() => {
    runStateRef.current = runState;
  }, [runState]);

  useEffect(() => {
    const startedAt = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min((now - startedAt) / 1_100, 1);
      setDisplayCount(Math.round(KNOWLEDGE_GRAPH_NODE_COUNT * (1 - (1 - progress) ** 3)));
      if (progress < 1) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const context = canvas.getContext('2d');
    if (!context) return undefined;
    const staticGraph = paintStaticGraph(graph);
    let animationFrame = 0;

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      dimensions.current = { width: bounds.width, height: bounds.height };
      canvas.width = Math.max(1, Math.round(bounds.width * ratio));
      canvas.height = Math.max(1, Math.round(bounds.height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const render = (time: number) => {
      const current = camera.current;
      const target = cameraTarget.current;
      const speed = runStateRef.current === 'running' ? 0.075 : 0.11;
      current.x += (target.x - current.x) * speed;
      current.y += (target.y - current.y) * speed;
      current.zoom += (target.zoom - current.zoom) * speed;

      const { width, height } = dimensions.current;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.fillStyle = '#050914';
      context.fillRect(0, 0, width, height);

      const transform = cameraTransform(width, height, current);
      context.save();
      context.translate(transform.offsetX, transform.offsetY);
      context.scale(transform.scale, transform.scale);
      context.globalAlpha = runStateRef.current === 'running' ? 0.52 : 1;
      context.drawImage(staticGraph, 0, 0);
      context.globalAlpha = 1;
      drawFocusPath(context, graph, focusStepRef.current, time);
      context.restore();

      const vignette = context.createRadialGradient(
        width / 2,
        height / 2,
        Math.min(width, height) * 0.2,
        width / 2,
        height / 2,
        Math.max(width, height) * 0.72,
      );
      vignette.addColorStop(0, 'rgba(5, 9, 20, 0)');
      vignette.addColorStop(1, 'rgba(5, 9, 20, .52)');
      context.fillStyle = vignette;
      context.fillRect(0, 0, width, height);

      animationFrame = window.requestAnimationFrame(render);
    };

    animationFrame = window.requestAnimationFrame(render);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(animationFrame);
    };
  }, [graph]);

  useEffect(() => {
    if (focusStep < 0) return;
    const focus = graph.focusNodes[focusStep];
    cameraTarget.current = {
      x: graph.x[focus.index],
      y: graph.y[focus.index],
      zoom: focusStep === 0 ? 2.05 : 3.1 + (focusStep % 2) * 0.42,
    };
  }, [focusStep, graph]);

  useEffect(
    () => () => {
      timers.current.forEach((timer) => window.clearTimeout(timer));
    },
    [],
  );

  const resetView = useCallback(() => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    cameraTarget.current = { ...FULL_CAMERA };
    setFocusStep(-1);
    setRunState('idle');
  }, []);

  const runQuery = useCallback(() => {
    if (!question.trim()) return;
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    setRunState('running');
    setFocusStep(-1);
    cameraTarget.current = { ...FULL_CAMERA, zoom: 1.08 };

    graph.focusNodes.forEach((_, index) => {
      timers.current.push(
        window.setTimeout(() => setFocusStep(index), 360 + index * STEP_DELAY_MS),
      );
    });
    timers.current.push(
      window.setTimeout(
        () => setRunState('answered'),
        360 + graph.focusNodes.length * STEP_DELAY_MS,
      ),
    );
  }, [graph.focusNodes, question]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    runQuery();
  };

  const pointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      cameraX: cameraTarget.current.x,
      cameraY: cameraTarget.current.y,
    };
  };

  const pointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drag.current) return;
    const transform = cameraTransform(
      dimensions.current.width,
      dimensions.current.height,
      cameraTarget.current,
    );
    cameraTarget.current = {
      ...cameraTarget.current,
      x: drag.current.cameraX - (event.clientX - drag.current.x) / transform.scale,
      y: drag.current.cameraY - (event.clientY - drag.current.y) / transform.scale,
    };
  };

  const pointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag.current = null;
  };

  const wheel = (event: ReactWheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.16 : 0.86;
    cameraTarget.current = {
      ...cameraTarget.current,
      zoom: Math.min(8, Math.max(0.72, cameraTarget.current.zoom * factor)),
    };
  };

  const activeFocus = focusStep >= 0 ? graph.focusNodes[focusStep] : null;

  return (
    <main className={`knowledge-graph ${runState === 'running' ? 'is-running' : ''}`}>
      <canvas
        ref={canvasRef}
        className="knowledge-graph__canvas"
        role="img"
        aria-label={`테마, 소재, 시장 반응, 관련 종목과 근거로 구성된 ${formatNumber(KNOWLEDGE_GRAPH_NODE_COUNT)}개 노드 지식 그래프`}
        onDoubleClick={resetView}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={pointerUp}
        onWheel={wheel}
      />

      <header className="kg-header">
        <div className="kg-brand">
          <span className="kg-brand__mark" aria-hidden="true" />
          <div>
            <strong>DAY JA VIEW</strong>
            <small>ONTOLOGY KNOWLEDGE GRAPH</small>
          </div>
        </div>
        <dl className="kg-stats">
          <div>
            <dt>NODES</dt>
            <dd>{formatNumber(displayCount)}</dd>
          </div>
          <div>
            <dt>RELATIONS</dt>
            <dd>{formatNumber(KNOWLEDGE_GRAPH_EDGE_COUNT)}</dd>
          </div>
          <div>
            <dt>STATUS</dt>
            <dd className="kg-status"><i />READY</dd>
          </div>
        </dl>
        <div className="kg-controls">
          <button type="button" onClick={resetView}>전체 보기</button>
          <button
            type="button"
            onClick={() => void document.documentElement.requestFullscreen?.()}
          >
            전체화면
          </button>
        </div>
      </header>

      <aside className="kg-legend" aria-label="노드 범례">
        <span className="kg-eyebrow">NODE TYPES</span>
        <ul>
          {graphNodeKinds.map((nodeKind) => (
            <li key={nodeKind.label}>
              <i style={{ backgroundColor: nodeKind.color }} />
              {nodeKind.label}
            </li>
          ))}
        </ul>
        <small>드래그 이동 · 휠 확대 · 더블클릭 초기화</small>
      </aside>

      {runState !== 'idle' ? (
        <section className="kg-trace" aria-live="polite">
          <div className="kg-trace__heading">
            <span className="kg-eyebrow">QUERY TRACE</span>
            <b>{focusStep + 1} / {graph.focusNodes.length}</b>
          </div>
          <ol>
            {graph.focusNodes.map((focus, index) => (
              <li
                key={focus.step}
                className={index === focusStep ? 'is-active' : index < focusStep ? 'is-complete' : ''}
              >
                <i />
                <span>{focus.step}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {activeFocus ? (
        <div className="kg-focus-label" aria-live="polite">
          <small>{activeFocus.subtitle}</small>
          <strong>{activeFocus.title}</strong>
        </div>
      ) : null}

      {runState === 'answered' ? (
        <section className="kg-answer" aria-live="polite">
          <div className="kg-answer__top">
            <span className="kg-eyebrow">GROUNDED ANSWER</span>
            <span className="kg-answer__verified"><i />근거 검증 완료</span>
          </div>
          <p>
            과거 유사 이벤트 14건 중 <strong>9건</strong>은 상승 흐름이 3거래일 이상 이어졌습니다.
            하루 만에 반응이 끝난 사례는 5건이었습니다.
          </p>
          <dl>
            <div><dt>T+5 중앙수익률</dt><dd>+4.8%</dd></div>
            <div><dt>상승 사례</dt><dd>10 / 14건</dd></div>
            <div><dt>분석 근거</dt><dd>6개 관계</dd></div>
          </dl>
          <small>과거 관측 결과이며 향후 수익을 예측하거나 보장하지 않습니다.</small>
        </section>
      ) : null}

      <form className="kg-query" onSubmit={submit}>
        <div className="kg-query__icon" aria-hidden="true">⌁</div>
        <label htmlFor="kg-question">자연어 질문</label>
        <input
          id="kg-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={runState === 'running'}
          autoComplete="off"
        />
        <button type="submit" disabled={!question.trim() || runState === 'running'}>
          {runState === 'running' ? <><i />분석 중</> : '질문 실행'}
        </button>
      </form>

      <div className="kg-mode">PRESENTATION TRACE · ONTOLOGY V1.3</div>
    </main>
  );
}
