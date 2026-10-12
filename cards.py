"""여러 명령어의 결과 카드 이미지 (혼잡도·중간도시·기차·도착·막차·주말 날씨)."""
from images import CARD, DIM, LINE, SUB, TEXT, _Canvas
from trip_images import _wrap

W = 720
PAD = 16
WHITE = (255, 255, 255)


def _hex(c: int) -> tuple[int, int, int]:
    return (c >> 16) & 255, (c >> 8) & 255, c & 255


def _mix(a, b, t: float):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _badge(c: _Canvas, x, y, text, size, fill, color=WHITE, anchor="l", h=None):
    """둥근 배지. anchor 'l' 은 x 가 왼쪽 끝, 'r' 은 오른쪽 끝. 너비를 돌려준다."""
    w = c.text_w(text, size, True) + size * 1.3
    h = h or size * 1.9
    x0 = x if anchor == "l" else x - w
    c.rect(x0, y, x0 + w, y + h, fill, r=h / 2)
    c.text(x0 + w / 2, y + h / 2, text, size, color, bold=True, anchor="mm")
    return w


def _star(c: _Canvas, x, y, r, fill):
    import math
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(((x + math.cos(a) * rr) * 2, (y + math.sin(a) * rr) * 2))
    c.d.polygon(pts, fill=fill)


# ---------------------------------------------------------------- 혼잡도

def render_crowd(crowd, levels: dict, order: list[str], fmt_people):
    """crowd: congestion.Crowd. levels: 이름 -> (이모지, 색 int)."""
    def color(level):
        return _hex(levels.get(level, ("", 0x888888))[1])

    probe = _Canvas(10, 10)
    msg = _wrap(probe, crowd.message, 15, W - 2 * PAD - 44, 3) if crowd.message else []
    fc = crowd.forecast[:12]
    head_h = 120 + len(msg) * 23
    chart_h = 270 if fc else 0
    h = PAD + head_h + (PAD + chart_h if fc else 0) + PAD
    c = _Canvas(W, h)

    # ---- 머리
    y = PAD
    c.rect(PAD, y, W - PAD, y + head_h, CARD, r=14)
    c.rect(PAD, y, PAD + 6, y + head_h, color(crowd.level), r=3)
    c.text(PAD + 22, y + 16, c.fit(crowd.area, 24, W - 2 * PAD - 200, True), 24, bold=True)
    _badge(c, W - PAD - 22, y + 18, f"지금 {crowd.level}", 17, color(crowd.level), anchor="r")
    t = crowd.time[11:16] if len(crowd.time) >= 16 else crowd.time
    c.text(PAD + 22, y + 58, f"약 {fmt_people(crowd.low, crowd.high)}", 18, TEXT, bold=True)
    c.text(PAD + 22 + c.text_w(f"약 {fmt_people(crowd.low, crowd.high)}", 18, True) + 12, y + 61,
           f"{t} 기준", 14, SUB)
    for i, line in enumerate(msg):
        c.text(PAD + 22, y + 96 + i * 23, line, 15, SUB)

    # ---- 앞으로 시간별 막대
    if fc:
        y += head_h + PAD
        c.rect(PAD, y, W - PAD, y + chart_h, CARD, r=14)
        c.text(PAD + 18, y + 14, f"앞으로 {len(fc)}시간 예상 인구", 13, DIM, bold=True)
        # 범례
        lx = W - PAD - 18
        for lv in reversed(order):
            tw = c.text_w(lv, 12)
            c.text(lx, y + 15, lv, 12, SUB, anchor="ra")
            c.circle(lx - tw - 9, y + 23, 5, color(lv))
            lx -= tw + 28
        quiet = crowd.quietest()
        top = max(f.high for f in fc) or 1
        x0, x1 = PAD + 22, W - PAD - 22
        base, ceil = y + chart_h - 46, y + 66
        bw = (x1 - x0) / len(fc)
        c.line([(x0, base), (x1, base)], LINE)
        for i, f in enumerate(fc):
            cx = x0 + bw * (i + 0.5)
            hi = base - (base - ceil) * f.high / top
            lo = base - (base - ceil) * f.low / top
            w2 = min(bw * 0.32, 18)
            col = color(f.level)
            c.rect(cx - w2, hi, cx + w2, base, _mix(col, CARD, 0.55), r=4)  # 최대
            c.rect(cx - w2, lo, cx + w2, base, col, r=4)  # 최소
            star = quiet is not None and f is quiet
            c.text(cx, base + 10, f.time, 12, TEXT if star else SUB, bold=star, anchor="mt")
            if star:
                c.text(cx, hi - 6, "가장 한산", 12, color(f.level), bold=True, anchor="mb")
        c.text(x0, y + chart_h - 16, "진한 부분 = 최소 · 연한 부분 = 최대 예상", 11, DIM, anchor="lm")
    return c.png()


# ---------------------------------------------------------------- 중간도시 지도

# 한반도 남쪽 윤곽 (위도, 경도) — 대략적인 해안선
KOREA = [
    (37.75, 126.65), (37.95, 126.68), (38.05, 126.95), (38.28, 127.15), (38.32, 127.55), (38.33, 127.90),
    (38.30, 128.10), (38.55, 128.30), (38.61, 128.36), (38.38, 128.47), (38.21, 128.59), (38.07, 128.66),
    (37.78, 128.95), (37.55, 129.11), (37.43, 129.20), (37.10, 129.37), (36.80, 129.45), (36.40, 129.38),
    (36.05, 129.40), (36.07, 129.57), (35.85, 129.48), (35.55, 129.45), (35.32, 129.30), (35.16, 129.20),
    (35.05, 129.05), (35.08, 128.80), (35.00, 128.60), (34.75, 128.65), (34.83, 128.42), (34.85, 128.10),
    (34.75, 127.75), (34.60, 127.45), (34.53, 127.10), (34.40, 126.70), (34.30, 126.52), (34.55, 126.25),
    (34.80, 126.37), (35.10, 126.30), (35.40, 126.42), (35.60, 126.48), (35.80, 126.55), (35.98, 126.70),
    (36.15, 126.55), (36.35, 126.52), (36.55, 126.30), (36.80, 126.12), (36.95, 126.35), (36.98, 126.75),
    (37.20, 126.65), (37.40, 126.60), (37.58, 126.50), (37.75, 126.45),
]
JEJU = [(33.52, 126.20), (33.56, 126.55), (33.52, 126.90), (33.45, 126.95), (33.25, 126.75), (33.21, 126.30),
        (33.30, 126.16)]
ORIGIN_COLORS = [(91, 140, 255), (240, 120, 90), (60, 190, 120), (200, 120, 240), (240, 190, 60)]
GOLD = (255, 200, 70)


def render_midpoint(origins: list[str], cands, coords: dict, fmt_minutes):
    """origins: 출발 도시들, cands: midpoint.Candidate (city, legs[(o, km, m)], max_min), coords: 도시 -> (위도, 경도)."""
    lat0, lat1, lon0, lon1 = 33.0, 38.75, 125.9, 129.8
    map_h = 560
    map_w = map_h * (lon1 - lon0) / (lat1 - lat0) * 0.8  # 위도 37도 근처 경도 축척
    side_w = W - 2 * PAD - map_w - 12
    h = PAD + map_h + PAD
    c = _Canvas(W, h)
    mx, my = PAD, PAD
    c.rect(mx, my, mx + map_w, my + map_h, (30, 42, 58), r=14)

    def pt(lat, lon):
        return (mx + 12 + (lon - lon0) / (lon1 - lon0) * (map_w - 24),
                my + 12 + (lat1 - lat) / (lat1 - lat0) * (map_h - 24))

    for shape in (KOREA, JEJU):
        poly = [pt(a, b) for a, b in shape]
        c.d.polygon([(x * 2, y * 2) for x, y in poly], fill=(52, 58, 68), outline=(80, 88, 100))

    uniq = list(dict.fromkeys(origins))
    ocol = {o: ORIGIN_COLORS[i % len(ORIGIN_COLORS)] for i, o in enumerate(uniq)}
    best = cands[0] if cands else None
    # 다른 후보 (작게)
    bpt = pt(*coords[best.city]) if best else None
    for cd in cands[1:]:
        x, y = pt(*coords[cd.city])
        c.circle(x, y, 6, (150, 150, 160), outline=CARD, width=2)
        if bpt and abs(x - bpt[0]) < 60 and -20 < y - bpt[1] < 50:
            continue  # 1위 이름표와 겹치면 이름은 오른쪽 목록으로
        c.text(x + 9, y, cd.city, 13, SUB, anchor="lm")
    if best:
        bx, by = pt(*coords[best.city])
        for o, km, m in best.legs:
            if o == best.city:
                continue
            ox, oy = pt(*coords[o])
            c.line([(ox, oy), (bx, by)], ocol.get(o, SUB), width=3)
    for o in uniq:
        x, y = pt(*coords[o])
        c.circle(x, y, 7, ocol[o], outline=WHITE, width=2)
        c.text(x, y - 11, o, 14, WHITE, bold=True, anchor="mb")
    if best:
        c.circle(bx, by, 13, GOLD, outline=WHITE, width=3)
        _star(c, bx, by, 8, (90, 65, 0))
        _badge(c, bx - (c.text_w(best.city, 16, True) + 21) / 2, by + 17, best.city, 16, GOLD, (40, 30, 0))

    # ---- 오른쪽: 순위
    sx = mx + map_w + 12
    y = PAD
    medals = [GOLD, (200, 205, 215), (205, 140, 90)]
    for i, cd in enumerate(cands[:3]):
        legs = [lg for lg in cd.legs]
        bh = 58 + len(legs) * 24
        c.rect(sx, y, W - PAD, y + bh, CARD, r=12)
        c.circle(sx + 24, y + 26, 13, medals[i])
        c.text(sx + 24, y + 26, str(i + 1), 14, (30, 30, 30), bold=True, anchor="mm")
        c.text(sx + 46, y + 14, cd.city, 19, TEXT, bold=True)
        c.text(W - PAD - 14, y + 18, f"최대 {fmt_minutes(cd.max_min)}", 13, SUB, anchor="ra")
        for k, (o, km, m) in enumerate(legs):
            ly = y + 50 + k * 24
            c.circle(sx + 22, ly + 9, 5, ocol.get(o, SUB))
            c.text(sx + 34, ly, c.fit(o, 14, side_w - 150), 14, TEXT)
            c.text(W - PAD - 14, ly, "도착지" if o == cd.city else f"{fmt_minutes(m)} · {km:.0f}km", 14,
                   SUB, anchor="ra")
        y += bh + 10
    c.text(sx + 4, h - PAD - 4, "직선거리로 어림잡은 버스 시간", 11, DIM, anchor="lb")
    return c.png()


# ---------------------------------------------------------------- 기차 시간표

GRADE_COLORS = {"KTX": (30, 90, 200), "KTX-산천": (30, 120, 190), "KTX-이음": (40, 150, 170),
                "KTX-청룡": (60, 80, 180), "ITX-새마을": (200, 60, 70), "ITX-마음": (60, 160, 110),
                "ITX-청춘": (80, 160, 90), "새마을호": (210, 80, 80), "무궁화호": (220, 120, 50),
                "누리로": (150, 110, 200), "SRT": (120, 40, 110)}


def grade_color(grade: str):
    for k in sorted(GRADE_COLORS, key=len, reverse=True):
        if grade.startswith(k):
            return GRADE_COLORS[k]
    return (110, 115, 130)


def _mins(hhmm: str) -> int | None:
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except (ValueError, IndexError):
        return None


def render_trains(title: str, subtitle: str, trips, footer: str = ""):
    """trips: models.TrainTrip 목록 (한 쪽 분량)."""
    row_h = 52
    head_h = 70
    h = PAD + head_h + 8 + max(1, len(trips)) * row_h + 36 + PAD
    c = _Canvas(W, h)
    c.text(PAD + 6, PAD + 4, title, 24, bold=True)
    c.text(PAD + 6, PAD + 40, subtitle, 14, SUB)
    y = PAD + head_h + 8
    durs = [d for t in trips if (d := _dur(t)) is not None]
    longest = max(durs, default=1) or 1
    for i, t in enumerate(trips):
        ry = y + i * row_h
        c.rect(PAD, ry, W - PAD, ry + row_h - 6, CARD, r=10)
        gcol = grade_color(t.grade or t.name)
        c.rect(PAD, ry, PAD + 5, ry + row_h - 6, gcol, r=2)
        mid = ry + (row_h - 6) / 2
        c.text(PAD + 20, mid, t.depart, 20, TEXT, bold=True, anchor="lm")
        c.text(PAD + 84, mid, "→", 15, DIM, anchor="lm")
        c.text(PAD + 106, mid, t.arrive, 17, SUB, anchor="lm")
        # 등급 배지 + 열차 번호
        grade = t.grade or t.name.split(" ")[0]
        no = t.name[len(grade):].strip() if t.name.startswith(grade) else t.name
        bx = PAD + 178
        bw = _badge(c, bx, mid - 13, grade, 13, gcol, h=26)
        c.text(bx + bw + 8, mid, no, 14, SUB, anchor="lm")
        # 소요 시간 막대
        d = _dur(t)
        if d is not None:
            bar_x0, bar_x1 = PAD + 380, PAD + 520
            c.rect(bar_x0, mid - 3, bar_x1, mid + 3, LINE, r=3)
            c.rect(bar_x0, mid - 3, bar_x0 + (bar_x1 - bar_x0) * d / longest, mid + 3, gcol, r=3)
            hh, mm = divmod(d, 60)
            c.text(bar_x0, mid - 8, f"{hh}시간 {mm}분" if hh and mm else (f"{hh}시간" if hh else f"{mm}분"),
                   12, SUB, anchor="lb")
        right = f"{t.fare:,}원" if t.fare else (t.general or "")
        c.text(W - PAD - 16, mid, right, 16, TEXT, bold=bool(t.fare), anchor="rm")
    if not trips:
        c.text(W / 2, y + 20, "남은 편이 없어요", 16, SUB, anchor="mt")
    c.text(PAD + 6, h - PAD - 6, footer, 12, DIM, anchor="lb")
    return c.png()


def _dur(t) -> int | None:
    a, b = _mins(t.depart), _mins(t.arrive)
    if a is None or b is None:
        return None
    return (b - a) % (24 * 60)


# ---------------------------------------------------------------- 실시간 도착 전광판

BOARD_BG = (12, 12, 14)
LED = (255, 160, 40)
LED_DIM = (120, 80, 30)
LED_GREEN = (90, 220, 120)
LED_RED = (255, 80, 70)


def render_board(station: str, groups: list[tuple[str, tuple[int, int, int], str, list[tuple[str, str, str]]]],
                 updated: str):
    """groups: [(노선, 노선색, 방향 이름, [(행선지, 남은 시간 글자, 상태 글자)])]."""
    row_h = 34
    blocks = [44 + max(1, len(rows)) * row_h + 10 for *_, rows in groups]
    h = PAD + 64 + sum(b + 10 for b in blocks) + 6 + PAD
    c = _Canvas(W, h)
    c.rect(PAD, PAD, W - PAD, h - PAD, BOARD_BG, r=14)
    c.text(PAD + 22, PAD + 18, f"{station}", 26, WHITE, bold=True)
    c.text(PAD + 22 + c.text_w(station, 26, True) + 10, PAD + 26, "실시간 도착", 15, (150, 150, 150))
    c.text(W - PAD - 22, PAD + 26, updated, 13, (130, 130, 130), anchor="ra")
    y = PAD + 64
    for (line, lcol, direction, rows), bh in zip(groups, blocks):
        c.rect(PAD + 14, y, W - PAD - 14, y + bh, (24, 24, 28), r=10)
        bw = _badge(c, PAD + 26, y + 9, line, 13, lcol, h=26)
        c.text(PAD + 26 + bw + 10, y + 22, direction, 15, (200, 200, 200), bold=True, anchor="lm")
        ry = y + 44
        if not rows:
            c.text(PAD + 30, ry + row_h / 2, "곧 오는 열차 정보가 없어요", 16, LED_DIM, anchor="lm")
        for dest, when, status in rows:
            mid = ry + row_h / 2
            c.text(PAD + 30, mid, f"{dest}", 19, LED, bold=True, anchor="lm")
            col = LED_RED if "도착" in when or "진입" in when else (LED_GREEN if "출발" in when else LED)
            c.text(W - PAD - 30, mid, when, 19, col, bold=True, anchor="rm")
            if status:
                c.text(PAD + 30 + c.text_w(dest, 19, True) + 12, mid + 1, status, 13, LED_DIM, anchor="lm")
            ry += row_h
        y += bh + 10
    return c.png()


# ---------------------------------------------------------------- 첫차·막차 표

def render_last_train(station: str, day_label: str,
                      rows: list[tuple[str, tuple[int, int, int], str, tuple[str, str] | None, list[tuple[str, str]]]],
                      note: str = ""):
    """rows: [(노선, 노선색, 방향, (첫차 시각, 행선지), [(막차 시각, 행선지)... 진짜 막차가 마지막])]."""
    row_h = 100
    h = PAD + 72 + max(1, len(rows)) * (row_h + 8) + 26 + PAD
    c = _Canvas(W, h)
    c.text(PAD + 6, PAD + 4, f"{station}역 첫차 · 막차", 24, bold=True)
    c.text(PAD + 6, PAD + 40, day_label, 14, SUB)
    y = PAD + 72
    red = (255, 120, 100)
    for line, lcol, direction, first, lasts in rows:
        c.rect(PAD, y, W - PAD, y + row_h, CARD, r=12)
        c.rect(PAD, y, PAD + 6, y + row_h, lcol, r=3)
        bw = _badge(c, PAD + 20, y + 14, line, 12, lcol, h=24)
        c.text(PAD + 20 + bw + 8, y + 26, c.fit(direction, 15, 330, True), 15, TEXT, bold=True, anchor="lm")
        c.text(PAD + 20, y + 58, "첫차", 13, DIM, anchor="lm")
        if first:
            c.text(PAD + 56, y + 58, first[0], 16, TEXT, bold=True, anchor="lm")
            c.text(PAD + 56 + c.text_w(first[0], 16, True) + 8, y + 58, f"{first[1]}행", 14, SUB, anchor="lm")
        x = W - PAD - 18
        if lasts:
            t, dest = lasts[-1]
            c.text(x, y + 28, t, 26, red, bold=True, anchor="rm")
            c.text(x - c.text_w(t, 26, True) - 10, y + 31, "막차", 13, red, bold=True, anchor="rm")
            c.text(x, y + 58, f"{dest}행", 15, TEXT, bold=True, anchor="rm")
            prev = " · ".join(f"{pt} {pd}행" for pt, pd in lasts[:-1])
            if prev:
                c.text(PAD + 20, y + 84, c.fit(f"그 전 열차  {prev}", 13, W - 2 * PAD - 40), 13, SUB, anchor="lm")
        else:
            c.text(x, y + 40, "정보 없음", 15, DIM, anchor="rm")
        y += row_h + 8
    if not rows:
        c.text(W / 2, y + 10, "시간표가 없어요", 16, SUB, anchor="mt")
    c.text(PAD + 6, h - PAD - 2, note, 12, DIM, anchor="lb")
    return c.png()


# ---------------------------------------------------------------- 주말 날씨 비교

def render_weekend(title: str, day_labels: list[str], rows, best: str | None, note: str = ""):
    """rows: [(도시, [weekend.Day 또는 None])]."""
    from weather_images import COLD, HOT, RAIN, draw_icon

    name_w = 120
    row_h = 78
    n = max(1, len(day_labels))
    col_w = (W - 2 * PAD - name_w) / n
    h = PAD + 64 + 34 + len(rows) * (row_h + 6) + 30 + PAD
    c = _Canvas(W, h)
    c.text(PAD + 6, PAD + 4, title, 24, bold=True)
    if best:
        c.text(PAD + 6, PAD + 40, "날씨가 가장 좋은 곳: ", 14, SUB)
        c.text(PAD + 6 + c.text_w("날씨가 가장 좋은 곳: ", 14), PAD + 39, best, 15, GOLD, bold=True)
    y = PAD + 72
    for i, label in enumerate(day_labels):
        cx = PAD + name_w + col_w * (i + 0.5)
        c.text(cx, y + 4, label, 15, TEXT, bold=True, anchor="mt")
    y += 34
    for city, days in rows:
        win = city == best
        c.rect(PAD, y, W - PAD, y + row_h, CARD if not win else (52, 48, 36), r=12,
               outline=GOLD if win else None, width=2 if win else 1)
        c.text(PAD + 18, y + row_h / 2, city, 18, GOLD if win else TEXT, bold=True, anchor="lm")
        for i, d in enumerate(days):
            x0 = PAD + name_w + col_w * i
            if i:
                c.line([(x0, y + 12), (x0, y + row_h - 12)], LINE)
            if d is None:
                c.text(x0 + col_w / 2, y + row_h / 2, "예보 전", 14, DIM, anchor="mm")
                continue
            draw_icon(c, x0 + 34, y + row_h / 2 - 2, 19, d.text, False, bg=CARD if not win else (52, 48, 36))
            tx = x0 + 66
            has_t = d.lo is not None or d.hi is not None
            if has_t:
                c.text(tx, y + 16, d.text, 15, TEXT, bold=True)
            else:
                c.text(tx, y + row_h / 2, d.text, 15, TEXT, bold=True, anchor="lm")
            if has_t:
                lo = f"{d.lo:.0f}°" if d.lo is not None else "-"
                hi = f"{d.hi:.0f}°" if d.hi is not None else "-"
                c.text(tx, y + 44, lo, 14, COLD, bold=True)
                c.text(tx + c.text_w(lo, 14, True) + 4, y + 44, "/", 14, DIM)
                c.text(tx + c.text_w(lo + "/", 14, True) + 8, y + 44, hi, 14, HOT, bold=True)
            if d.pop is not None:
                px = x0 + col_w - 14
                c.text(px, y + 18, f"비 {d.pop}%", 13, RAIN if d.pop >= 30 else SUB, bold=d.pop >= 60, anchor="ra")
        y += row_h + 6
    c.text(PAD + 6, h - PAD - 4, note, 12, DIM, anchor="lb")
    return c.png()
