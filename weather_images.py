"""날씨 카드 이미지 (Pillow). 아이콘은 글꼴 이모지 대신 도형으로 직접 그린다."""
import math
from datetime import datetime

from images import CARD, DIM, LINE, SUB, TEXT, _Canvas

W = 720
PAD = 16
SUN = (255, 196, 60)
CLOUD = (190, 196, 210)
CLOUD_DARK = (140, 146, 160)
RAIN = (90, 160, 255)
SNOW = (235, 240, 255)
HOT = (255, 120, 90)
COLD = (100, 170, 255)
TEMP_LINE = (255, 170, 80)
WEEK = "월화수목금토일"

# 미세먼지 등급 색 (좋음 / 보통 / 나쁨 / 매우나쁨 / 정보 없음)
DUST_COLORS = {"좋음": (70, 140, 255), "보통": (60, 190, 110), "나쁨": (245, 150, 50),
               "매우나쁨": (235, 70, 70), "정보 없음": (100, 104, 114)}


def _is_night(when: datetime) -> bool:
    return when.hour >= 19 or when.hour < 6


def _sun(c: _Canvas, x, y, r):
    for i in range(8):
        a = math.pi / 4 * i
        c.line([(x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35),
                (x + math.cos(a) * r * 1.75, y + math.sin(a) * r * 1.75)], SUN, width=max(1.5, r / 6))
    c.circle(x, y, r, SUN)


def _moon(c: _Canvas, x, y, r, bg):
    c.circle(x, y, r, SUN)
    c.circle(x + r * 0.45, y - r * 0.35, r * 0.85, bg)


def _cloud(c: _Canvas, x, y, s, color=CLOUD):
    """(x, y) 가 구름 가운데. s 는 대략 반지름."""
    c.circle(x - s * 0.55, y + s * 0.15, s * 0.5, color)
    c.circle(x + s * 0.05, y - s * 0.2, s * 0.65, color)
    c.circle(x + s * 0.6, y + s * 0.15, s * 0.45, color)
    c.rect(x - s * 0.55, y + s * 0.1, x + s * 0.6, y + s * 0.6, color, r=s * 0.25)


def draw_icon(c: _Canvas, x, y, size, text: str, night: bool = False, bg=CARD):
    """size 는 아이콘 전체 폭의 절반쯤. text 는 기상청 하늘 상태 글자."""
    s = size
    if text == "맑음":
        (_moon(c, x, y, s * 0.6, bg) if night else _sun(c, x, y, s * 0.5))
        return
    if text == "구름많음":
        if night:
            _moon(c, x - s * 0.3, y - s * 0.3, s * 0.42, bg)
        else:
            _sun(c, x - s * 0.3, y - s * 0.3, s * 0.36)
        _cloud(c, x + s * 0.1, y + s * 0.1, s * 0.62)
        return
    if text == "흐림":
        _cloud(c, x - s * 0.25, y - s * 0.2, s * 0.5, CLOUD_DARK)
        _cloud(c, x + s * 0.1, y + s * 0.05, s * 0.65)
        return
    # 비·눈·소나기: 구름 아래에 빗줄기/눈송이
    if text == "소나기" and not night:
        _sun(c, x - s * 0.35, y - s * 0.4, s * 0.32)
    _cloud(c, x, y - s * 0.2, s * 0.68, CLOUD_DARK if text != "소나기" else CLOUD)
    drops = (-0.45, 0.0, 0.45)
    for i, dx in enumerate(drops):
        px, py = x + dx * s, y + s * 0.6
        snow = text == "눈" or (text == "비/눈" and i == 1)
        if snow:
            c.circle(px, py + s * 0.12, s * 0.11, SNOW)
        else:
            c.line([(px + s * 0.08, py - s * 0.08), (px - s * 0.08, py + s * 0.3)], RAIN, width=max(1.5, s / 9))


def _temp_color(t: float | None):
    if t is None:
        return SUB
    if t >= 28:
        return HOT
    if t <= 0:
        return COLD
    return TEXT


def render_weather(city: str, now: datetime, slots, daily, dust=None, dust_note: str = ""):
    """slots: weather.Slot 목록 (시간 순), daily: [(라벨, 최저, 최고)],
    dust: dust.Dust 또는 None (dust_note 로 이유 표시)."""
    from dust import _PM10, _PM25, level

    cols = max(1, len(slots))
    hourly_h, daily_h, dust_h = 220, 92, 86
    h = PAD + 128 + PAD + hourly_h + PAD + (daily_h + PAD if daily else 0) + dust_h + PAD
    c = _Canvas(W, h)

    # ---- 머리: 도시 · 지금 날씨
    first = slots[0]
    y = PAD
    c.rect(PAD, y, W - PAD, y + 128, CARD, r=14)
    c.text(PAD + 22, y + 20, f"{city} 날씨", 26, bold=True)
    c.text(PAD + 22, y + 60, f"{now.month}월 {now.day}일 ({WEEK[now.weekday()]}) {now.hour}시 기준", 14, SUB)
    lo_hi = next(((lo, hi) for label, lo, hi in daily if label == "오늘"), None)
    if lo_hi:
        lo, hi = lo_hi
        c.text(PAD + 22, y + 88, "최저 ", 14, SUB)
        x = PAD + 22 + c.text_w("최저 ", 14)
        c.text(x, y + 88, f"{lo:.0f}°" if lo is not None else "-", 14, COLD, bold=True)
        x += c.text_w(f"{lo:.0f}°" if lo is not None else "-", 14, True) + 12
        c.text(x, y + 88, "최고 ", 14, SUB)
        x += c.text_w("최고 ", 14)
        c.text(x, y + 88, f"{hi:.0f}°" if hi is not None else "-", 14, HOT, bold=True)
    temp = f"{first.temp:.0f}°" if first.temp is not None else "-"
    tw = c.text_w(temp, 52, True)
    tx = W - PAD - 26 - tw
    c.text(W - PAD - 26, y + 18, temp, 52, _temp_color(first.temp), bold=True, anchor="ra")
    c.text(W - PAD - 26, y + 92, first.text, 16, SUB, anchor="ra")
    draw_icon(c, tx - 52, y + 62, 40, first.text, _is_night(first.when))

    # ---- 시간별
    y += 128 + PAD
    c.rect(PAD, y, W - PAD, y + hourly_h, CARD, r=14)
    c.text(PAD + 18, y + 12, "3시간 간격 · 아래 % 는 비 올 확률", 13, DIM, bold=True)
    cw = (W - 2 * PAD - 20) / cols
    x0 = PAD + 10
    temps = [s.temp for s in slots if s.temp is not None]
    t_lo, t_hi = (min(temps), max(temps)) if temps else (0, 1)
    span = max(t_hi - t_lo, 4)
    chart_top, chart_bot = y + 134, y + 168
    pts = []
    for i, s in enumerate(slots):
        cx = x0 + cw * (i + 0.5)
        if i:
            c.line([(x0 + cw * i, y + 40), (x0 + cw * i, y + hourly_h - 12)], LINE)
        day = s.when.date() != now.date()
        label = f"{s.when.hour}시"
        c.text(cx, y + 42, label, 14, TEXT if not day else SUB, bold=not day, anchor="mt")
        if day and (i == 0 or slots[i - 1].when.date() != s.when.date()):
            c.text(cx, y + 30, "내일" if (s.when.date() - now.date()).days == 1 else
                   f"{WEEK[s.when.weekday()]}요일", 10, DIM, anchor="mt")
        draw_icon(c, cx, y + 84, 17, s.text, _is_night(s.when))
        if s.temp is not None:
            py = chart_bot - (s.temp - t_lo) / span * (chart_bot - chart_top)
            pts.append((cx, py, s.temp))
        pop = s.pop or 0
        c.text(cx, y + hourly_h - 30, f"{pop}%" if pop else "-", 12, RAIN if pop >= 30 else DIM,
               bold=pop >= 60, anchor="mt")
    if len(pts) > 1:
        c.line([(px, py) for px, py, _ in pts], TEMP_LINE, width=2)
    for px, py, t in pts:
        c.circle(px, py, 3.5, TEMP_LINE)
        c.text(px, py - 8, f"{t:.0f}°", 13, _temp_color(t), bold=True, anchor="mb")

    # ---- 날짜별 최저/최고
    y += hourly_h + PAD
    if daily:
        n = len(daily)
        gap = 10
        bw = (W - 2 * PAD - gap * (n - 1)) / n
        for i, (label, lo, hi) in enumerate(daily):
            bx = PAD + i * (bw + gap)
            c.rect(bx, y, bx + bw, y + daily_h, CARD, r=12)
            c.text(bx + bw / 2, y + 14, label, 14, TEXT, bold=True, anchor="mt")
            lo_t = f"{lo:.0f}°" if lo is not None else "-"
            hi_t = f"{hi:.0f}°" if hi is not None else "-"
            c.text(bx + bw / 2 - 8, y + 46, lo_t, 22, COLD, bold=True, anchor="rt")
            c.text(bx + bw / 2, y + 50, "/", 16, DIM, anchor="mt")
            c.text(bx + bw / 2 + 8, y + 46, hi_t, 22, HOT, bold=True, anchor="lt")
        y += daily_h + PAD

    # ---- 미세먼지
    c.rect(PAD, y, W - PAD, y + dust_h, CARD, r=14)
    if dust is None:
        c.text(PAD + 18, y + 14, "공기", 13, DIM, bold=True)
        c.text(PAD + 18, y + 42, dust_note or "미세먼지 정보를 못 받았어요", 14, SUB)
    else:
        c.text(PAD + 18, y + 14, f"공기 · {dust.area}", 13, DIM, bold=True)
        half = (W - 2 * PAD) / 2
        for i, (name, val, cuts) in enumerate((("미세먼지", dust.pm10, _PM10), ("초미세먼지", dust.pm25, _PM25))):
            grade, _ = level(val, cuts)
            bx = PAD + 18 + i * half
            c.text(bx, y + 46, name, 15, SUB)
            gx = bx + c.text_w(name, 15) + 12
            gw = c.text_w(grade, 15, True) + 22
            c.rect(gx, y + 42, gx + gw, y + 70, DUST_COLORS.get(grade, DIM), r=14)
            c.text(gx + gw / 2, y + 56, grade, 15, (255, 255, 255), bold=True, anchor="mm")
            if val is not None:
                c.text(gx + gw + 10, y + 47, f"{val:.0f}", 15, TEXT, bold=True)
    return c.png()
