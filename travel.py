"""여행 명령어: /지하철 /노선도 /도착 /날씨, 그리고 /중간도시 결과의 버스 바로가기 버튼."""
import asyncio
import logging
import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from cities import CITIES
from subway import (REALTIME, REALTIME_ALIAS, SubwayData, find_route, line_color, line_sort_key,
                    parse_arrivals, realtime_code)
from weather import fetch_forecast, pick_slots

log = logging.getLogger("seatbot.travel")
KST = ZoneInfo("Asia/Seoul")
WEEK = "월화수목금토일"

try:
    import subway_images
except Exception:  # pragma: no cover - Pillow 없음
    subway_images = None


def _color_int(hex_color: str) -> int:
    return int(hex_color.lstrip("#"), 16)


def _minutes_text(m: float) -> str:
    m = round(m)
    if m < 60:
        return f"{m}분"
    h, r = divmod(m, 60)
    return f"{h}시간" if r == 0 else f"{h}시간 {r}분"


def route_text(route) -> str:
    """이미지를 못 만들 때 쓰는 글자 경로."""
    lines = []
    for i, leg in enumerate(route.legs):
        if i:
            lines.append(f"　↳ **{leg.stations[0]}** 환승")
        mid = len(leg.stations) - 2
        lines.append(f"**{leg.line}** {leg.stations[0]} → {leg.stations[-1]} ({len(leg.stations) - 1}개 역"
                     + (f", 중간 {mid}개" if mid > 0 else "") + ")")
        if leg.heading:
            lines.append(f"　▶ {leg.heading} 열차")
    return "\n".join(lines)


def match_names(names, current: str, limit: int = 25) -> list[str]:
    cur = current.strip()
    if not cur:
        return list(names)[:limit]
    starts = [n for n in names if n.startswith(cur)]
    contains = [n for n in names if cur in n and not n.startswith(cur)]
    return (starts + contains)[:limit]


class TravelState:
    def __init__(self):
        self.session = None
        # 두 서울시 키를 어느 칸에 넣어도 되게: 되는 키를 찾아 쓴다
        general, metro = os.getenv("SEOUL_API_KEY"), os.getenv("SEOUL_SUBWAY_KEY")
        self.subway = SubwayData([general, metro])
        self.realtime_keys = [k for k in dict.fromkeys([metro, general]) if k]
        self.kma_key = os.getenv("KMA_API_KEY")
        self._loading: asyncio.Task | None = None

    async def start(self):
        import aiohttp

        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self._loading = asyncio.create_task(self.subway.load(self.session))
        asyncio.create_task(self.self_check())

    async def self_check(self):
        """시작할 때 실시간 도착·날씨 키가 되는지 한 번 확인해서 로그에 남긴다 (키 값은 남기지 않음)."""
        await asyncio.sleep(3)
        for n, key in enumerate(self.realtime_keys, 1):
            try:
                async with self.session.get(REALTIME.format(key=key, name=quote("서울"))) as r:
                    data = await r.json(content_type=None)
                code = realtime_code(data)
                log.info("확인 실시간 도착(%d번째 키): %s, %d건", n, code or "?", len(parse_arrivals(data)))
                if code in ("INFO-000", "INFO-200"):
                    if n > 1:
                        self.realtime_keys.insert(0, self.realtime_keys.pop(n - 1))
                    break
            except Exception as e:
                log.warning("확인 실시간 도착(%d번째 키) 실패: %r", n, e)
        # 경로 방향 확인용 (실제 데이터)
        if self._loading:
            await asyncio.wait({self._loading}, timeout=30)
        if self.subway.net is not None:
            for a, b in (("강남", "홍대입구"), ("홍대입구", "강남"), ("서울", "수원"), ("용답", "강남"),
                         ("김포공항", "서울"), ("응암", "공덕")):
                r = find_route(self.subway.net, a, b)
                if r:
                    log.info("확인 경로 %s->%s: %s", a, b,
                             " | ".join(f"{l.line} {l.stations[0]}->{l.stations[-1]} [{l.heading}]" for l in r.legs))
        if self.realtime_keys:
            data = await fetch_realtime("강남")
            if data:
                seen = {(a.line, a.direction, a.heading) for a in parse_arrivals(data)}
                log.info("확인 실시간 강남 방향: %s", "; ".join(f"{l}/{d}/{h}" for l, d, h in sorted(seen))[:800])
        if self.kma_key:
            try:
                lat, lon = CITIES["서울"]
                fc = await fetch_forecast(self.session, self.kma_key, lat, lon, datetime.now(KST))
                log.info("확인 날씨: 정상, 예보 %d칸", len(fc.slots))
            except Exception as e:
                log.warning("확인 날씨 실패: %s", str(e)[:200])

    async def close(self):
        if self.session:
            await self.session.close()

    async def ensure_subway(self) -> str | None:
        """준비됐으면 None, 아니면 사용자에게 보여줄 안내."""
        if self.subway.ready:
            return None
        if self._loading and not self._loading.done():
            await asyncio.wait({self._loading}, timeout=15)
        if self.subway.ready:
            return None
        if self.subway.key and self.session and (self._loading is None or self._loading.done()):
            self._loading = asyncio.create_task(self.subway.load(self.session))  # 다시 시도
        return self.subway.error or "지하철 데이터를 준비하는 중이에요. 잠시 후 다시 시도해 주세요."


state = TravelState()


# ---------------------------------------------------------------- 자동완성

async def station_autocomplete(interaction: discord.Interaction, current: str):
    net = state.subway.net
    if net is None:
        return []
    out = []
    for n in match_names(net.station_names(), current):
        lines = net.lines_at(n)
        label = f"{n} · {', '.join(lines[:4])}" + (" 외" if len(lines) > 4 else "")
        out.append(app_commands.Choice(name=label[:100], value=n))
    return out


async def line_autocomplete(interaction: discord.Interaction, current: str):
    net = state.subway.net
    if net is None:
        return []
    names = sorted(net.lines, key=line_sort_key)
    return [app_commands.Choice(name=n, value=n) for n in match_names(names, current)]


async def city_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=n, value=n) for n in match_names(list(CITIES), current)]


# ---------------------------------------------------------------- 지하철

class RouteView(discord.ui.View):
    def __init__(self, route, src: str, dst: str):
        super().__init__(timeout=600)
        self.route, self.src, self.dst = route, src, dst

    @discord.ui.button(label="지도로 보기", emoji="🗺️", style=discord.ButtonStyle.secondary)
    async def show_map(self, interaction: discord.Interaction, _button):
        await interaction.response.defer(thinking=True)
        net = state.subway.net
        if net is None or subway_images is None:
            await interaction.followup.send("지도를 그릴 수 없어요.", ephemeral=True)
            return
        png = await asyncio.to_thread(subway_images.render_map, net, route=self.route,
                                      title=f"{self.src} → {self.dst}")
        await interaction.followup.send(file=discord.File(png, filename="route_map.png"))

    @discord.ui.button(label="출발역 실시간 도착", emoji="⏱️", style=discord.ButtonStyle.secondary)
    async def arrivals(self, interaction: discord.Interaction, _button):
        await send_arrivals(interaction, self.src, ephemeral=True)


async def subway_route(interaction: discord.Interaction, src: str, dst: str):
    msg = await state.ensure_subway()
    if msg:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        return
    net = state.subway.net
    unknown = [s for s in (src, dst) if s not in net.by_name]
    if unknown:
        await interaction.response.send_message(
            f"모르는 역이에요: {', '.join(unknown)}. 자동완성 목록에서 골라주세요.", ephemeral=True)
        return
    if src == dst:
        await interaction.response.send_message("출발역과 도착역이 같아요.", ephemeral=True)
        return
    route = find_route(net, src, dst)
    if route is None:
        await interaction.response.send_message("경로를 찾지 못했어요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    color = _color_int(line_color(route.legs[0].line))
    head = f"약 {_minutes_text(route.minutes)} · 환승 {route.transfers}회 · {route.stops}개 역"
    first = route.legs[0]
    if first.heading:
        head += f"\n**{first.stations[0]}**에서 **{first.heading}** 열차를 타세요"
    if state.realtime_keys:
        try:
            data = await fetch_realtime(first.stations[0])
            trains = next_trains(parse_arrivals(data), first) if data else []
            if trains:
                head += "\n⏱️ 다음 열차: " + " / ".join(t.message for t in trains)
        except Exception:
            log.exception("경로 실시간 실패")
    embed = discord.Embed(title=f"🚇 {src} → {dst}", description=head, color=color)
    files = []
    if subway_images is not None:
        try:
            png = await asyncio.to_thread(subway_images.render_route, route, src, dst)
            embed.set_image(url="attachment://route.png")
            files = [discord.File(png, filename="route.png")]
        except Exception:
            log.exception("경로 이미지 실패")
    if not files:
        embed.description = head + "\n\n" + route_text(route)
    embed.set_footer(text="예상 시간은 대략값이에요 · 데이터: 서울특별시")
    await interaction.followup.send(embed=embed, files=files, view=RouteView(route, src, dst))


async def subway_map(interaction: discord.Interaction, line: str | None):
    msg = await state.ensure_subway()
    if msg:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        return
    net = state.subway.net
    if line and line not in net.lines:
        await interaction.response.send_message("모르는 노선이에요. 자동완성 목록에서 골라주세요.", ephemeral=True)
        return
    if subway_images is None:
        await interaction.response.send_message("이 서버에서는 지도를 그릴 수 없어요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    title = f"{line} 노선도" if line else "서울 지하철 노선도"
    png = await asyncio.to_thread(subway_images.render_map, net, line=line, title=title)
    note = "" if line else "서울 시내 중심으로 보여줘요. 노선을 고르면 그 노선 전체가 나와요."
    await interaction.followup.send(content=note or None, file=discord.File(png, filename="subway_map.png"))


async def fetch_realtime(station: str) -> dict | None:
    """실시간 도착 원본 응답. 키가 여러 개면 되는 키를 찾아 쓴다. 실패하면 None."""
    name = REALTIME_ALIAS.get(station, station)
    for n, key in enumerate(list(state.realtime_keys), 1):
        url = REALTIME.format(key=key, name=quote(name))
        try:
            async with state.session.get(url) as r:
                data = await r.json(content_type=None)
        except Exception:
            log.exception("실시간 도착 실패")
            continue
        if realtime_code(data) in ("INFO-000", "INFO-200", ""):
            if n > 1:  # 되는 키를 앞으로
                state.realtime_keys.remove(key)
                state.realtime_keys.insert(0, key)
            return data
        log.warning("실시간 도착: %d번째 키 거절 (%s)", n, realtime_code(data))
    return None


def next_trains(arrivals, leg, limit: int = 2) -> list:
    """경로 첫 구간과 같은 노선·같은 방향 열차만."""
    want_next = leg.stations[1] if len(leg.stations) > 1 else ""
    loop = leg.loop[:2]  # '내선' / '외선'
    out = []
    for a in arrivals:
        if a.line != leg.line:
            continue
        ok = f"{want_next}방면" in a.heading.replace(" ", "")
        if loop and a.direction.startswith(loop):
            ok = True
        if ok:
            out.append(a)
    return out[:limit]


async def send_arrivals(interaction: discord.Interaction, station: str, ephemeral: bool = False):
    if not state.realtime_keys:
        await interaction.response.send_message("⚠️ 실시간 지하철 키(SEOUL_SUBWAY_KEY)가 아직 없어요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=ephemeral)
    data = await fetch_realtime(station)
    if data is None:
        await interaction.followup.send("실시간 정보를 받아오지 못했어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)
        return
    arrivals = parse_arrivals(data)
    if not arrivals:
        reason = (data.get("message") or (data.get("errorMessage") or {}).get("message") or "")
        await interaction.followup.send(
            f"**{station}** 도착 정보가 없어요. 서울 밖 역이나 운행이 끝난 시간에는 정보가 없을 수 있어요."
            + (f"\n({reason})" if reason else ""), ephemeral=True)
        return
    embed = discord.Embed(title=f"⏱️ {station}역 실시간 도착",
                          color=_color_int(line_color(arrivals[0].line)))
    groups: dict[str, list] = {}
    for a in arrivals:
        groups.setdefault(a.line, []).append(a)
    for line, items in list(groups.items())[:8]:
        rows, per_dir = [], {}
        for a in items:
            if per_dir.get(a.direction, 0) >= 2:
                continue
            per_dir[a.direction] = per_dir.get(a.direction, 0) + 1
            exp = " `급행`" if a.express else ""
            rows.append(f"**{a.heading}**{exp}\n└ {a.message}")
        embed.add_field(name=line, value="\n".join(rows)[:1024] or "-", inline=False)
    embed.set_footer(text="서울시 실시간 도착정보 · 실제와 1~2분 차이가 날 수 있어요")
    await interaction.followup.send(embed=embed, ephemeral=ephemeral)


# ---------------------------------------------------------------- 날씨

async def send_weather(interaction: discord.Interaction, city: str):
    if city not in CITIES:
        await interaction.response.send_message("자동완성 목록에서 도시를 골라주세요.", ephemeral=True)
        return
    if not state.kma_key:
        await interaction.response.send_message("⚠️ 기상청 키(KMA_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    now = datetime.now(KST)
    lat, lon = CITIES[city]
    try:
        fc = await fetch_forecast(state.session, state.kma_key, lat, lon, now)
    except Exception as e:
        log.exception("날씨 실패")
        await interaction.followup.send(f"날씨를 받아오지 못했어요. ({str(e)[:120]})", ephemeral=True)
        return
    slots = pick_slots(fc, now.replace(tzinfo=None))
    if not slots:
        await interaction.followup.send("예보가 비어 있어요. 잠시 후 다시 시도해 주세요.", ephemeral=True)
        return
    first = slots[0]
    embed = discord.Embed(title=f"{first.sky} {city} 날씨",
                          description=f"**{first.text}** · {first.temp:.0f}°" if first.temp is not None else first.text,
                          color=0x5B8CFF)
    rows = []
    for s in slots:
        day = "" if s.when.date() == now.date() else f"{WEEK[s.when.weekday()]} "
        t = f"{s.temp:.0f}°" if s.temp is not None else "-"
        pop = f" · 💧{s.pop}%" if s.pop else ""
        rows.append(f"`{day}{s.when.hour:02d}시` {s.sky} {t}{pop}")
    embed.add_field(name="3시간 간격", value="\n".join(rows), inline=False)
    for d, (lo, hi) in sorted(fc.daily.items())[:3]:
        dt = datetime.strptime(d, "%Y%m%d")
        label = "오늘" if dt.date() == now.date() else f"{dt.month}/{dt.day}({WEEK[dt.weekday()]})"
        lo_t = f"{lo:.0f}°" if lo is not None else "-"
        hi_t = f"{hi:.0f}°" if hi is not None else "-"
        embed.add_field(name=label, value=f"최저 {lo_t} / 최고 {hi_t}", inline=True)
    embed.set_footer(text="기상청 단기예보 · 도시 중심 기준")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------- 중간도시 -> 버스 버튼

class MidpointBusView(discord.ui.View):
    """1위 도시로 가는 각자의 버스를 바로 조회하는 버튼. 누른 사람 기준으로 열린다."""

    def __init__(self, origins: list[str], city: str, start_bus):
        super().__init__(timeout=1800)
        for o in origins:
            if o == city:
                continue
            btn = discord.ui.Button(label=f"{o} → {city} 버스", emoji="🚌", style=discord.ButtonStyle.secondary)
            btn.callback = self._make(start_bus, o, city)
            self.add_item(btn)

    @staticmethod
    def _make(start_bus, dep, arr):
        async def cb(interaction: discord.Interaction):
            await start_bus(interaction, dep, arr)

        return cb


# ---------------------------------------------------------------- 등록

def register(tree: app_commands.CommandTree):
    @tree.command(name="지하철", description="수도권 지하철 경로와 환승을 알려줘요")
    @app_commands.rename(src="출발역", dst="도착역")
    @app_commands.autocomplete(src=station_autocomplete, dst=station_autocomplete)
    async def subway_cmd(interaction: discord.Interaction, src: str, dst: str):
        await subway_route(interaction, src, dst)

    @tree.command(name="노선도", description="직접 그린 수도권 지하철 노선도를 보여줘요")
    @app_commands.rename(line="노선")
    @app_commands.describe(line="비워두면 서울 시내 전체")
    @app_commands.autocomplete(line=line_autocomplete)
    async def map_cmd(interaction: discord.Interaction, line: str | None = None):
        await subway_map(interaction, line)

    @tree.command(name="도착", description="지하철역 실시간 도착 정보를 보여줘요 (서울 시내 역)")
    @app_commands.rename(station="역")
    @app_commands.autocomplete(station=station_autocomplete)
    async def arrival_cmd(interaction: discord.Interaction, station: str):
        await send_arrivals(interaction, station)

    @tree.command(name="날씨", description="도시 날씨 예보를 보여줘요 (기상청)")
    @app_commands.rename(city="도시")
    @app_commands.autocomplete(city=city_autocomplete)
    async def weather_cmd(interaction: discord.Interaction, city: str):
        await send_weather(interaction, city)
