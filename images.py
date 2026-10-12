"""편 목록 / 좌석 배치도 이미지 (Pillow). 디스코드 다크 테마에 맞춘 색."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from models import BusTrip
from seatmap import AISLE, seat_rows, seats_per_row

FONT_DIR = Path(__file__).parent / "fonts"
SCALE = 2  # 2배로 그려서 디스코드에서 선명하게

# 색
BG = (35, 37, 42)
CARD = (43, 45, 49)
ROW_ALT = (48, 50, 56)
LINE = (62, 64, 72)
TEXT = (235, 236, 240)
SUB = (160, 163, 172)
DIM = (110, 113, 122)
GREEN = (59, 196, 120)
YELLOW = (240, 185, 50)
RED = (235, 80, 80)
EXPRESS = (88, 130, 255)  # 고속
INTERCITY = (170, 110, 245)  # 시외
SEAT_FREE = (59, 196, 120)
SEAT_TAKEN = (72, 75, 84)


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSansKR-Bold-subset.otf" if bold else "NotoSansKR-Regular-subset.otf"
    return ImageFont.truetype(str(FONT_DIR / name), size * SCALE)


class _Canvas:
    """좌표는 1배 기준으로 받고 SCALE 배로 그린다."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SCALE, h * SCALE), BG)
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x0, y0, x1, y1, fill, r=0, outline=None, width=1):
        box = [x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE]
        self.d.rounded_rectangle(box, r * SCALE, fill=fill, outline=outline, width=width * SCALE)

    def text(self, x, y, s, size, color=TEXT, bold=False, anchor="la"):
        self.d.text((x * SCALE, y * SCALE), s, font=font(size, bold), fill=color, anchor=anchor)

    def text_w(self, s, size, bold=False) -> float:
        return self.d.textlength(s, font=font(size, bold)) / SCALE

    def fit(self, s: str, size: int, max_w: float, bold=False) -> str:
        if self.text_w(s, size, bold) <= max_w:
            return s
        while s and self.text_w(s + "…", size, bold) > max_w:
            s = s[:-1]
        return s + "…"

    def png(self) -> BytesIO:
        buf = BytesIO()
        self.img.save(buf, "PNG", compress_level=6)
        buf.seek(0)
        return buf


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def duration_text(dep: str, arr: str) -> str:
    m = (_minutes(arr) - _minutes(dep)) % (24 * 60)
    h, r = divmod(m, 60)
    if h == 0:
        return f"{r}분"
    return f"{h}시간" if r == 0 else f"{h}시간 {r}분"


def seat_color(t: BusTrip):
    if t.sold_out:
        return RED
    return GREEN if t.seats_left >= 5 else YELLOW


def _badge(c: _Canvas, x, y, kind: str):
    color = EXPRESS if kind == "고속" else INTERCITY
    w = c.text_w(kind, 15, True) + 18
    c.rect(x, y, x + w, y + 26, fill=color, r=13)
    c.text(x + w / 2, y + 13, kind, 15, (255, 255, 255), True, anchor="mm")
    return w


# ---------------------------------------------------------------- 편 목록

W_LIST = 880
ROW_H = 58


def render_schedule(title: str, subtitle: str, trips: list[BusTrip], footer: str = "",
                    notice: str = "") -> BytesIO:
    """한 쪽 분량의 편 목록 이미지."""
    head_h = 92 + (30 if notice else 0)
    body_h = max(len(trips), 1) * ROW_H
    foot_h = 44
    c = _Canvas(W_LIST, head_h + body_h + foot_h + 16)
    c.rect(8, 8, W_LIST - 8, c.h - 8, fill=CARD, r=16)

    c.text(32, 26, title, 26, bold=True)
    c.text(32, 62, subtitle, 16, SUB)
    if notice:
        c.text(32, 90, notice, 15, YELLOW)

    y = head_h
    if not trips:
        c.text(W_LIST / 2, y + ROW_H / 2, "남은 편이 없어요", 18, SUB, anchor="mm")
    for i, t in enumerate(trips):
        if i % 2:
            c.rect(16, y, W_LIST - 16, y + ROW_H, fill=ROW_ALT, r=8)
        cy = y + ROW_H / 2
        # 시간
        c.text(36, cy - 2, t.depart, 24, bold=True, anchor="ls")
        c.text(36, cy + 20, f"→ {t.arrive}", 14, SUB, anchor="ls")
        # 터미널 / 등급·소요시간
        route = t.route or ""
        c.text(140, cy - 2, c.fit(route, 19, 340, True), 19, bold=True, anchor="ls")
        c.text(140, cy + 20, f"{t.grade} · {duration_text(t.depart, t.arrive)}", 14, SUB, anchor="ls")
        # 고속/시외
        _badge(c, 520, cy - 13, t.kind)
        # 잔여석 막대
        bx, bw = 600, 130
        c.rect(bx, cy - 5, bx + bw, cy + 5, fill=LINE, r=5)
        if not t.sold_out:
            fill_w = max(bw * t.seats_left / t.seats_total, 10)
            c.rect(bx, cy - 5, bx + fill_w, cy + 5, fill=seat_color(t), r=5)
        label = "매진" if t.sold_out else f"{t.seats_left}/{t.seats_total}석"
        c.text(bx, cy - 10, label, 14, seat_color(t), True, anchor="ls")
        # 요금
        c.text(W_LIST - 32, cy + 7, f"{t.fare:,}원", 18, bold=True, anchor="rs")
        y += ROW_H

    if footer:
        c.text(32, y + 26, c.fit(footer, 13, W_LIST - 64), 13, DIM, anchor="lm")
    return c.png()


# ---------------------------------------------------------------- 좌석 배치도

SEAT_W, SEAT_H, GAP = 54, 50, 8


def render_seats(title: str, subtitle: str, t: BusTrip) -> BytesIO:
    """버스를 위에서 본 모습. 앞이 왼쪽. 진행 방향 기준 왼쪽(운전석 쪽) 좌석이 아래에 온다."""
    rows = seat_rows(t.seats_total)
    nlanes = 4 if seats_per_row(t.seats_total) == 3 else 5  # 통로 포함 가로 칸 수
    free = set(t.free_seats)

    front_w = 70
    grid_w = len(rows) * SEAT_W + (len(rows) - 1) * GAP
    grid_h = nlanes * SEAT_H + (nlanes - 1) * GAP
    bus_w = front_w + grid_w + 28
    bus_h = grid_h + 36
    pad = 32
    W = max(bus_w + pad * 2, 560)
    head_h = 100
    legend_h = 54
    H = head_h + bus_h + legend_h + 20

    c = _Canvas(W, H)
    c.rect(8, 8, W - 8, H - 8, fill=CARD, r=16)
    c.text(28, 26, c.fit(title, 22, W - 56, True), 22, bold=True)
    c.text(28, 62, c.fit(subtitle, 15, W - 56), 15, SUB)

    bx0 = (W - bus_w) / 2
    by0 = head_h
    c.rect(bx0, by0, bx0 + bus_w, by0 + bus_h, fill=BG, r=30, outline=LINE, width=2)
    gy0 = by0 + 18
    gx0 = bx0 + front_w

    def lane_y(col: int) -> float:  # col 0 = 왼쪽 창가 -> 맨 아래
        return gy0 + (nlanes - 1 - col) * (SEAT_H + GAP)

    # 앞쪽: 문(오른쪽 = 위), 운전석(왼쪽 = 아래)
    fx = bx0 + 14
    c.rect(fx, lane_y(nlanes - 1), fx + 44, lane_y(nlanes - 1) + SEAT_H, fill=None, r=8, outline=LINE, width=2)
    c.text(fx + 22, lane_y(nlanes - 1) + SEAT_H / 2, "문", 13, DIM, True, anchor="mm")
    c.rect(fx, lane_y(0), fx + 44, lane_y(0) + SEAT_H, fill=LINE, r=8)
    c.text(fx + 22, lane_y(0) + SEAT_H / 2, "운전", 12, SUB, True, anchor="mm")
    c.text(fx + 22, gy0 + grid_h / 2, "앞", 15, DIM, True, anchor="mm")

    for i, row in enumerate(rows):
        x = gx0 + i * (SEAT_W + GAP)
        for col, num in row:
            y = lane_y(col)
            is_free = num in free
            c.rect(x, y, x + SEAT_W, y + SEAT_H, fill=SEAT_FREE if is_free else SEAT_TAKEN, r=10)
            # 등받이 (뒤쪽 = 오른쪽)
            c.rect(x + SEAT_W - 9, y + 6, x + SEAT_W - 5, y + SEAT_H - 6,
                   fill=(40, 150, 90) if is_free else (60, 62, 70), r=2)
            c.text(x + SEAT_W / 2 - 2, y + SEAT_H / 2, str(num), 18,
                   (255, 255, 255) if is_free else DIM, True, anchor="mm")

    ly = by0 + bus_h + 34
    lx = W / 2 - 130
    c.rect(lx, ly - 9, lx + 18, ly + 9, fill=SEAT_FREE, r=4)
    c.text(lx + 26, ly, f"빈 자리 {t.seats_left}", 15, TEXT, anchor="lm")
    lx2 = W / 2 + 30
    c.rect(lx2, ly - 9, lx2 + 18, ly + 9, fill=SEAT_TAKEN, r=4)
    c.text(lx2 + 26, ly, f"찬 자리 {t.seats_total - t.seats_left}", 15, SUB, anchor="lm")
    return c.png()
