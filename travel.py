"""여행 명령어: /지하철 /노선도 /도착 /날씨, 그리고 /중간도시 결과의 버스 바로가기 버튼."""
import asyncio
from dataclasses import dataclass
import random
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from cities import CITIES, PLACES, sido_of
from subway import (REALTIME, REALTIME_ALIAS, SubwayData, find_route, line_color, line_sort_key,
                    parse_arrivals, realtime_code)
from weather import fetch_forecast, pick_slots
import timetable
import tour
import trips_ui
from dust import fetch_dust
from weather_images import render_weather
import congestion
import roulette
from trips import MemoryStore, NeonStore, TripBook

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
        self.areas = congestion.AreaList()
        self._loading: asyncio.Task | None = None

    async def start(self):
        import aiohttp

        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self._loading = asyncio.create_task(self.subway.load(self.session))
        asyncio.create_task(self.self_check())
        if self.subway.keys:
            asyncio.create_task(self.areas.verify(self.session, self.subway.keys[0]))
        await self.load_trips()

    async def load_trips(self):
        url = os.getenv("TRAVEL_DATABASE_URL")
        if url:
            try:
                book = TripBook(NeonStore(url, self.session))
                await book.load()
                trips_ui.book = book
                log.info("여행 저장소: DB 연결됨, 여행 %d개, 매개변수 확인 %s", len(book.trips),
                         await book.store.check())
                return
            except Exception as e:
                log.warning("여행 저장소: DB 연결 실패, 임시 저장으로 (%s)", str(e)[:200])
        trips_ui.book = TripBook(MemoryStore())
        await trips_ui.book.load()

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
                         ("김포공항", "서울"), ("응암", "공덕"), ("잠실", "여의도"), ("노원", "강남"),
                         ("인천", "서울"), ("판교", "광화문")):
                r = find_route(self.subway.net, a, b)
                if r:
                    log.info("확인 경로 %s->%s (%d분): %s", a, b, round(r.minutes),
                             " | ".join(f"{l.line} {l.stations[0]}->{l.stations[-1]} [{l.heading}]" for l in r.legs))
        try:
            wk = timetable.week_tag(datetime.now(KST).date())
            for inout in (1, 2):
                rows = await _timetable("0222", wk, inout)
                info = timetable.summarize(rows, timetable.direction_label("2호선", inout))
                if info.count:
                    log.info("확인 막차 강남 %s: 첫차 %s %s행 / 막차 %s", info.label, info.first.time, info.first.dest,
                             ", ".join(f"{d.time} {d.dest}행" for d in info.lasts))
        except Exception as e:
            log.warning("확인 막차 실패: %s", str(e)[:200])
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
    return [app_commands.Choice(name=n, value=n) for n in match_names(list(PLACES), current)]


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

    @discord.ui.button(label="도착역 근처 맛집", emoji="🍽️", style=discord.ButtonStyle.secondary)
    async def near_food(self, interaction: discord.Interaction, _button):
        await send_nearby(interaction, f"역:{self.dst}", "맛집")

    @discord.ui.button(label="도착역 근처 숙소", emoji="🏨", style=discord.ButtonStyle.secondary)
    async def near_stay(self, interaction: discord.Interaction, _button):
        await send_nearby(interaction, f"역:{self.dst}", "숙소")


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
    if city not in PLACES:
        await interaction.response.send_message("자동완성 목록에서 도시를 골라주세요.", ephemeral=True)
        return
    if not state.kma_key:
        await interaction.response.send_message("⚠️ 기상청 키(KMA_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    now = datetime.now(KST)
    lat, lon = PLACES[city]
    sido = sido_of(city)
    dust_task = (asyncio.create_task(fetch_dust(state.session, state.kma_key, sido[0], city))
                 if sido else None)
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
    daily = []
    for d, (lo, hi) in sorted(fc.daily.items())[:3]:
        dt = datetime.strptime(d, "%Y%m%d")
        label = "오늘" if dt.date() == now.date() else f"{dt.month}/{dt.day}({WEEK[dt.weekday()]})"
        daily.append((label, lo, hi))
    dust, dust_note = None, ""
    if dust_task:
        try:
            dust = await dust_task
        except Exception as e:
            log.warning("미세먼지 실패: %s", str(e)[:150])
            dust_note = "미세먼지 정보를 못 받았어요 (에어코리아 활용신청을 확인해 주세요)"
    else:
        dust_note = "이 지역은 미세먼지 정보가 없어요"
    first = slots[0]
    try:
        png = await asyncio.to_thread(render_weather, city, now.replace(tzinfo=None), slots, daily, dust, dust_note)
    except Exception:
        log.exception("날씨 이미지 실패")
        await interaction.followup.send(embed=weather_text_embed(city, now, slots, daily, dust, dust_note))
        return
    t = f" · {first.temp:.0f}°" if first.temp is not None else ""
    embed = discord.Embed(title=f"{first.sky} {city} 날씨", description=f"**{first.text}**{t}", color=0x5B8CFF)
    embed.set_image(url=f"attachment://{WEATHER_IMAGE}")
    embed.set_footer(text="기상청 단기예보 · 에어코리아 · 도시 중심 기준")
    await interaction.followup.send(embed=embed, file=discord.File(png, filename=WEATHER_IMAGE))


WEATHER_IMAGE = "weather.png"


def weather_text_embed(city, now, slots, daily, dust, dust_note) -> discord.Embed:
    """이미지를 못 만들 때 쓰는 글자 버전."""
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
    for label, lo, hi in daily:
        lo_t = f"{lo:.0f}°" if lo is not None else "-"
        hi_t = f"{hi:.0f}°" if hi is not None else "-"
        embed.add_field(name=label, value=f"최저 {lo_t} / 최고 {hi_t}", inline=True)
    if dust:
        embed.add_field(name=f"공기 · {dust.area}", value=dust.text(), inline=False)
    elif dust_note:
        embed.add_field(name="공기", value=dust_note, inline=False)
    embed.set_footer(text="기상청 단기예보 · 에어코리아 · 도시 중심 기준")
    return embed


# ---------------------------------------------------------------- 관광

class TourView(discord.ui.View):
    """종류 바꾸기 버튼."""

    def __init__(self, city: str, current: str, owner: int):
        super().__init__(timeout=600)
        self.owner = owner
        for kind in tour.KINDS:
            btn = discord.ui.Button(label=kind, emoji=tour.KIND_EMOJI[kind], disabled=kind == current,
                                    style=discord.ButtonStyle.primary if kind == current
                                    else discord.ButtonStyle.secondary, row=0 if len(self.children) < 5 else 1)
            btn.callback = self._cb(city, kind)
            self.add_item(btn)

    @staticmethod
    def _cb(city, kind):
        async def cb(interaction: discord.Interaction):
            await send_tour(interaction, city, kind, edit=True)

        return cb


def tour_embed(city: str, kind: str, spots) -> discord.Embed:
    emoji = tour.KIND_EMOJI[kind]
    embed = discord.Embed(title=f"{emoji} {city} {kind}", color=0x2EB872)
    if not spots:
        embed.description = "가까운 곳에서 찾지 못했어요."
    lines = []
    for sp in spots:
        extra = []
        if sp.period():
            extra.append(f"📅 {sp.period()}")
        if sp.km is not None:
            extra.append(f"{sp.km:.1f}km")
        addr = sp.addr.split(" ")
        short = " ".join(addr[:3]) if addr else ""
        lines.append(f"**{sp.title}**\n└ {short}" + (f" · {' · '.join(extra)}" if extra else ""))
    if lines:
        embed.description = "\n".join(lines)[:4000]
    img = next((sp.image for sp in spots if sp.image), "")
    if img:
        embed.set_image(url=img)
    embed.set_footer(text="한국관광공사 관광정보 · 도시 중심에서 가까운 순" if kind != "축제"
                     else "한국관광공사 관광정보 · 진행 중이거나 열릴 축제 (도시 30km 안)")
    return embed


async def send_tour(interaction: discord.Interaction, city: str, kind: str = "관광지", edit: bool = False):
    if city not in PLACES:
        await interaction.response.send_message("자동완성 목록에서 도시를 골라주세요.", ephemeral=True)
        return
    if kind not in tour.KINDS:
        kind = "관광지"
    if not state.kma_key:
        await interaction.response.send_message("⚠️ 공공데이터포털 키(KMA_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    if edit:
        await interaction.response.defer()
    else:
        await interaction.response.defer(thinking=True)
    sido = sido_of(city)
    try:
        spots = await tour.fetch_spots(state.session, state.kma_key, kind, PLACES[city],
                                       sido[1] if sido else None, datetime.now(KST).date())
    except Exception as e:
        log.warning("관광 실패: %s", str(e)[:150])
        await interaction.followup.send(
            f"관광 정보를 받아오지 못했어요. 관광공사 API 활용신청이 승인됐는지 확인해 주세요. ({str(e)[:80]})",
            ephemeral=True)
        return
    embed, view = tour_embed(city, kind, spots), TourView(city, kind, interaction.user.id)
    if edit:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)


# ---------------------------------------------------------------- 장소 찾기 (역·도시·이름)

@dataclass
class Spot0:
    """검색 기준점."""
    label: str  # '강남역', '전주', '경포대'
    lat: float
    lon: float
    kind: str  # 역 / 도시 / 장소
    key: str  # 다시 찾을 때 쓰는 값 ('역:강남', '도시:전주', '장소:경포대')

    @property
    def wide(self) -> bool:
        return self.kind == "도시"


def _station_point(name: str) -> Spot0 | None:
    net = state.subway.net
    if net is None or name not in net.by_name:
        return None
    pts = [(n.lat, n.lon) for i in net.by_name[name] if (n := net.nodes[i]).lat is not None]
    if not pts:
        return None
    lat = sum(p[0] for p in pts) / len(pts)
    lon = sum(p[1] for p in pts) / len(pts)
    return Spot0(f"{name}역", lat, lon, "역", f"역:{name}")


def _city_point(name: str) -> Spot0 | None:
    if name not in PLACES:
        return None
    lat, lon = PLACES[name]
    return Spot0(name, lat, lon, "도시", f"도시:{name}")


async def resolve_point(text: str) -> Spot0 | None:
    """'역:강남' / '도시:전주' / 그냥 이름. 이름은 도시 → 역 → 관광공사 장소 검색 순."""
    text = (text or "").strip()
    if ":" in text:
        kind, name = text.split(":", 1)
        if kind == "역":
            return _station_point(name)
        if kind == "도시":
            return _city_point(name)
        text = name
    if not text:
        return None
    hit = _city_point(text) or _station_point(text)
    if not hit and text.endswith("역"):
        hit = _station_point(text[:-1])
    if hit:
        return hit
    if not state.kma_key:
        return None
    found = await tour.search_keyword(state.session, state.kma_key, text)
    if not found:
        return None
    title, lat, lon, _addr = found
    return Spot0(title, lat, lon, "장소", f"장소:{text}")


async def point_autocomplete(interaction: discord.Interaction, current: str):
    out = [app_commands.Choice(name=f"🏙️ {n}", value=f"도시:{n}") for n in match_names(list(PLACES), current, 8)]
    net = state.subway.net
    if net is not None:
        cur = current.strip()
        cur = cur[:-1] if cur.endswith("역") and len(cur) > 1 else cur
        for n in match_names(net.station_names(), cur, 25 - len(out)):
            lines = net.lines_at(n)
            label = f"🚇 {n}역 · {', '.join(lines[:3])}" + (" 외" if len(lines) > 3 else "")
            out.append(app_commands.Choice(name=label[:100], value=f"역:{n}"))
    text = current.strip()
    names = {c.value.split(":", 1)[1] for c in out}
    if text and text not in names and text.removesuffix("역") not in names:
        search = app_commands.Choice(name=f"🔎 '{text}' 이름으로 찾기"[:100], value=f"장소:{text}"[:100])
        out = [search] + out[:24]
    return out[:25]


def kakao_link(*words: str) -> str:
    return "https://map.kakao.com/link/search/" + quote(" ".join(w for w in words if w))


def _dist(km: float | None) -> str:
    if km is None:
        return ""
    return f"{km * 1000:.0f}m" if km < 1 else f"{km:.1f}km"


_NOT_FOUND = "그 장소를 못 찾았어요. 자동완성 목록에서 고르거나 다른 이름으로 적어 보세요."


# ---------------------------------------------------------------- 뭐먹지

_food_cache: dict[str, tuple[datetime, list]] = {}
FOOD_TTL = timedelta(hours=6)


async def _food_pool(pt: Spot0) -> list:
    now = datetime.now(KST)
    hit = _food_cache.get(pt.key)
    if hit and now - hit[0] < FOOD_TTL:
        return hit[1]
    radius = 10000 if pt.wide else 1500
    spots = await tour.fetch_spots(state.session, state.kma_key, "맛집", (pt.lat, pt.lon), None, now.date(),
                                   radius_m=radius, limit=60)
    spots = [p for p in spots if p.title]
    if len(_food_cache) > 100:
        _food_cache.clear()
    _food_cache[pt.key] = (now, spots)
    return spots


def food_embed(pt: Spot0, spot, n: int) -> discord.Embed:
    lines = [f"## {spot.title}"]
    if spot.cat:
        lines.append(f"🍴 {spot.cat}")
    if spot.addr:
        lines.append(f"📍 {spot.addr}")
    if spot.km is not None:
        lines.append(f"📏 {pt.label}{' 중심' if pt.wide else ''}에서 {_dist(spot.km)}")
    e = discord.Embed(title=f"🍽️ {pt.label}에서 뭐 먹지?", description="\n".join(lines), color=0xF0A030)
    if spot.image:
        e.set_image(url=spot.image)
    e.set_footer(text=f"{pt.label} 근처 맛집 {n}곳 중에서 뽑았어요 · 한국관광공사 · 영업시간은 가기 전에 확인하세요")
    return e


class FoodView(discord.ui.View):
    def __init__(self, pt: Spot0, spot, seen: set[str]):
        super().__init__(timeout=1800)
        self.pt, self.seen = pt, seen
        again = discord.ui.Button(label="다시 뽑기", emoji="🎲", style=discord.ButtonStyle.primary)
        again.callback = self.reroll
        self.add_item(again)
        area = spot.addr.split(" ")[1] if len(spot.addr.split(" ")) > 1 else ""
        self.add_item(discord.ui.Button(label="지도", emoji="🗺️", url=kakao_link(area, spot.title)))
        more = discord.ui.Button(label="근처 맛집 목록", emoji="📋")
        more.callback = self.listing
        self.add_item(more)

    async def reroll(self, interaction: discord.Interaction):
        await send_food(interaction, self.pt.key, seen=self.seen, edit=True, point=self.pt)

    async def listing(self, interaction: discord.Interaction):
        await send_nearby(interaction, self.pt.key, "맛집", point=self.pt)


async def send_food(interaction: discord.Interaction, place: str, seen: set[str] | None = None, edit: bool = False,
                    point: Spot0 | None = None):
    if not state.kma_key:
        await interaction.response.send_message("⚠️ 공공데이터포털 키(KMA_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    await (interaction.response.defer() if edit else interaction.response.defer(thinking=True))
    try:
        pt = point or await resolve_point(place)
        if pt is None:
            await interaction.followup.send(_NOT_FOUND, ephemeral=True)
            return
        pool = await _food_pool(pt)
    except Exception as e:
        log.warning("뭐먹지 실패: %s", str(e)[:150])
        await interaction.followup.send(
            f"맛집 정보를 받아오지 못했어요. 관광공사 API 활용신청이 승인됐는지 확인해 주세요. ({str(e)[:80]})",
            ephemeral=True)
        return
    if not pool:
        await interaction.followup.send(f"{pt.label} 근처에서 맛집 정보를 못 찾았어요.", ephemeral=True)
        return
    seen = set(seen or ())
    fresh = [p for p in pool if p.title not in seen] or pool
    spot = random.choice(fresh)
    seen.add(spot.title)
    if len(seen) >= len(pool):
        seen = {spot.title}
    embed, view = food_embed(pt, spot, len(pool)), FoodView(pt, spot, seen)
    if edit:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)


# ---------------------------------------------------------------- 근처

NEAR_KINDS = ["맛집", "숙소", "관광지", "문화시설", "레포츠"]
RADII = {"500m": 500, "1km": 1000, "2km": 2000, "5km": 5000}


def nearby_embed(pt: Spot0, kind: str, spots, radius: int, widened: bool) -> discord.Embed:
    emoji = tour.KIND_EMOJI[kind]
    r = f"{radius // 1000}km" if radius >= 1000 else f"{radius}m"
    e = discord.Embed(title=f"{emoji} {pt.label} 근처 {kind}", color=0x2EB872)
    if not spots:
        e.description = f"{r} 안에서 찾지 못했어요. 거리를 넓혀 보세요."
    lines = []
    area = ""
    for sp in spots:
        parts = sp.addr.split(" ")
        area = area or (parts[1] if len(parts) > 1 else "")
        meta = [x for x in (_dist(sp.km), sp.cat, " ".join(parts[1:3])) if x]
        lines.append(f"**[{sp.title}]({kakao_link(parts[1] if len(parts) > 1 else '', sp.title)})**\n└ "
                     + " · ".join(meta))
    if lines:
        e.description = ("\n".join(lines))[:4000]
        if widened:
            e.description = f"가까이엔 없어서 {r}까지 넓혀 찾았어요.\n\n" + e.description
    img = next((sp.image for sp in spots if sp.image), "")
    if img:
        e.set_image(url=img)
    where = {"역": "역", "도시": "도시 중심", "장소": "그 장소"}[pt.kind]
    e.set_footer(text=f"{where}에서 가까운 순 · {r} 안 · 이름을 누르면 카카오맵 · 한국관광공사")
    return e


class NearbyView(discord.ui.View):
    def __init__(self, pt: Spot0, current: str, radius: int | None):
        super().__init__(timeout=1800)
        for kind in NEAR_KINDS:
            on = kind == current
            b = discord.ui.Button(label=kind, emoji=tour.KIND_EMOJI[kind], disabled=on,
                                  style=discord.ButtonStyle.primary if on else discord.ButtonStyle.secondary)
            b.callback = self._cb(pt, kind, radius)
            self.add_item(b)

    @staticmethod
    def _cb(pt, kind, radius):
        async def cb(interaction: discord.Interaction):
            await send_nearby(interaction, pt.key, kind, radius, edit=True, point=pt)

        return cb


async def send_nearby(interaction: discord.Interaction, place: str, kind: str = "맛집", radius: int | None = None,
                      edit: bool = False, point: Spot0 | None = None):
    kind = kind if kind in NEAR_KINDS else "맛집"
    if not state.kma_key:
        await interaction.response.send_message("⚠️ 공공데이터포털 키(KMA_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    await (interaction.response.defer() if edit else interaction.response.defer(thinking=True))
    try:
        pt = point or await resolve_point(place)
        if pt is None:
            await interaction.followup.send(_NOT_FOUND, ephemeral=True)
            return
        r = radius or (3000 if pt.wide else 1000)
        today = datetime.now(KST).date()
        spots = await tour.fetch_spots(state.session, state.kma_key, kind, (pt.lat, pt.lon), None, today,
                                       radius_m=r, limit=10)
        widened = False
        if not spots and radius is None and r < 5000:  # 거리를 안 정했으면 한 번 넓혀 본다
            r, widened = 5000, True
            spots = await tour.fetch_spots(state.session, state.kma_key, kind, (pt.lat, pt.lon), None, today,
                                           radius_m=r, limit=10)
    except Exception as e:
        log.warning("근처 실패: %s", str(e)[:150])
        await interaction.followup.send(
            f"근처 정보를 받아오지 못했어요. 관광공사 API 활용신청이 승인됐는지 확인해 주세요. ({str(e)[:80]})",
            ephemeral=True)
        return
    embed, view = nearby_embed(pt, kind, spots, r, widened), NearbyView(pt, kind, radius)
    if edit:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)


# ---------------------------------------------------------------- 첫차·막차

_tt_cache: dict[tuple, list] = {}


async def _timetable(cd: str, week: int, inout: int) -> list[dict]:
    key = (cd, week, inout, datetime.now(KST).date())
    if key not in _tt_cache:
        if len(_tt_cache) > 300:
            _tt_cache.clear()
        last = None
        for k in list(state.subway.keys):
            try:
                _tt_cache[key] = await timetable.fetch_direction(state.session, k, cd, week, inout)
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(str(last))
    return _tt_cache[key]


async def send_last_train(interaction: discord.Interaction, station: str, day: str = "오늘"):
    msg = await state.ensure_subway()
    if msg:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        return
    net = state.subway.net
    ids = net.by_name.get(station)
    if not ids:
        await interaction.response.send_message("모르는 역이에요. 자동완성 목록에서 골라주세요.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    today = datetime.now(KST).date()
    week = {"평일": 1, "토요일": 2, "휴일": 3}.get(day) or timetable.week_tag(today)
    embed = discord.Embed(title=f"🌙 {station}역 첫차·막차",
                          description=f"**{timetable.WEEK_NAME[week]}** 시간표 · 공휴일은 휴일 시간표를 따라요",
                          color=0x6C5CE7)
    found = False
    for nid in sorted(ids, key=lambda i: line_sort_key(net.nodes[i].line))[:6]:
        node = net.nodes[nid]
        rows_text = []
        for inout in (1, 2):
            try:
                rows = await _timetable(nid, week, inout)
            except Exception as e:
                log.warning("시간표 실패 %s: %s", nid, str(e)[:120])
                rows = []
            info = timetable.summarize(rows, timetable.direction_label(node.line, inout))
            if not info.count:
                continue
            found = True
            dests = "·".join(info.main_dests)
            last = info.lasts[0]
            more = ", ".join(f"{d.time} {d.dest}행" for d in info.lasts[1:])
            rows_text.append(f"**{info.label}** ({dests}행 쪽)\n"
                             f"첫차 `{info.first.time}` {info.first.dest}행 · 막차 `{last.time}` **{last.dest}행**"
                             + (f"\n└ 그 전: {more}" if more else ""))
        if rows_text:
            embed.add_field(name=node.line, value="\n".join(rows_text)[:1024], inline=False)
    if not found:
        embed.description += "\n\n시간표 정보가 없어요. 서울시 데이터에 없는 노선(일부 코레일·경전철 구간)일 수 있어요."
    embed.set_footer(text="서울시 지하철 역별 시간표 · 막차는 행선지가 짧을 수 있으니 '행'을 꼭 확인하세요")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------- 혼잡도

def crowd_embed(c: congestion.Crowd) -> discord.Embed:
    e = discord.Embed(title=f"{c.emoji} {c.area} · 지금 {c.level}", color=c.color,
                      description=f"{c.message}\n👥 약 {congestion.fmt_people(c.low, c.high)} · {c.time[11:16]} 기준")
    rows = []
    for f in c.forecast[:6]:
        emo = congestion.LEVELS.get(f.level, ("⚪", 0))[0]
        rows.append(f"`{f.time}` {emo} {f.level} · {congestion.fmt_people(f.low, f.high)}")
    if rows:
        e.add_field(name="앞으로 6시간", value="\n".join(rows), inline=False)
    q = c.quietest()
    if q:
        emo = congestion.LEVELS.get(q.level, ("⚪", 0))[0]
        e.add_field(name="12시간 안에 가장 한산할 때", value=f"{emo} **{q.time}** ({q.level})", inline=False)
    e.set_footer(text="서울시 실시간 도시데이터 · 휴대폰 신호로 추정한 인구예요")
    return e


async def send_crowd(interaction: discord.Interaction, area: str):
    keys = state.subway.keys
    if not keys:
        await interaction.response.send_message("⚠️ 서울시 키(SEOUL_API_KEY)가 아직 없어요.", ephemeral=True)
        return
    name = area.strip()
    if name not in state.areas.valid:
        found = state.areas.search(name, 5)
        if len(found) == 1:
            name = found[0]
        elif not found:
            await interaction.response.send_message("그런 장소가 없어요. 자동완성 목록에서 골라주세요. (서울 주요 장소만 돼요)",
                                                    ephemeral=True)
            return
        else:
            await interaction.response.send_message("이 중에서 골라주세요: " + ", ".join(found), ephemeral=True)
            return
    await interaction.response.defer(thinking=True)
    try:
        crowd = await congestion.fetch_crowd(state.session, keys[0], name)
    except Exception as e:
        log.warning("혼잡도 실패: %s", str(e)[:150])
        crowd = None
    if crowd is None:
        await interaction.followup.send(f"**{name}** 혼잡도를 받아오지 못했어요. 잠시 후 다시 해 주세요.", ephemeral=True)
        return
    await interaction.followup.send(embed=crowd_embed(crowd))


async def area_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=a, value=a) for a in state.areas.search(current)]


# ---------------------------------------------------------------- 어디가지

TIME_CHOICES = {"1시간": 60, "2시간": 120, "3시간": 180, "4시간": 240, "상관없음": None}


def roulette_embed(origin: str | None, p: roulette.Pick, max_label: str, theme: str | None, n: int) -> discord.Embed:
    tags = " ".join(f"{roulette.THEME_EMOJI[t]} {t}" for t in p.themes)
    title = f"🎲 {origin}에서 떠나는 여행지는…" if origin else "🎲 이번 여행지는…"
    travel = p.travel_text()
    e = discord.Embed(title=title, color=0x9B59B6,
                      description=f"## 📍 {p.place}\n{p.blurb}\n\n{tags}" + (f"\n{travel}" if travel else ""))
    if origin:
        cond = [max_label if max_label != "상관없음" else "시간 상관없음", theme or "테마 상관없음"]
        note = "시간은 버스 기준 대략값"
    else:
        cond = ["전국", theme or "테마 상관없음"]
        note = ("시간 조건은 출발지를 넣어야 적용돼요" if max_label != "상관없음"
                else "출발지를 넣으면 이동 시간도 알려줘요")
    e.set_footer(text=f"조건: {' · '.join(cond)} · 후보 {n}곳 중에서 뽑았어요 · {note}")
    return e


class RouletteView(discord.ui.View):
    def __init__(self, origin: str | None, max_label: str, theme: str | None, p: roulette.Pick, seen: set[str],
                 start_bus=None):
        super().__init__(timeout=1800)
        self.origin, self.max_label, self.theme, self.pick, self.seen = origin, max_label, theme, p, seen
        self.start_bus = start_bus
        self._btn("다시 뽑기", "🎲", self.reroll, discord.ButtonStyle.primary)
        self._btn("날씨", "🌤️", self.weather)
        self._btn("관광", "🏞️", self.tour)
        bus = self._btn("버스", "🚌", self.bus)
        bus.disabled = p.minutes is None or p.place not in CITIES or self.origin not in CITIES or not start_bus
        self._btn("여행으로 만들기", "🧳", self.make_trip, discord.ButtonStyle.success, row=1)

    def _btn(self, label, emoji, cb, style=discord.ButtonStyle.secondary, row=0):
        b = discord.ui.Button(label=label, emoji=emoji, style=style, row=row)
        b.callback = cb
        self.add_item(b)
        return b

    async def reroll(self, interaction: discord.Interaction):
        await send_roulette(interaction, self.origin, self.max_label, self.theme, seen=self.seen, edit=True,
                            start_bus=self.start_bus)

    async def weather(self, interaction: discord.Interaction):
        await send_weather(interaction, self.pick.place)

    async def tour(self, interaction: discord.Interaction):
        await send_tour(interaction, self.pick.place, "관광지")

    async def bus(self, interaction: discord.Interaction):
        await self.start_bus(interaction, self.origin, self.pick.place)

    async def make_trip(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("서버 안에서만 여행을 만들 수 있어요.", ephemeral=True)
            return
        await interaction.response.send_modal(trips_ui.CreateTripModal(self.pick.place))


async def send_roulette(interaction: discord.Interaction, origin: str | None, max_label: str = "상관없음",
                        theme: str | None = None, seen: set[str] | None = None, edit: bool = False, start_bus=None):
    origin = (origin or "").strip() or None
    if origin is not None and origin not in PLACES:
        await interaction.response.send_message("출발 도시를 자동완성 목록에서 골라주세요. (비워두면 전국에서 뽑아요)",
                                                ephemeral=True)
        return
    theme = theme if theme in roulette.THEMES else None
    max_label = max_label if max_label in TIME_CHOICES else "상관없음"
    seen = set(seen or ())
    p, n = roulette.pick(origin, TIME_CHOICES[max_label] if origin else None, theme, avoid=seen)
    if p is None:
        await interaction.response.send_message("조건에 맞는 곳이 없어요. 시간을 늘리거나 테마를 바꿔 보세요.", ephemeral=True)
        return
    seen.add(p.place)
    if len(seen) >= n:  # 다 한 번씩 나왔으면 처음부터
        seen = {p.place}
    embed = roulette_embed(origin, p, max_label, theme, n)
    view = RouletteView(origin, max_label, theme, p, seen, start_bus or _start_bus_hook.get("fn"))
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)


_start_bus_hook: dict = {}


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

async def tour_kind_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=k, value=k) for k in tour.KINDS if current.strip() in k]


def register(tree: app_commands.CommandTree, start_bus=None):
    trips_ui.register(tree, start_bus=start_bus, send_weather=send_weather, send_tour=send_tour, send_food=send_food)

    @tree.command(name="뭐먹지", description="역·도시·장소 근처 맛집 하나를 랜덤으로 골라줘요 (한국관광공사)")
    @app_commands.rename(place="장소")
    @app_commands.describe(place="지하철역, 도시, 또는 장소 이름 (예: 홍대입구역, 전주, 경포대)")
    @app_commands.autocomplete(place=point_autocomplete)
    async def food_cmd(interaction: discord.Interaction, place: str):
        await send_food(interaction, place)

    @tree.command(name="근처", description="역·도시·장소 근처 맛집·숙소·관광지를 가까운 순으로 보여줘요")
    @app_commands.rename(place="장소", kind="종류", radius="거리")
    @app_commands.describe(place="지하철역, 도시, 또는 장소 이름 (예: 강남역, 강릉, 경포대)",
                           radius="비우면 역·장소는 1km, 도시는 3km")
    @app_commands.choices(kind=[app_commands.Choice(name=k, value=k) for k in NEAR_KINDS],
                          radius=[app_commands.Choice(name=k, value=v) for k, v in RADII.items()])
    @app_commands.autocomplete(place=point_autocomplete)
    async def near_cmd(interaction: discord.Interaction, place: str, kind: str | None = None,
                       radius: int | None = None):
        await send_nearby(interaction, place, kind or "맛집", radius)
    _start_bus_hook["fn"] = start_bus

    @tree.command(name="혼잡", description="서울 주요 장소의 지금 혼잡도와 앞으로 예측을 보여줘요")
    @app_commands.rename(area="장소")
    @app_commands.describe(area="예: 홍대, 성수, 강남역, 여의도한강공원")
    @app_commands.autocomplete(area=area_autocomplete)
    async def crowd_cmd(interaction: discord.Interaction, area: str):
        await send_crowd(interaction, area)

    @tree.command(name="어디가지", description="조건에 맞는 여행지를 랜덤으로 골라줘요")
    @app_commands.rename(origin="출발", hours="시간", theme="테마")
    @app_commands.describe(origin="출발 도시 (비워두면 전국에서 뽑아요)", hours="버스로 이만큼 안에 갈 수 있는 곳", theme="원하는 분위기")
    @app_commands.choices(hours=[app_commands.Choice(name=k, value=k) for k in TIME_CHOICES],
                          theme=[app_commands.Choice(name=t, value=t) for t in roulette.THEMES])
    @app_commands.autocomplete(origin=city_autocomplete)
    async def roulette_cmd(interaction: discord.Interaction, origin: str | None = None, hours: str | None = None,
                           theme: str | None = None):
        await send_roulette(interaction, origin, hours or "상관없음", theme)

    @tree.command(name="관광", description="도시의 관광지·맛집·축제·숙소를 보여줘요 (한국관광공사)")
    @app_commands.rename(city="도시", kind="종류")
    @app_commands.autocomplete(city=city_autocomplete, kind=tour_kind_autocomplete)
    async def tour_cmd(interaction: discord.Interaction, city: str, kind: str | None = None):
        await send_tour(interaction, city, kind or "관광지")

    @tree.command(name="막차", description="지하철역 첫차·막차 시간을 알려줘요")
    @app_commands.rename(station="역", day="요일")
    @app_commands.describe(day="비우면 오늘 기준")
    @app_commands.choices(day=[app_commands.Choice(name=n, value=n) for n in ("평일", "토요일", "휴일")])
    @app_commands.autocomplete(station=station_autocomplete)
    async def last_cmd(interaction: discord.Interaction, station: str, day: str | None = None):
        await send_last_train(interaction, station, day or "오늘")

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
