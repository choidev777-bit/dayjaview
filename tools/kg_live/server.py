"""시연용 로컬 서버. 질문 하나를 운영 엔진에 그대로 물어보고 과정을 흘려보낸다.

  bash tools/kg_live/start.sh        # 터널 + 서버 한 번에
  브라우저에서 http://127.0.0.1:8899

`/ask`는 Server-Sent Events로 (a) 해석기가 만든 실제 조회 계획, (b) 실제로
일어난 DB 조회 하나하나, (c) 최종 답변 블록을 순서대로 보낸다. 수치는 전부
`packages.ontology` 운영 코드가 낸 것이고 이 파일은 옮기기만 한다.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tools.kg_live.engine import LiveEngine, build_engine
from tools.kg_live.graph import OntologyGraph, load_graph

HERE = Path(__file__).resolve().parent
PORT = 8899

_ENGINE: LiveEngine | None = None
_GRAPH: OntologyGraph | None = None
_GRAPH_PAYLOAD: bytes | None = None
# Postgres 연결도 가격 sqlite도 만든 스레드에서만 쓸 수 있다. 질문은 전용
# 스레드 하나가 순서대로 처리한다.
_JOBS: "queue.Queue[tuple[str, queue.Queue[dict[str, Any]]]]" = queue.Queue()


# ------------------------------------------------------------------ 조회 관찰


class TracingRepository:
    """실제 저장소 호출을 그대로 넘기면서 무슨 조회가 언제 일어났는지 알린다."""

    _LABELS = {
        "versions": "데이터 버전 확인",
        "ready_prerequisites": "질의 선행 조건 확인",
        "daily_day": "특정 거래일 시황 조회",
        "daily_days": "기간 시황 조회",
        "stock_daily_rows": "종목 일별 언급 조회",
        "theme_daily_changes": "테마 등락 조회",
        "theme_members": "테마 구성 종목 조회",
        "stock_theme_memberships": "종목 소속 테마 조회",
        "theme_history": "테마 사건 기록 조회",
        "catalysts": "소재 사건 조회",
        "value_facts": "금액 사실 조회",
        "outcomes": "사건 뒤 주가 조회",
        "leader_outcomes": "주도주 사건 뒤 주가 조회",
    }

    def __init__(self, inner: Any, sink: "queue.Queue[dict[str, Any]]") -> None:
        self._inner = inner
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if not callable(target) or name.startswith("_"):
            return target

        def traced(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            result = target(*args, **kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            rows = len(result) if isinstance(result, (tuple, list)) else None
            self._sink.put(
                {
                    "type": "db",
                    "call": name,
                    "labelKo": self._LABELS.get(name, name),
                    "detail": _describe_call(name, args, kwargs),
                    "rows": rows,
                    "ms": round(elapsed, 1),
                }
            )
            return result

        return traced


def _describe_call(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """조회 조건을 사람이 읽을 한 줄로 줄인다."""

    parts: list[str] = []
    for value in list(args) + list(kwargs.values()):
        if isinstance(value, date):
            parts.append(value.isoformat())
        elif isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
            parts.append("T+" + ", T+".join(str(item) for item in value))
        elif hasattr(value, "catalyst_type_id") or hasattr(value, "theme_ids"):
            parts.append(_describe_filter(value))
    return " · ".join(part for part in parts if part)[:160]


def _describe_filter(value: Any) -> str:
    fields = []
    for name in (
        "catalyst_type_id",
        "topic",
        "theme_ids",
        "seed_stock_code",
        "date_from",
        "date_to",
        "direction",
    ):
        item = getattr(value, name, None)
        if not item:
            continue
        if isinstance(item, (tuple, list)):
            item = ",".join(str(entry) for entry in item)
        fields.append(f"{name}={item}")
    return " ".join(fields)


# ------------------------------------------------------------------ 노드 되찾기


def _focus_targets(graph: OntologyGraph, answer: dict[str, Any]) -> list[dict[str, Any]]:
    """실제 답변이 쓴 테마·유형·사건을 그래프 노드 번호로 되찾는다."""

    interpretation = answer.get("interpretation", {})
    targets: list[dict[str, Any]] = []

    catalyst = interpretation.get("catalystType")
    if isinstance(catalyst, dict):
        node = graph.type_node_by_id.get(str(catalyst.get("typeId")))
        if node is not None:
            targets.append(
                {
                    "kind": "TYPE",
                    "node": node,
                    "title": str(catalyst.get("nameKo") or catalyst.get("typeId")),
                    "caption": f"소재 유형 · 원문에서 '{catalyst.get('matchedText')}'",
                }
            )

    for theme in interpretation.get("themes") or ():
        if not isinstance(theme, dict):
            continue
        node = graph.theme_node_by_id.get(str(theme.get("sourceThemeId")))
        if node is None:
            node = graph.theme_node_by_name.get(str(theme.get("themeName")))
        if node is not None:
            targets.append(
                {
                    "kind": "THEME",
                    "node": node,
                    "title": str(theme.get("themeName")),
                    "caption": f"테마 · 원문에서 '{theme.get('matchedText')}'",
                }
            )

    known_themes = [
        str(theme.get("themeName"))
        for theme in interpretation.get("themes") or ()
        if isinstance(theme, dict)
    ]

    for row in (answer.get("rows") or ())[:12]:
        values = row.get("values") or {}
        evidence = row.get("evidence") or []
        excerpt = str(evidence[0].get("excerpt")) if evidence else ""
        theme_names = [str(name) for name in (values.get("themeNames") or [])]
        event_date = str(values.get("eventDate") or "")

        # 날짜가 붙은 행은 그래프의 실제 사건 기록을 가리킨다.
        if event_date:
            nodes = graph.event_nodes(theme_names, event_date)
            if not nodes:
                continue
            targets.append(
                {
                    "kind": "EVENT",
                    "node": nodes[0],
                    "alsoNodes": nodes[1:6],
                    "title": excerpt[:80] or event_date,
                    "caption": f"{event_date} · 실제 사건 기록",
                    "row": {
                        "eventDate": event_date,
                        "themeNames": theme_names,
                        "leaderCount": values.get("leaderCount"),
                        "upCount": values.get("upCount"),
                        "medianReturn": values.get("medianReturn"),
                        "horizon": values.get("horizon"),
                        "leaders": values.get("leaders") or [],
                        "excerpt": excerpt,
                    },
                    "themeNodes": [
                        graph.theme_node_by_name[name]
                        for name in theme_names
                        if name in graph.theme_node_by_name
                    ],
                }
            )
            continue

        # 테마를 세는 행(소속 테마·구성 종목)은 테마 노드를 가리킨다.
        theme_node = graph.theme_node_by_id.get(
            str(values.get("sourceThemeId"))
        ) or graph.theme_node_by_name.get(str(row.get("label")))
        if theme_node is not None:
            targets.append(
                {
                    "kind": "THEME",
                    "node": theme_node,
                    "alsoNodes": _evidence_nodes(graph, evidence, [str(row.get("label"))]),
                    "title": str(row.get("label")),
                    "caption": excerpt[:110] or "테마 노드",
                }
            )
            continue

        # 소재 유형을 세는 행(테마 과거 소재)은 유형 노드와 근거 기록을 가리킨다.
        type_node = graph.type_node_by_name.get(str(row.get("label")))
        if type_node is not None:
            targets.append(
                {
                    "kind": "TYPE",
                    "node": type_node,
                    "alsoNodes": _evidence_nodes(graph, evidence, known_themes),
                    "title": str(row.get("label")),
                    "caption": f"원천 기록 {values.get('recordCount', '—')}건 · {excerpt[:70]}",
                }
            )
    return targets


def _evidence_nodes(
    graph: OntologyGraph, evidence: list[dict[str, Any]], theme_names: list[str]
) -> list[int]:
    """근거에 적힌 날짜로 실제 사건 기록 노드를 찾는다."""

    nodes: list[int] = []
    for item in evidence[:4]:
        occurred = item.get("occurredOn")
        if not occurred:
            continue
        nodes.extend(graph.event_nodes(theme_names, str(occurred))[:2])
    return nodes[:8]


# ------------------------------------------------------------------ 서버


def _engine() -> LiveEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = build_engine()
    return _ENGINE


def _engine_loop(ready: threading.Event) -> None:
    """엔진 스레드. 여기서 연결을 만들고 여기서만 쓴다.

    터널이 끊기면 다음 질문에서 다시 연결한다. 시연 중에 서버가 죽지 않는다.
    """

    while True:
        try:
            _engine()
            break
        except Exception as error:  # noqa: BLE001 - 터널이 아직이면 기다린다
            print(f"엔진 연결 재시도: {type(error).__name__}")
            time.sleep(3)
    ready.set()
    while True:
        question, sink = _JOBS.get()
        try:
            _run_question(question, sink)
        except Exception as error:  # noqa: BLE001 - 스레드를 죽이지 않는다
            global _ENGINE
            _ENGINE = None  # 다음 질문에서 다시 연결한다
            sink.put({"type": "error", "message": f"{type(error).__name__}: {error}"})
            sink.put({"type": "done"})


def _graph() -> OntologyGraph:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = load_graph()
    return _GRAPH


def _graph_payload() -> bytes:
    global _GRAPH_PAYLOAD
    if _GRAPH_PAYLOAD is None:
        _GRAPH_PAYLOAD = json.dumps(_graph().payload()).encode("utf-8")
    return _GRAPH_PAYLOAD


def _run_question(question: str, sink: "queue.Queue[dict[str, Any]]") -> None:
    engine = _engine()
    graph = _graph()
    started = time.perf_counter()
    try:
        result = engine.plan(question)
        plan = result.plan
        sink.put(
            {
                "type": "plan",
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "ok": plan is not None,
                "queryType": None if plan is None else plan.query_type.value,
                "slots": {} if plan is None else _plan_slots(plan),
                "failure": None if result.failure is None else result.failure.as_dict(),
                "llm": engine.llm_enabled,
            }
        )

        boundary = engine.boundary
        inner = boundary._repository  # noqa: SLF001 - 시연 중 실제 조회를 보여준다
        boundary._repository = TracingRepository(inner, sink)  # noqa: SLF001
        try:
            answer = engine.answer(question)
        finally:
            boundary._repository = inner  # noqa: SLF001

        payload: dict[str, Any] = dict(answer)
        block = payload.get("answer")
        if isinstance(block, dict):
            payload["focus"] = _focus_targets(graph, block)
        payload["type"] = "answer"
        payload["ms"] = round((time.perf_counter() - started) * 1000, 1)
        sink.put(payload)
    except Exception as error:  # noqa: BLE001 - 시연 중 죽지 않고 화면에 보여준다
        sink.put(
            {
                "type": "error",
                "message": f"{type(error).__name__}: {error}",
                "trace": traceback.format_exc()[-1200:],
            }
        )
    finally:
        sink.put({"type": "done"})


def _plan_slots(plan: Any) -> dict[str, Any]:
    slots = dict(plan.slot_mapping())
    if plan.topic:
        slots["topic"] = plan.topic
    if plan.catalyst_type is not None:
        slots["catalystType"] = getattr(plan.catalyst_type, "name_ko", None) or getattr(
            plan.catalyst_type, "type_id", ""
        )
    if plan.themes:
        slots["themes"] = [theme.theme_name for theme in plan.themes]
    if plan.outcome_horizon:
        slots["outcomeHorizon"] = f"T+{plan.outcome_horizon}"
    return slots


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: Any) -> None:  # 시연 중 콘솔을 조용하게 둔다
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send_bytes(
                (HERE / "app.html").read_bytes(), "text/html; charset=utf-8"
            )
        elif self.path == "/graph.json":
            self._send_bytes(_graph_payload(), "application/json; charset=utf-8")
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("content-length", "0")
            self.end_headers()
        elif self.path == "/health":
            self._send_bytes(b'{"status":"ok"}', "application/json")
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ask":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        question = str(body.get("question", "")).strip()[:300]
        if not question:
            self.send_error(400)
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "close")
        self.end_headers()

        sink: queue.Queue[dict[str, Any]] = queue.Queue()
        _JOBS.put((question, sink))
        while True:
            event = sink.get()
            chunk = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError):
                return
            if event.get("type") == "done":
                return

    def _send_bytes(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    graph = _graph()
    print(f"그래프 {graph.node_count:,} 노드 준비 완료")
    ready = threading.Event()
    threading.Thread(target=_engine_loop, args=(ready,), daemon=True).start()
    ready.wait()
    print("운영 엔진 연결 완료 (Postgres 터널 + 가격 corpus)")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"http://127.0.0.1:{PORT} 열림 — Ctrl+C로 종료")
    server.serve_forever()


if __name__ == "__main__":
    main()
