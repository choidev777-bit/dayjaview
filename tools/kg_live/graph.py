"""온톨로지 라벨을 화면에 그릴 그래프로 만든다.

`research/ontology/labels.jsonl`(운영에 적재된 것과 같은 파일)에서 테마·소재
유형·테마사건기록 노드를 만들고, 답변의 테마·유형·날짜를 노드 번호로 되찾을
색인을 함께 만든다. 좌표는 결정론적이라 실행할 때마다 같은 그림이 나온다.
"""

from __future__ import annotations

import base64
import json
import math
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LABELS_PATH = REPOSITORY_ROOT / "research" / "ontology" / "labels.jsonl"
DEFINITIONS_PATH = (
    REPOSITORY_ROOT / "research" / "ontology" / "catalyst_type_definitions.tsv"
)

GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
THEME_FIELD_RADIUS = 1180.0
EVENT_CLOUD_RADIUS = 104.0
TYPE_RING_RADIUS = 1320.0


@dataclass(slots=True)
class OntologyGraph:
    theme_ids: list[str]
    theme_names: list[str]
    type_ids: list[str]
    type_names: list[str]
    positions: array
    kinds: array
    event_theme_index: array
    event_type_index: array
    event_dates: list[str]
    event_texts: list[str]
    theme_node_by_id: dict[str, int] = field(default_factory=dict)
    theme_node_by_name: dict[str, int] = field(default_factory=dict)
    type_node_by_id: dict[str, int] = field(default_factory=dict)
    type_node_by_name: dict[str, int] = field(default_factory=dict)
    event_nodes_by_theme_date: dict[tuple[int, str], list[int]] = field(
        default_factory=dict
    )
    event_nodes_by_date: dict[str, list[int]] = field(default_factory=dict)

    @property
    def theme_count(self) -> int:
        return len(self.theme_ids)

    @property
    def type_count(self) -> int:
        return len(self.type_ids)

    @property
    def event_count(self) -> int:
        return len(self.event_dates)

    @property
    def node_count(self) -> int:
        return self.theme_count + self.type_count + self.event_count

    def event_node_start(self) -> int:
        return self.theme_count + self.type_count

    def payload(self) -> dict[str, object]:
        """브라우저로 보내는 그래프. 좌표는 base64 float32로 싣는다."""

        counts = [0] * self.theme_count
        for theme_index in self.event_theme_index:
            counts[theme_index] += 1
        return {
            "themeCount": self.theme_count,
            "typeCount": self.type_count,
            "eventCount": self.event_count,
            "nodeCount": self.node_count,
            "relationCount": self.event_count
            + sum(1 for index in self.event_type_index if index != 255),
            "themes": [
                {"id": theme_id, "name": name, "events": counts[index]}
                for index, (theme_id, name) in enumerate(
                    zip(self.theme_ids, self.theme_names, strict=True)
                )
            ],
            "types": [
                {"id": type_id, "name": name}
                for type_id, name in zip(self.type_ids, self.type_names, strict=True)
            ],
            "positions": base64.b64encode(self.positions.tobytes()).decode("ascii"),
            "kinds": base64.b64encode(self.kinds.tobytes()).decode("ascii"),
            "eventThemeIndex": base64.b64encode(
                self.event_theme_index.tobytes()
            ).decode("ascii"),
            "eventTypeIndex": base64.b64encode(self.event_type_index.tobytes()).decode(
                "ascii"
            ),
        }

    def node_label(self, node_index: int) -> str:
        if node_index < self.theme_count:
            return self.theme_names[node_index]
        if node_index < self.theme_count + self.type_count:
            return self.type_names[node_index - self.theme_count]
        return self.event_texts[node_index - self.event_node_start()]

    def event_nodes(self, theme_names: list[str], event_date: str) -> list[int]:
        found: list[int] = []
        for name in theme_names:
            theme_node = self.theme_node_by_name.get(name)
            if theme_node is None:
                continue
            found.extend(self.event_nodes_by_theme_date.get((theme_node, event_date), ()))
        if not found:
            found = list(self.event_nodes_by_date.get(event_date, ())[:3])
        return found


def load_graph() -> OntologyGraph:
    labels = [
        json.loads(line)
        for line in LABELS_PATH.read_text(encoding="utf-8").splitlines()
        if line
    ]

    definitions: dict[str, str] = {}
    for line in DEFINITIONS_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            definitions[parts[0]] = parts[1]

    theme_name_by_id: dict[str, str] = {}
    for label in labels:
        theme_name_by_id.setdefault(str(label["themeId"]), str(label["themeName"]))
    theme_ids = sorted(theme_name_by_id, key=int)
    theme_names = [theme_name_by_id[theme_id] for theme_id in theme_ids]
    theme_position = {theme_id: index for index, theme_id in enumerate(theme_ids)}

    type_ids = sorted({str(label["primaryTypeId"]) for label in labels if label.get("primaryTypeId")})
    type_names = [definitions.get(type_id, type_id) for type_id in type_ids]
    type_position = {type_id: index for index, type_id in enumerate(type_ids)}

    theme_count = len(theme_ids)
    type_count = len(type_ids)
    event_count = len(labels)
    node_count = theme_count + type_count + event_count

    positions = array("f", [0.0]) * (node_count * 2)
    kinds = array("B", [0]) * node_count
    event_theme_index = array("H", [0]) * event_count
    event_type_index = array("B", [255]) * event_count
    event_dates: list[str] = []
    event_texts: list[str] = []

    theme_xy: list[tuple[float, float]] = []
    for index in range(theme_count):
        radius = THEME_FIELD_RADIUS * math.sqrt((index + 0.5) / theme_count)
        angle = GOLDEN_ANGLE * index
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        theme_xy.append((x, y))
        positions[index * 2] = x
        positions[index * 2 + 1] = y
        kinds[index] = 0

    for index in range(type_count):
        angle = 2 * math.pi * index / max(type_count, 1)
        node = theme_count + index
        positions[node * 2] = TYPE_RING_RADIUS * math.cos(angle)
        positions[node * 2 + 1] = TYPE_RING_RADIUS * math.sin(angle)
        kinds[node] = 1

    event_start = theme_count + type_count
    theme_totals: dict[int, int] = defaultdict(int)
    for label in labels:
        theme_totals[theme_position[str(label["themeId"])]] += 1
    largest_theme = max(theme_totals.values())
    theme_seen: dict[int, int] = defaultdict(int)
    event_nodes_by_theme_date: dict[tuple[int, str], list[int]] = defaultdict(list)
    event_nodes_by_date: dict[str, list[int]] = defaultdict(list)

    for row, label in enumerate(labels):
        theme_index = theme_position[str(label["themeId"])]
        primary = label.get("primaryTypeId")
        event_theme_index[row] = theme_index
        event_type_index[row] = type_position[str(primary)] if primary else 255
        event_date = str(label["eventDate"])
        event_dates.append(event_date)
        event_texts.append(str(label["rawText"])[:220])

        seen = theme_seen[theme_index]
        theme_seen[theme_index] = seen + 1
        total = theme_totals[theme_index]
        # 사건이 많은 테마일수록 구름이 크다. 화면에서 테마 크기가 곧 기록 수다.
        cloud = EVENT_CLOUD_RADIUS * (0.35 + 0.65 * math.sqrt(total / largest_theme))
        angle = GOLDEN_ANGLE * seen + theme_index * 0.7
        spread = cloud * math.sqrt((seen + 0.7) / total)
        base_x, base_y = theme_xy[theme_index]
        node = event_start + row
        positions[node * 2] = base_x + spread * math.cos(angle)
        positions[node * 2 + 1] = base_y + spread * math.sin(angle)
        kinds[node] = 2
        event_nodes_by_theme_date[(theme_index, event_date)].append(node)
        event_nodes_by_date[event_date].append(node)

    graph = OntologyGraph(
        theme_ids=theme_ids,
        theme_names=theme_names,
        type_ids=type_ids,
        type_names=type_names,
        positions=positions,
        kinds=kinds,
        event_theme_index=event_theme_index,
        event_type_index=event_type_index,
        event_dates=event_dates,
        event_texts=event_texts,
    )
    graph.theme_node_by_id = {theme_id: theme_position[theme_id] for theme_id in theme_ids}
    graph.theme_node_by_name = {
        name: index for index, name in enumerate(theme_names)
    }
    graph.type_node_by_id = {
        type_id: theme_count + type_position[type_id] for type_id in type_ids
    }
    graph.type_node_by_name = {
        name: theme_count + index for index, name in enumerate(type_names)
    }
    graph.event_nodes_by_theme_date = dict(event_nodes_by_theme_date)
    graph.event_nodes_by_date = dict(event_nodes_by_date)
    return graph


if __name__ == "__main__":
    built = load_graph()
    print(
        json.dumps(
            {
                "themeCount": built.theme_count,
                "typeCount": built.type_count,
                "eventCount": built.event_count,
                "nodeCount": built.node_count,
            },
            indent=2,
        )
    )
