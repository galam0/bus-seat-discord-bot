"""수도권 지하철: 서울시 공개 데이터로 역·노선 그래프를 만들고 경로를 찾는다.

데이터 (서울 열린데이터광장, 공공누리 1유형: 출처 서울특별시)
- subwayStationMaster        : BLDN_ID, BLDN_NM, ROUTE, LAT, LOT          (역 좌표)
- SearchSTNBySubwayLineInfo  : STATION_CD, STATION_NM, LINE_NUM, FR_CODE  (역 코드·외부코드)

역 사이 연결은 공개 데이터에 따로 없어서 외부코드(FR_CODE) 순서와 좌표로 추정한다.
1) 같은 노선·같은 코드 묶음(접두어/가지 번호) 안에서 코드 순서대로 잇는다.
2) 가지 묶음은 정해진 분기역에 붙인다 (예: 211-1 -> 211, P549 -> 548).
3) 그래도 떨어진 조각은 가장 가까운 역끼리 잇는다.
4) 2호선은 순환선이라 처음과 끝을 잇는다.
환승: 다른 노선의 같은 이름 역, 또는 250m 안의 역.
"""
from __future__ import annotations

import heapq
import logging
import re
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

log = logging.getLogger("seatbot.subway")

API = "http://openapi.seoul.go.kr:8088/{key}/json/{service}/1/1000/"
REALTIME = "http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/30/{name}"

TRANSFER_MIN = 8.0  # 환승 한 번에 드는 시간(분): 걷기 + 기다리기
DWELL_MIN = 0.5  # 역마다 서 있는 시간
KMH = 33  # 역 사이 평균 속도
MAX_LINK_KM = 12  # 코드 순서로 이을 때 이보다 멀면 의심해서 잇지 않는다
TRANSFER_KM = 0.25


# ---------------------------------------------------------------- 노선 이름·색

# (이름에 들어 있는 글자, 보여줄 이름, 색). 위에서부터 먼저 맞는 것을 쓴다.
_LINES = [
    ("신분당", "신분당선", "#D4003B"),
    ("수인분당", "수인분당선", "#FABE00"), ("분당", "수인분당선", "#FABE00"), ("수인", "수인분당선", "#FABE00"),
    ("경의", "경의중앙선", "#77C4A3"), ("중앙", "경의중앙선", "#77C4A3"),
    ("공항", "공항철도", "#0090D2"),
    ("경춘", "경춘선", "#0C8E72"), ("경강", "경강선", "#003DA5"),
    ("우이신설", "우이신설선", "#B0CE18"), ("신림", "신림선", "#6789CA"),
    ("서해", "서해선", "#8FC31F"), ("김포", "김포골드라인", "#A17800"),
    ("인천2", "인천 2호선", "#ED8B00"), ("인천선", "인천 1호선", "#7CA8D5"), ("인천1", "인천 1호선", "#7CA8D5"),
    ("에버라인", "용인에버라인", "#56AD2D"), ("용인", "용인에버라인", "#56AD2D"),
    ("의정부", "의정부경전철", "#FDA600"), ("GTX", "GTX-A", "#9A6292"),
    ("1호선", "1호선", "#0052A4"), ("2호선", "2호선", "#00A84D"), ("3호선", "3호선", "#EF7C1C"),
    ("4호선", "4호선", "#00A5DE"), ("5호선", "5호선", "#996CAC"), ("6호선", "6호선", "#CD7C2F"),
    ("7호선", "7호선", "#747F00"), ("8호선", "8호선", "#E6186C"), ("9호선", "9호선", "#BDB092"),
]


def line_name(raw: str) -> str:
    """'01호선' -> '1호선', '경의중앙선' -> '경의중앙선'."""
    s = raw.strip()
    m = re.fullmatch(r"0*(\d)호선", s)
    if m:
        return f"{m.group(1)}호선"
    for key, name, _ in _LINES:
        if key in s and "호선" not in key:
            return name
    return s


def line_color(name: str) -> str:
    for key, disp, color in _LINES:
        if key in name or disp == name:
            return color
    return "#8A8F98"


def line_sort_key(name: str):
    m = re.fullmatch(r"(\d)호선", name)
    return (0, int(m.group(1))) if m else (1, name)


# 실시간 API 의 subwayId -> 노선 이름
SUBWAY_ID = {
    "1001": "1호선", "1002": "2호선", "1003": "3호선", "1004": "4호선", "1005": "5호선",
    "1006": "6호선", "1007": "7호선", "1008": "8호선", "1009": "9호선",
    "1061": "경의중앙선", "1063": "경의중앙선", "1065": "공항철도", "1067": "경춘선",
    "1075": "수인분당선", "1077": "신분당선", "1081": "경강선", "1092": "우이신설선",
    "1093": "서해선", "1094": "신림선", "1032": "GTX-A",
}


# ---------------------------------------------------------------- 자료형

@dataclass
class Node:
    id: str  # STATION_CD
    name: str  # 보여줄 역 이름
    line: str  # 보여줄 노선 이름
    lat: float | None
    lon: float | None
    fr: str = ""


@dataclass
class Network:
    nodes: dict[str, Node]
    adj: dict[str, dict[str, float]]  # node -> {node: 분}
    lines: dict[str, list[tuple[str, str]]]  # 노선 -> 역 사이 연결 목록
    by_name: dict[str, list[str]] = field(default_factory=dict)  # 역 이름 -> node id들

    def station_names(self) -> list[str]:
        return sorted(self.by_name)

    def lines_at(self, name: str) -> list[str]:
        return sorted({self.nodes[i].line for i in self.by_name.get(name, [])}, key=line_sort_key)


@dataclass
class Leg:
    line: str
    stations: list[str]  # 타는 역 ~ 내리는 역
    minutes: float
    ids: list[str] = field(default_factory=list)  # 같은 순서의 node id
    toward: str = ""  # '합정 방면'
    loop: str = ""  # 2호선 '내선순환' / '외선순환'
    ends: list[str] = field(default_factory=list)  # 이 방향 종점(갈래가 있으면 여럿)

    @property
    def heading(self) -> str:
        """'합정 방면 · 내선순환' / '구로 방면 · 인천·신창 방향'."""
        parts = [self.toward] if self.toward else []
        if self.loop:
            parts.append(self.loop)
        elif self.ends:
            parts.append("·".join(self.ends[:2]) + (" 등" if len(self.ends) > 2 else "") + " 방향")
        return " · ".join(parts)


@dataclass
class Route:
    legs: list[Leg]
    minutes: float
    path: list[str] = field(default_factory=list)  # 지나는 node id (지도에 그릴 때)

    @property
    def transfers(self) -> int:
        return max(len(self.legs) - 1, 0)

    @property
    def stops(self) -> int:
        return sum(len(l.stations) - 1 for l in self.legs)


# ---------------------------------------------------------------- 만들기

def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def clean_name(name: str) -> str:
    """'서울역' -> '서울', '총신대입구(이수)' -> '총신대입구(이수)' (괄호는 남긴다)."""
    s = name.strip()
    if s.endswith("역") and len(s) > 2 and s not in ("역삼",):
        s = s[:-1]
    return s


def base_name(name: str) -> str:
    """환승 판단용: 괄호 부분을 뺀다."""
    return re.sub(r"\(.*?\)", "", name).strip()


_FR = re.compile(r"^([A-Z]*)(\d+)(?:-(\d+))?$")


def parse_fr(fr: str):
    """'P549' -> ('P', 549, None), '211-1' -> ('', 211, 1)."""
    m = _FR.match(fr.strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None


def _hop_minutes(a: Node, b: Node) -> float:
    if a.lat is None or b.lat is None:
        return 2.0
    km = haversine_km((a.lat, a.lon), (b.lat, b.lon))
    return max(km / KMH * 60, 1.0) + DWELL_MIN


def _dist(a: Node, b: Node) -> float:
    if a.lat is None or b.lat is None:
        return 99.0
    return haversine_km((a.lat, a.lon), (b.lat, b.lon))


def _near(a: Node, b: Node, limit: float) -> bool:
    """좌표를 모르면 코드 순서를 믿는다."""
    if a.lat is None or b.lat is None:
        return True
    return _dist(a, b) <= limit


def build_network(master_rows: list[dict], line_rows: list[dict]) -> Network:
    # 좌표: 역 코드로 먼저, 안 되면 (이름, 노선)으로
    coord_by_id: dict[str, tuple[float, float]] = {}
    coord_by_name: dict[tuple[str, str], tuple[float, float]] = {}
    coord_by_base: dict[str, tuple[float, float]] = {}  # 다른 노선의 같은 역 좌표라도 빌려 쓴다
    for r in master_rows:
        try:
            ll = (float(r["LAT"]), float(r["LOT"]))
        except (KeyError, TypeError, ValueError):
            continue
        coord_by_id[str(r.get("BLDN_ID", "")).strip()] = ll
        nm = clean_name(str(r.get("BLDN_NM", "")))
        coord_by_name[(nm, line_name(str(r.get("ROUTE", ""))))] = ll
        coord_by_base.setdefault(base_name(nm), ll)

    nodes: dict[str, Node] = {}
    for r in line_rows:
        sid = str(r.get("STATION_CD", "")).strip()
        if not sid:
            continue
        name = clean_name(str(r.get("STATION_NM", "")))
        line = line_name(str(r.get("LINE_NUM", "")))
        ll = coord_by_id.get(sid) or coord_by_name.get((name, line)) or coord_by_base.get(base_name(name))
        nodes[sid] = Node(sid, name, line, ll[0] if ll else None, ll[1] if ll else None,
                          str(r.get("FR_CODE", "")).strip())

    adj: dict[str, dict[str, float]] = {i: {} for i in nodes}
    lines: dict[str, list[tuple[str, str]]] = {}

    def link(a: str, b: str):
        if a == b or b in adj[a]:
            return
        w = _hop_minutes(nodes[a], nodes[b])
        adj[a][b] = adj[b][a] = w
        lines.setdefault(nodes[a].line, []).append((a, b))

    by_line: dict[str, list[Node]] = {}
    for n in nodes.values():
        by_line.setdefault(n.line, []).append(n)

    for line, members in by_line.items():
        # 1) 코드 묶음 안에서 순서대로
        groups: dict[tuple[str, int | None], list[tuple[int, Node]]] = {}
        code_index: dict[tuple[str, int, int | None], Node] = {}
        for n in members:
            p = parse_fr(n.fr)
            if not p:
                continue
            prefix, num, sub = p
            code_index[(prefix, num, sub)] = n
            key = (prefix, num if sub is not None else None)
            groups.setdefault(key, []).append((sub if sub is not None else num, n))
        for (prefix, branch_of), items in groups.items():
            items.sort(key=lambda x: x[0])
            for (_, a), (_, b) in zip(items, items[1:]):
                if _near(a, b, MAX_LINK_KM):
                    link(a.id, b.id)
            first = items[0][1]
            # 2) 가지 묶음은 분기역에 붙인다
            if branch_of is not None:  # 211-1 -> 211
                root = code_index.get((prefix, branch_of, None))
                if root and _near(root, first, MAX_LINK_KM):
                    link(root.id, first.id)
            elif prefix:  # P549 -> 548 (접두어 없는 바로 앞 번호)
                p = parse_fr(first.fr)
                root = code_index.get(("", p[1] - 1, None)) if p else None
                if root and _near(root, first, MAX_LINK_KM):
                    link(root.id, first.id)
        # 4) 2호선 순환
        if line == "2호선":
            main = sorted((p[1], n) for n in members if (p := parse_fr(n.fr)) and not p[0] and p[2] is None)
            if len(main) > 2:
                lo, hi = main[0][1], main[-1][1]
                if _near(lo, hi, 3):
                    link(lo.id, hi.id)
        # 3) 남은 조각은 가장 가까운 역끼리
        _join_components(members, adj, link)

    # 환승
    by_name: dict[str, list[str]] = {}
    for n in nodes.values():
        by_name.setdefault(n.name, []).append(n.id)
    by_base: dict[str, list[str]] = {}
    for n in nodes.values():
        by_base.setdefault(base_name(n.name), []).append(n.id)
    for ids in by_base.values():
        for i in ids:
            for j in ids:
                if i < j and nodes[i].line != nodes[j].line:
                    adj[i][j] = adj[j][i] = TRANSFER_MIN
    located = [n for n in nodes.values() if n.lat is not None]
    located.sort(key=lambda n: n.lat)
    for k, a in enumerate(located):  # 위도로 정렬해 가까운 것만 비교
        for b in located[k + 1:]:
            if b.lat - a.lat > 0.003:
                break
            if a.line != b.line and b.id not in adj[a.id] and _dist(a, b) <= TRANSFER_KM:
                adj[a.id][b.id] = adj[b.id][a.id] = TRANSFER_MIN
    net = Network(nodes, adj, lines, by_name)
    apply_fixes(net, LINK_FIXES)
    return net


# 코드 순서·거리로 추정한 연결 중 실제와 다른 곳 (2026-10 서울시 데이터로 확인)
LINK_FIXES: dict[str, dict[str, list[tuple[str, str]]]] = {
    # 응암 순환: 응암 -> 역촌 -> 불광 -> 독바위 -> 연신내 -> 구산 -> 응암, 응암 - 새절
    "6호선": {"remove": [("구산", "새절")], "add": [("구산", "응암"), ("응암", "새절")]},
    # 삼성역 구간 미개통: 운정중앙~서울역, 수서~동탄 따로 운행
    "GTX-A": {"remove": [("서울", "수서")]},
    # 용산 - 효창공원앞 - 공덕 - 서강대 / 서울역 - 신촌 - 가좌 (추정 연결이 좌표에 따라 달라져서 넉넉히 적어 둠)
    "경의중앙선": {"remove": [("공덕", "이촌"), ("공덕", "용산"), ("신촌", "서강대")],
               "add": [("용산", "효창공원앞"), ("효창공원앞", "공덕"), ("신촌", "가좌")]},
    "경춘선": {"remove": [("광운대", "청량리")], "add": [("광운대", "상봉")]},
    # 서울역 - 공덕 - 홍대입구 - 디지털미디어시티 - 마곡나루 - 김포공항 - 계양 - 검암 - 청라국제도시 - 영종 - 운서 - ...
    "공항철도": {"remove": [("디지털미디어시티", "김포공항"), ("운서", "검암")],
             "add": [("검암", "청라국제도시"), ("디지털미디어시티", "마곡나루")]},
}


def apply_fixes(net: Network, fixes) -> None:
    for line, fx in fixes.items():
        ids = {n.name: i for i, n in net.nodes.items() if n.line == line}
        if not ids:
            continue
        edges = net.lines.setdefault(line, [])
        for a, b in fx.get("remove", []):
            ia, ib = ids.get(a), ids.get(b)
            if ia is None or ib is None or ib not in net.adj[ia]:
                continue  # 이미 없으면 그대로
            del net.adj[ia][ib], net.adj[ib][ia]
            edges[:] = [e for e in edges if set(e) != {ia, ib}]
        for a, b in fx.get("add", []):
            ia, ib = ids.get(a), ids.get(b)
            if ia is None or ib is None:
                log.warning("연결 보정: %s %s 또는 %s 역 없음", line, a, b)
                continue
            if ib in net.adj[ia]:
                continue
            w = _hop_minutes(net.nodes[ia], net.nodes[ib])
            net.adj[ia][ib] = net.adj[ib][ia] = w
            edges.append((ia, ib))


def _join_components(members: list[Node], adj, link):
    ids = {n.id for n in members}
    seen: set[str] = set()
    comps: list[list[Node]] = []
    lookup = {n.id: n for n in members}
    for n in members:
        if n.id in seen:
            continue
        stack, comp = [n.id], []
        seen.add(n.id)
        while stack:
            cur = stack.pop()
            comp.append(lookup[cur])
            for nb in adj[cur]:
                if nb in ids and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    # 큰 조각부터 하나씩 가장 가까운 쌍으로 붙인다
    comps.sort(key=len, reverse=True)
    joined = comps[0] if comps else []
    for comp in comps[1:]:
        best = min(((_dist(a, b), a, b) for a in comp for b in joined), key=lambda x: x[0], default=None)
        if best and best[0] < 99:
            link(best[1].id, best[2].id)
        joined = joined + comp


# ---------------------------------------------------------------- 경로

def find_route(net: Network, src: str, dst: str) -> Route | None:
    """역 이름으로 최단 시간 경로. 같은 이름이 여러 노선에 있으면 아무 노선에서나 출발/도착."""
    if src not in net.by_name or dst not in net.by_name or src == dst:
        return None
    targets = set(net.by_name[dst])
    dist: dict[str, float] = {}
    prev: dict[str, str] = {}
    pq = []
    for s in net.by_name[src]:
        dist[s] = 0.0
        heapq.heappush(pq, (0.0, s))
    end = None
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18):
            continue
        if u in targets:
            end = u
            break
        for v, w in net.adj[u].items():
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if end is None:
        return None
    path = [end]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    # 출발/도착역 안에서의 환승(같은 역 다른 노선)은 빼고 시작
    while len(path) > 1 and net.nodes[path[1]].name == src:
        path.pop(0)
    while len(path) > 1 and net.nodes[path[-2]].name == dst:
        path.pop()

    legs: list[Leg] = []
    for a, b in zip(path, path[1:]):
        na, nb = net.nodes[a], net.nodes[b]
        if na.line != nb.line:  # 환승
            continue
        w = net.adj[a][b]
        # 2호선 지선 <-> 본선(순환)은 같은 노선이어도 열차를 갈아타야 한다 (성수·신도림)
        kind = "loop" if (_on_loop(net, a) and _on_loop(net, b)) else "line"
        same_train = not legs or getattr(legs[-1], "_kind", kind) == kind or na.line != "2호선"
        if legs and legs[-1].line == na.line and legs[-1].stations[-1] == na.name and same_train:
            legs[-1].stations.append(nb.name)
            legs[-1].ids.append(b)
            legs[-1].minutes += w
        else:
            legs.append(Leg(na.line, [na.name, nb.name], w, [a, b]))
        legs[-1]._kind = kind
    if not legs:
        return None
    for leg in legs:
        set_heading(net, leg)
    total = sum(l.minutes for l in legs) + TRANSFER_MIN * (len(legs) - 1)
    return Route(legs, total, path)


def _line_neighbors(net: Network, i: str) -> list[str]:
    line = net.nodes[i].line
    return [j for j in net.adj[i] if net.nodes[j].line == line]


def _on_loop(net: Network, i: str) -> bool:
    p = parse_fr(net.nodes[i].fr)
    return net.nodes[i].line == "2호선" and bool(p) and not p[0] and p[2] is None and 201 <= p[1] <= 243


def line_ends(net: Network, prev: str, cur: str, limit: int = 400) -> list[str]:
    """prev -> cur 방향으로 계속 갔을 때 닿는 종점들 (갈래가 있으면 여럿, 순환이면 없음).
    2호선 지선에서 본선(순환)에 닿으면 그 분기역을 종점으로 본다 (지선 열차는 거기서 끝남)."""
    ends: list[str] = []
    seen = {prev}
    stack = [(prev, cur)]
    while stack and limit > 0:
        limit -= 1
        p, c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        nxt = [j for j in _line_neighbors(net, c) if j != p]
        if _on_loop(net, c) and not _on_loop(net, p):
            nxt = []
        if not nxt:
            ends.append(net.nodes[c].name)
        stack.extend((c, j) for j in nxt)
    return list(dict.fromkeys(ends))


def loop_direction(net: Network, a: str, b: str) -> str:
    """2호선 본선에서 a -> b 가 내선(시계 방향, 시청->을지로입구)인지 외선인지."""
    na, nb = net.nodes[a], net.nodes[b]
    if na.line != "2호선":
        return ""
    pa, pb = parse_fr(na.fr), parse_fr(nb.fr)
    if not pa or not pb or pa[0] or pb[0] or pa[2] is not None or pb[2] is not None:
        return ""  # 성수·신정 지선
    if not (201 <= pa[1] <= 243 and 201 <= pb[1] <= 243):
        return ""
    return "내선순환" if (pb[1] - pa[1]) % 43 <= 21 else "외선순환"


def set_heading(net: Network, leg: Leg) -> None:
    if len(leg.ids) < 2:
        return
    leg.toward = f"{leg.stations[1]} 방면"
    leg.loop = loop_direction(net, leg.ids[0], leg.ids[1])
    if not leg.loop:
        ends = line_ends(net, leg.ids[-2], leg.ids[-1]) or [leg.stations[-1]]
        if ends != [leg.stations[1]]:  # '남부터미널 방면 · 남부터미널 방향' 처럼 겹치면 생략
            leg.ends = ends


def describe(net: Network) -> str:
    """불러온 데이터 요약 (로그용)."""
    located = sum(1 for n in net.nodes.values() if n.lat is not None)
    parts = []
    for line in sorted(net.lines, key=line_sort_key):
        ids = {i for e in net.lines[line] for i in e}
        parts.append(f"{line} {len(ids)}역")
    return f"역 {len(net.nodes)}개(좌표 {located}) · 노선 {len(net.lines)}개: " + ", ".join(parts)


# ---------------------------------------------------------------- 불러오기

class SubwayData:
    """API 키로 한 번 받아서 메모리에 둔다. 키를 여러 개 주면 되는 것을 쓴다."""

    def __init__(self, keys):
        if isinstance(keys, str) or keys is None:
            keys = [keys]
        self.keys = [k for k in dict.fromkeys(keys) if k]
        self.net: Network | None = None
        self.error: str | None = None

    @property
    def key(self) -> str | None:
        return self.keys[0] if self.keys else None

    @property
    def ready(self) -> bool:
        return self.net is not None

    async def load(self, session) -> None:
        if not self.keys:
            self.error = "서울시 API 키(SEOUL_API_KEY)가 아직 없어요."
            return
        for n, key in enumerate(self.keys, 1):
            try:
                master = await _fetch_rows(session, key, "subwayStationMaster")
                info = await _fetch_rows(session, key, "SearchSTNBySubwayLineInfo")
            except Exception as e:
                log.warning("지하철 데이터: %d번째 키로 실패 (%r)", n, e)
                continue
            self.net = build_network(master, info)
            self.error = None
            log.info("지하철 데이터(%d번째 키): %s", n, describe(self.net))
            missing = sorted(f"{x.name}({x.line})" for x in self.net.nodes.values() if x.lat is None)
            log.info("좌표 없는 역 %d개: %s", len(missing), ", ".join(missing))
            for line in sorted(self.net.lines, key=line_sort_key):  # 연결 확인용
                pairs = ", ".join(f"{self.net.nodes[a].name}-{self.net.nodes[b].name}"
                                  for a, b in self.net.lines[line])
                log.info("연결 %s: %s", line, pairs[:3500])
            return
        self.error = "지하철 데이터를 받아오지 못했어요. 서울시 일반 인증키가 맞는지 확인해 주세요."


async def _fetch_rows(session, key: str, service: str) -> list[dict]:
    rows: list[dict] = []
    start = 1
    while True:
        url = f"http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{start + 999}/"
        async with session.get(url) as r:
            data = await r.json(content_type=None)
        body = data.get(service)
        if not body:
            raise RuntimeError(f"{service} 응답 이상: {str(data)[:200]}")
        rows.extend(body.get("row", []))
        total = int(body.get("list_total_count", 0))
        start += 1000
        if start > total:
            return rows


# ---------------------------------------------------------------- 실시간 도착

# 실시간 API 에서 이름이 다르게 등록된 역
REALTIME_ALIAS = {
    "응암": "응암순환(상선)", "공릉": "공릉(서울산업대입구)", "남한산성입구": "남한산성입구(성남법원, 검찰청)",
    "대모산입구": "대모산", "천호": "천호(풍납토성)", "몽촌토성": "몽촌토성(평화의문)",
}


@dataclass
class Arrival:
    line: str
    direction: str  # 상행/하행/내선/외선
    heading: str  # 'OO행 - XX방면'
    message: str  # '3분 후 (OO)' / '전역 도착'
    seconds: int
    express: bool
    when: str = ""  # '3분 후' / '전역 도착' (전광판용 짧은 글자)
    where: str = ""  # 지금 열차가 있는 역
    code: str = ""  # arvlCd: 0 진입, 1 도착, 2 출발, 3 전역출발, 4 전역진입, 5 전역도착, 99 운행중
    train_no: str = ""

    def eta(self) -> int | None:
        """이 역까지 대략 몇 초 남았는지. 모르면 None, 이미 떠났으면 -1."""
        if self.seconds > 0:
            return self.seconds
        if self.code == "2":
            return -1
        if self.code in ("0", "1"):
            return 0
        if self.code in ("3", "4", "5"):
            return {"3": 90, "4": 100, "5": 120}[self.code]
        m = re.search(r"\[(\d+)\]번째 전역", self.message)
        if m:
            return int(m.group(1)) * 120
        m = re.search(r"(\d+)분", self.message)
        if m:
            return int(m.group(1)) * 60
        return None


def realtime_code(data: dict) -> str:
    """실시간 API 결과 코드. INFO-000 정상, INFO-200 데이터 없음, 그 밖은 키 오류 등."""
    return str(data.get("code") or (data.get("errorMessage") or {}).get("code") or "")


def parse_arrivals(data: dict) -> list[Arrival]:
    out = []
    for r in data.get("realtimeArrivalList") or []:
        try:
            secs = int(r.get("barvlDt") or 0)
        except ValueError:
            secs = 0
        line = SUBWAY_ID.get(str(r.get("subwayId")), str(r.get("subwayId")))
        msg = str(r.get("arvlMsg2") or "").strip()
        where = str(r.get("arvlMsg3") or "").strip()
        when = msg
        if secs > 0:
            m, s = divmod(secs, 60)
            msg = f"{m}분 {s}초 후" if m else f"{s}초 후"
            when = f"{m + (1 if s >= 30 else 0)}분 후" if m else "곧 도착"
            if where:
                msg += f" ({where})"
        out.append(Arrival(line, str(r.get("updnLine") or ""), str(r.get("trainLineNm") or ""),
                           msg, secs, str(r.get("btrainSttus") or "") in ("급행", "ITX", "특급"), when, where,
                           str(r.get("arvlCd") or ""), str(r.get("btrainNo") or "")))
    out.sort(key=lambda a: (line_sort_key(a.line), a.direction, a.seconds))
    return out
