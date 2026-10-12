"""여행 일정표 이미지: 날짜별 타임라인."""
from datetime import date, timedelta

from images import CARD, DIM, LINE, SUB, TEXT, _Canvas
from trips import WEEK, Trip

W = 720
PAD = 16
ACCENT = (91, 140, 255)
DAY_COLORS = [(91, 140, 255), (59, 196, 120), (240, 160, 50), (200, 110, 245), (235, 90, 120), (60, 190, 200),
              (230, 200, 60)]
MAX_DAYS = 10
TEXT_SIZE = 16
LINE_H = 24
TIME_W = 64


def _wrap(c: _Canvas, s: str, size: int, max_w: float, max_lines: int = 3) -> list[str]:
    """띄어쓰기 단위로 줄바꿈. 한 단어가 한 줄보다 길면 글자 단위로 자른다."""
    lines: list[str] = []
    cur = ""
    for word in s.split():
        cand = f"{cur} {word}" if cur else word
        if c.text_w(cand, size) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:  # 긴 단어
            if c.text_w(cur + ch, size) > max_w and cur:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [c.fit(lines[max_lines - 1] + " " + lines[max_lines] + "…", size, max_w)]
    return lines


def _days(trip: Trip) -> list[date]:
    s, e = trip.start_date, trip.end_date
    all_days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
    if len(all_days) <= MAX_DAYS:
        return all_days
    with_items = sorted({date.fromisoformat(i.day) for i in trip.items})
    return (with_items or all_days)[:MAX_DAYS]


def render_timeline(trip: Trip, today: date):
    probe = _Canvas(10, 10)
    text_x = PAD + 20 + TIME_W + 26
    text_w = W - PAD - 20 - text_x
    days = _days(trip)
    by_day = {d: [i for i in trip.items if i.day == d.isoformat()] for d in days}
    wrapped = {d: [_wrap(probe, it.text, TEXT_SIZE, text_w) for it in items] for d, items in by_day.items()}

    head_h = 96
    blocks = []
    for d in days:
        rows = wrapped[d]
        body = sum(max(1, len(r)) * LINE_H + 14 for r in rows) if rows else 40
        blocks.append(54 + body + 10)
    h = PAD + head_h + PAD + sum(b + 12 for b in blocks) + PAD - 12
    c = _Canvas(W, h)

    # ---- 머리
    y = PAD
    c.rect(PAD, y, W - PAD, y + head_h, CARD, r=14)
    c.text(PAD + 22, y + 16, c.fit(trip.name, 24, W - 2 * PAD - 160, bold=True), 24, bold=True)
    s, e = trip.start_date, trip.end_date
    f = lambda d: f"{d.month}/{d.day}({WEEK[d.weekday()]})"  # noqa: E731
    period = f(s) if s == e else f"{f(s)} ~ {f(e)}"
    sub = f"{period} · {trip.nights()}" + (f" · {trip.place}" if trip.place else "")
    sub += f" · 참가 {len(trip.members)}명"
    c.text(PAD + 22, y + 58, c.fit(sub, 14, W - 2 * PAD - 160), 14, SUB)
    dday = trip.dday(today)
    bw = c.text_w(dday, 18, True) + 28
    c.rect(W - PAD - 22 - bw, y + 30, W - PAD - 22, y + 66, ACCENT, r=18)
    c.text(W - PAD - 22 - bw / 2, y + 48, dday, 18, (255, 255, 255), bold=True, anchor="mm")

    # ---- 날짜별
    y += head_h + PAD
    for n, (d, bh) in enumerate(zip(days, blocks)):
        color = DAY_COLORS[(d - s).days % len(DAY_COLORS)]
        c.rect(PAD, y, W - PAD, y + bh, CARD, r=14)
        c.rect(PAD, y, PAD + 6, y + bh, color, r=3)
        c.text(PAD + 22, y + 16, f"{(d - s).days + 1}일차", 17, color, bold=True)
        c.text(PAD + 22 + c.text_w(f"{(d - s).days + 1}일차", 17, True) + 10, y + 18,
               f"{d.month}월 {d.day}일 {WEEK[d.weekday()]}요일" + (" · 오늘" if d == today else ""), 15, SUB)
        items = by_day[d]
        ty = y + 54
        if not items:
            c.text(text_x, ty + 4, "아직 일정이 없어요", 15, DIM)
        else:
            line_x = PAD + 20 + TIME_W + 10
            last_dot = None
            for it, rows in zip(items, wrapped[d]):
                rows = rows or [""]
                dot_y = ty + LINE_H / 2
                if last_dot is not None:
                    c.line([(line_x, last_dot + 6), (line_x, dot_y - 6)], LINE, width=2)
                if it.time:
                    c.text(PAD + 20 + TIME_W - 4, ty + 2, it.time, 15, TEXT, bold=True, anchor="ra")
                c.circle(line_x, dot_y, 5, color if it.time else LINE)
                for k, r in enumerate(rows):
                    c.text(text_x, ty + 1 + k * LINE_H, r, TEXT_SIZE, TEXT)
                last_dot = dot_y
                ty += len(rows) * LINE_H + 14
        y += bh + 12
    return c.png()
