import discord

from midpoint import Candidate, fmt_minutes
from models import BusTrip, TrainTrip

MAX_ROWS = 25  # 디스코드 선택 메뉴 한도와 맞춤
MEDALS = ("🥇", "🥈", "🥉")


def _bus_seat_text(t: BusTrip) -> str:
    if t.sold_out:
        return "🔴 매진"
    dot = "🟢" if t.seats_left >= 5 else "🟡"
    return f"{dot} {t.seats_left}/{t.seats_total}석"


def bus_embed(dep: str, arr: str, day_label: str, trips: list[BusTrip],
              sources: list[str], missing: list[str] | None = None) -> discord.Embed:
    lines = [
        f"`{t.depart}→{t.arrive}` [{t.kind}] {t.grade} · {_bus_seat_text(t)} · {t.fare:,}원"
        for t in trips[:MAX_ROWS]
    ]
    if len(trips) > MAX_ROWS:
        lines.append(f"…외 {len(trips) - MAX_ROWS}편")
    body = "\n".join(lines) if lines else "남은 편이 없어요."
    counts = " · ".join(
        f"{k} {n}편" for k in ("고속", "시외") if (n := sum(t.kind == k for t in trips))
    )
    head = f"**{day_label}**" + (f"  ({counts})" if counts else "")
    if missing:
        head += f"\n⚠️ {', '.join(missing)} 조회 실패 — 일부 노선이 빠졌을 수 있어요"
    e = discord.Embed(title=f"🚌 {dep} → {arr}", description=f"{head}\n{body}")
    e.set_footer(text=f"출처: {' + '.join(sources)}")
    return e


def train_embed(dep: str, arr: str, day_label: str, trips: list[TrainTrip], source: str) -> discord.Embed:
    lines = [
        f"`{t.depart}→{t.arrive}` {t.name} · 일반 {t.general} · 특실 {t.special}"
        for t in trips[:MAX_ROWS]
    ]
    if len(trips) > MAX_ROWS:
        lines.append(f"…외 {len(trips) - MAX_ROWS}편")
    body = "\n".join(lines) if lines else "남은 편이 없어요."
    e = discord.Embed(title=f"🚆 {dep} → {arr}", description=f"**{day_label}**\n{body}")
    e.set_footer(text=f"출처: {source} · 기차는 잔여 석수 없이 상태만 보여요")
    return e


def midpoint_embed(origins: list[str], cands: list[Candidate]) -> discord.Embed:
    e = discord.Embed(
        title="📍 모이기 좋은 중간 도시",
        description="출발지: " + " · ".join(dict.fromkeys(origins)),
    )
    for medal, c in zip(MEDALS, cands):
        legs = "\n".join(f"{o} → 약 {fmt_minutes(m)} ({km:.0f}km)" for o, km, m in c.legs)
        e.add_field(
            name=f"{medal} {c.city}",
            value=f"{legs}\n가장 먼 사람 기준 약 {fmt_minutes(c.max_min)}",
            inline=False,
        )
    e.set_footer(text="직선거리 기반 추정이에요. 정확한 시간과 잔여석은 /버스로 확인하세요.")
    return e
