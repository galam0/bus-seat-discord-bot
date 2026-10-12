"""지하철 경로 / 노선도 이미지. 공식 노선도를 따라 그리지 않고, 역 좌표로 직접 그린다."""
from io import BytesIO
from math import cos, radians

from images import BG, CARD, DIM, LINE, SUB, TEXT, _Canvas
from subway import TRANSFER_MIN, Network, Route, line_color, line_sort_key


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c, base=BG, t=0.25):
    """c 를 base 쪽으로 흐리게 (t = c 의 비율)."""
    return tuple(int(a * t + b * (1 - t)) for a, b in zip(c, base))


def _badge(c: _Canvas, x, y, text, color, size=14):
    w = c.text_w(text, size, True) + 20
    c.rect(x, y, x + w, y + size + 14, fill=color, r=(size + 14) / 2)
    c.text(x + w / 2, y + (size + 14) / 2, text, size, (255, 255, 255), True, anchor="mm")
    return w


def _minutes_text(m: float) -> str:
    m = round(m)
    if m < 60:
        return f"{m}분"
    h, r = divmod(m, 60)
    return f"{h}시간" if r == 0 else f"{h}시간 {r}분"


# ---------------------------------------------------------------- 경로

ROW = 34
MAX_MIDDLE = 5  # 중간역이 이보다 많으면 접어서 보여준다


def _leg_rows(stations: list[str]):
    """[(종류, 글자)] 종류: board / stop / fold / alight."""
    mid = stations[1:-1]
    rows = [("board", stations[0])]
    if len(mid) > MAX_MIDDLE:
        rows += [("stop", s) for s in mid[:2]]
        rows.append(("fold", f"{len(mid) - 3}개 역"))
        rows.append(("stop", mid[-1]))
    else:
        rows += [("stop", s) for s in mid]
    rows.append(("alight", stations[-1]))
    return rows


def render_route(route: Route, src: str, dst: str) -> BytesIO:
    W = 620
    head_h = 108
    body = []
    for i, leg in enumerate(route.legs):
        if i:
            same = route.legs[i - 1].line == leg.line
            body.append(("transfer", "열차 갈아타기 (같은 노선)" if same else "환승"))
        body.append(("badge", leg))
        if leg.heading:
            body.append(("heading", leg))
        body += [(k, (t, leg)) for k, t in _leg_rows(leg.stations)]
    sizes = {"badge": 48, "transfer": 48, "heading": 46}
    H = head_h + sum(sizes.get(k, ROW) for k, _ in body) + 60

    c = _Canvas(W, H)
    c.rect(8, 8, W - 8, H - 8, fill=CARD, r=16)
    c.text(32, 26, f"{src} → {dst}", 26, bold=True)
    c.text(32, 64, f"약 {_minutes_text(route.minutes)} · 환승 {route.transfers}회 · {route.stops}개 역 이동",
           16, SUB)

    x_bar = 64
    y = head_h
    for kind, data in body:
        if kind == "transfer":
            for yy in range(int(y + 6), int(y + 42), 8):
                c.line([(x_bar, yy), (x_bar, yy + 4)], DIM, 3)
            c.text(x_bar + 26, y + 24, f"{data} · 걷기·기다림 약 {TRANSFER_MIN:.0f}분", 14, SUB, anchor="lm")
            y += 48
            continue
        if kind == "heading":
            leg = data
            color = _rgb(line_color(leg.line))
            text = c.fit(f"▶ {leg.heading} 열차", 16, W - x_bar - 80, True)
            w = c.text_w(text, 16, True) + 28
            c.line([(x_bar, y), (x_bar, y + 46)], _mix(color, CARD, 0.35), 8)
            c.rect(x_bar + 18, y + 6, x_bar + 18 + w, y + 40, fill=_mix(color, CARD, 0.22), r=10,
                   outline=color, width=2)
            c.text(x_bar + 32, y + 23, text, 16, TEXT, True, anchor="lm")
            y += 46
            continue
        if kind == "badge":
            leg = data
            color = _rgb(line_color(leg.line))
            w = _badge(c, x_bar - 14, y + 10, leg.line, color)
            c.text(x_bar - 14 + w + 12, y + 24,
                   f"{len(leg.stations) - 1}개 역 · 약 {_minutes_text(leg.minutes)}", 14, SUB, anchor="lm")
            y += 48
            continue
        text, leg = data
        color = _rgb(line_color(leg.line))
        cy = y + ROW / 2
        top = y if kind != "board" else cy
        bottom = y + ROW if kind != "alight" else cy
        c.line([(x_bar, top), (x_bar, bottom)], color, 8)
        if kind == "fold":
            for k in (-7, 0, 7):
                c.circle(x_bar, cy + k, 2, (255, 255, 255))
            c.text(x_bar + 26, cy, text, 14, DIM, anchor="lm")
        elif kind in ("board", "alight"):
            c.circle(x_bar, cy, 10, (255, 255, 255), outline=color, width=4)
            label = "승차" if kind == "board" else "하차"
            c.text(x_bar + 26, cy, text, 19, TEXT, True, anchor="lm")
            c.text(W - 32, cy, label, 13, color, True, anchor="rm")
        else:
            c.circle(x_bar, cy, 5, (255, 255, 255))
            c.text(x_bar + 26, cy, text, 16, SUB, anchor="lm")
        y += ROW

    c.text(32, H - 30, "예상 시간은 역 사이 거리로 계산한 대략값이에요 · 데이터: 서울특별시", 12, DIM, anchor="lm")
    return c.png()


# ---------------------------------------------------------------- 노선도

SEOUL_BOUNDS = (37.43, 37.70, 126.80, 127.18)  # (위도 min, max, 경도 min, max)


class _Proj:
    def __init__(self, bounds, W, H, pad):
        self.la0, self.la1, self.lo0, self.lo1 = bounds
        self.k = cos(radians((self.la0 + self.la1) / 2))
        self.W, self.H, self.pad = W, H, pad

    def __call__(self, lat, lon):
        x = self.pad + (lon - self.lo0) * self.k / ((self.lo1 - self.lo0) * self.k) * (self.W - 2 * self.pad)
        y = self.pad + (self.la1 - lat) / (self.la1 - self.la0) * (self.H - 2 * self.pad)
        return x, y


def _bounds_of(points, margin=0.08):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    la0, la1, lo0, lo1 = min(lats), max(lats), min(lons), max(lons)
    dla, dlo = max(la1 - la0, 0.02), max(lo1 - lo0, 0.02)
    return (la0 - dla * margin, la1 + dla * margin, lo0 - dlo * margin, lo1 + dlo * margin)


def _size_for(bounds, W=1000, min_h=560, max_h=900):
    la0, la1, lo0, lo1 = bounds
    k = cos(radians((la0 + la1) / 2))
    ratio = (la1 - la0) / ((lo1 - lo0) * k)
    return W, int(min(max(W * ratio, min_h), max_h))


def _expand_to_aspect(bounds, W, H):
    """그림 비율에 맞게 범위를 넓혀 지도가 찌그러지지 않게."""
    la0, la1, lo0, lo1 = bounds
    k = cos(radians((la0 + la1) / 2))
    want = H / W
    have = (la1 - la0) / ((lo1 - lo0) * k)
    if have < want:  # 위아래를 넓힌다
        extra = ((lo1 - lo0) * k * want - (la1 - la0)) / 2
        la0, la1 = la0 - extra, la1 + extra
    else:
        extra = ((la1 - la0) / want / k - (lo1 - lo0)) / 2
        lo0, lo1 = lo0 - extra, lo1 + extra
    return la0, la1, lo0, lo1


def render_map(net: Network, *, line: str | None = None, route: Route | None = None,
               title: str = "") -> BytesIO:
    nodes = net.nodes
    located = {i: n for i, n in nodes.items() if n.lat is not None}

    if route is not None:
        focus_ids = [i for i in route.path if i in located]
    elif line is not None:
        focus_ids = [i for i, n in located.items() if n.line == line]
    else:
        focus_ids = []
    if focus_ids:
        bounds = _bounds_of([(located[i].lat, located[i].lon) for i in focus_ids], 0.12)
    else:
        bounds = SEOUL_BOUNDS

    head_h = 84
    W, H = _size_for(bounds)
    bounds = _expand_to_aspect(bounds, W, H)
    c = _Canvas(W, H + head_h + 60)
    c.rect(0, 0, c.w, c.h, fill=CARD)
    proj = _Proj(bounds, W, H, 30)

    def pt(i):
        n = located[i]
        x, y = proj(n.lat, n.lon)
        return x, y + head_h

    def visible(i):
        x, y = pt(i)
        return -50 <= x <= W + 50 and head_h - 50 <= y <= head_h + H + 50

    highlight_line = line
    route_edges = set()
    if route is not None:
        for a, b in zip(route.path, route.path[1:]):
            route_edges.add((a, b))
            route_edges.add((b, a))

    # 노선 선
    order = sorted(net.lines, key=line_sort_key)
    if highlight_line in order:  # 강조 노선을 맨 위에
        order.remove(highlight_line)
        order.append(highlight_line)
    lines_in_view = []
    for ln in order:
        color = _rgb(line_color(ln))
        dim = (route is not None) or (highlight_line is not None and ln != highlight_line)
        drawn = False
        for a, b in net.lines[ln]:
            if a in located and b in located and (visible(a) or visible(b)):
                if (a, b) in route_edges:
                    continue
                c.line([pt(a), pt(b)], _mix(color) if dim else color, 3 if dim else 5)
                drawn = True
        if drawn and not dim:
            lines_in_view.append(ln)
    if route is not None:
        for a, b in zip(route.path, route.path[1:]):
            if a in located and b in located and nodes[a].line == nodes[b].line:
                c.line([pt(a), pt(b)], _rgb(line_color(nodes[a].line)), 8)
        lines_in_view = list(dict.fromkeys(l.line for l in route.legs))

    # 역 점과 이름
    by_name_lines: dict[str, set] = {}
    for n in located.values():
        by_name_lines.setdefault(n.name, set()).add(n.line)

    if route is not None:
        label_ids = [i for i in route.path if i in located]
        key_names = {route.legs[0].stations[0], route.legs[-1].stations[-1]} | {l.stations[0] for l in route.legs}
    elif highlight_line is not None:
        label_ids = focus_ids
        key_names = {n.name for i, n in located.items() if n.line == highlight_line and len(by_name_lines[n.name]) > 1}
    else:
        # 전체 노선도: 3개 노선 이상 환승역만 이름
        label_ids = [i for i, n in located.items() if len(by_name_lines[n.name]) >= 2]
        key_names = {n for n, ls in by_name_lines.items() if len(ls) >= 3}

    placed = []
    # 역 점 자리도 미리 막아 두어 이름이 다른 역 점을 덮지 않게 한다
    for i in label_ids:
        if visible(i):
            x, y = pt(i)
            placed.append((x - 6, y - 6, x + 6, y + 6))
    seen_names = set()
    # 중요한 역 이름부터 자리를 잡는다
    label_ids.sort(key=lambda i: (located[i].name not in key_names, -len(by_name_lines[located[i].name])))
    for i in label_ids:
        n = located[i]
        if not visible(i):
            continue
        x, y = pt(i)
        is_key = n.name in key_names
        is_transfer = len(by_name_lines[n.name]) > 1
        if route is not None or highlight_line is not None or is_transfer:
            r = 6 if is_transfer else 4
            c.circle(x, y, r, (255, 255, 255), outline=(40, 40, 40) if is_transfer else None, width=2)
        if n.name in seen_names:
            continue
        size = 14 if is_key else 11
        if route is None and highlight_line is None and not is_key:
            size = 10
        w = c.text_w(n.name, size, is_key)
        h = size + 4
        for dx, dy, anchor in ((9, 0, "lm"), (-9, 0, "rm"), (0, -12, "mb"), (0, 12, "mt")):
            if anchor == "lm":
                box = (x + dx, y - h / 2, x + dx + w, y + h / 2)
            elif anchor == "rm":
                box = (x + dx - w, y - h / 2, x + dx, y + h / 2)
            elif anchor == "mb":
                box = (x - w / 2, y + dy - h, x + w / 2, y + dy)
            else:
                box = (x - w / 2, y + dy, x + w / 2, y + dy + h)
            if not any(box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1] for p in placed):
                if box[0] < 4 or box[2] > W - 4 or box[1] < head_h or box[3] > head_h + H:
                    continue
                placed.append(box)
                # 글자 테두리 효과
                tx, ty = x + dx, y + dy
                for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    c.text(tx + ox, ty + oy, n.name, size, CARD, is_key, anchor=anchor)
                c.text(tx, ty, n.name, size, TEXT if (is_key or route or highlight_line) else SUB,
                       is_key, anchor=anchor)
                seen_names.add(n.name)
                break

    # 머리말 / 범례
    c.rect(0, 0, c.w, head_h, fill=CARD)
    c.text(28, 18, title or "수도권 지하철 노선도", 24, bold=True)
    c.text(28, 64, "역 좌표로 직접 그린 지도예요 (실제 거리 비율) · 데이터: 서울특별시", 12, DIM, anchor="lm")
    ly = head_h + H + 18
    c.rect(0, head_h + H, c.w, c.h, fill=CARD)
    x = 28
    shown = lines_in_view if (route or highlight_line) else sorted(net.lines, key=line_sort_key)
    for ln in shown:
        w = c.text_w(ln, 12, True) + 30
        if x + w > W - 20:
            break
        c.line([(x, ly + 8), (x + 18, ly + 8)], _rgb(line_color(ln)), 6)
        c.text(x + 24, ly + 8, ln, 12, SUB, True, anchor="lm")
        x += w + 10
    return c.png()
