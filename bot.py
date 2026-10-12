import asyncio
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from cities import CITIES, place_label, resolve_place
from embeds import (BUS_IMAGE, SEAT_IMAGE, bus_embed, bus_image_embed, bus_subtitle,
                    midpoint_embed, missing_text, page_range, train_embed)
from midpoint import find_midpoints
from providers import ProviderUnavailable, get_bus_provider, get_train_provider
from seatmap import render_seat_map
import travel
from views import DateView, ResultView, date_label

try:  # Pillow 가 없으면 글자로만 보여준다
    import images
except Exception:  # pragma: no cover
    images = None

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("seatbot")


def today_kst() -> date:
    return datetime.now(KST).date()


def drop_departed(trips, day: date, now: datetime | None = None):
    """오늘 날짜면 이미 출발한 편은 뺀다."""
    now = now or datetime.now(KST)
    if day != now.date():
        return list(trips)
    hhmm = now.strftime("%H:%M")
    return [t for t in trips if t.depart >= hhmm]


def filter_terminals(trips, dep_term: str | None, arr_term: str | None):
    """터미널을 지정했으면 그 터미널에서 출발/도착하는 편만 남긴다."""
    return [
        t for t in trips
        if (not dep_term or t.dep_terminal == dep_term) and (not arr_term or t.arr_terminal == arr_term)
    ]


class Flow:
    """한 번의 조회 흐름: 날짜 고르기 -> 결과 -> (다른 날짜 | 자리 보기)."""

    def __init__(self, kind: str, provider, dep: str, arr: str, owner_id: int):
        self.kind = kind  # "bus" | "train"
        self.provider = provider
        self.dep, self.arr, self.owner_id = dep, arr, owner_id
        # 버스는 '동서울' 같은 터미널 이름도 받는다: 도시로 조회한 뒤 그 터미널만 남긴다
        self.q_dep, self.q_arr = dep, arr
        self.dep_term = self.arr_term = None
        if kind == "bus":
            try:
                self.q_dep, self.dep_term = resolve_place(dep)
                self.q_arr, self.arr_term = resolve_place(arr)
            except KeyError:
                pass  # 실제 공급자가 아는 이름일 수 있으니 그대로 넘긴다
        self.trips: list = []
        self.day_label = ""
        self.sources: list[str] = []
        self.missing: list[str] = []

    def _prompt(self) -> str:
        return f"**{self.dep} → {self.arr}**  날짜를 골라주세요."

    def _date_view(self) -> DateView:
        return DateView(self.owner_id, self.pick, today_kst())

    async def start(self, interaction: discord.Interaction):
        await interaction.response.send_message(self._prompt(), view=self._date_view())

    async def back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=self._prompt(), embed=None, view=self._date_view(), attachments=[]
        )

    async def pick(self, interaction: discord.Interaction, day: date):
        label = date_label(day, today_kst())
        await interaction.response.edit_message(
            content=f"**{self.dep} → {self.arr}** {label} 조회 중…", embed=None, view=None, attachments=[]
        )
        try:
            result = await self.provider.search(self.q_dep, self.q_arr, day)
        except ProviderUnavailable as e:
            await interaction.edit_original_response(content=f"⚠️ {e}", view=self._date_view())
            return
        except Exception:
            log.exception("조회 실패")
            await interaction.edit_original_response(
                content="조회 중 오류가 났어요. 잠시 후 다시 시도해 주세요.", view=self._date_view()
            )
            return

        self.day_label = label
        if self.kind == "bus":  # BusSearch (고속+시외 통합)
            self.trips = drop_departed(filter_terminals(result.trips, self.dep_term, self.arr_term), day)
            self.sources, self.missing = result.sources, result.missing
        else:
            self.trips = drop_departed(result, day)
        embed, view, files = await self._render(0)
        await interaction.edit_original_response(content=None, embed=embed, view=view, attachments=files)

    async def _render(self, page: int):
        """(embed, view, 첨부 파일들). 버스는 편 목록을 이미지로, 못 만들면 글자로."""
        if self.kind == "train":
            view = ResultView(self.owner_id, self.back, trips=self.trips, page=page, on_page=self.turn)
            embed = train_embed(self.dep, self.arr, self.day_label, self.trips,
                                self.provider.label, view.page)
            return embed, view, []

        view = ResultView(self.owner_id, self.back, trips=self.trips, page=page,
                          on_page=self.turn, on_seat=self.seat)
        page = view.page
        if images is not None and self.trips:
            try:
                start, end = page_range(len(self.trips), page)
                notice = f"※ {missing_text(self.missing)}" if self.missing else ""
                png = await asyncio.to_thread(
                    images.render_schedule,
                    f"{self.dep} → {self.arr}",
                    bus_subtitle(self.day_label, self.trips, page),
                    self.trips[start:end],
                    f"출처: {' + '.join(self.sources)}",
                    notice,
                )
                embed = bus_image_embed(self.dep, self.arr, self.day_label, self.trips, self.missing, page)
                return embed, view, [discord.File(png, filename=BUS_IMAGE)]
            except Exception:
                log.exception("편 목록 이미지 실패, 글자로 대신 보여줌")
        embed = bus_embed(self.dep, self.arr, self.day_label, self.trips, self.sources, self.missing, page)
        return embed, view, []

    async def turn(self, interaction: discord.Interaction, page: int):
        await interaction.response.defer()  # 이미지 그리는 동안 3초 제한에 안 걸리게
        embed, view, files = await self._render(page)
        await interaction.edit_original_response(embed=embed, view=view, attachments=files)

    async def seat(self, interaction: discord.Interaction, idx: int):
        t = self.trips[idx]
        route = t.route or f"{self.dep} → {self.arr}"
        title = f"{t.depart}→{t.arrive}  {route}"
        sub = f"[{t.kind}] {t.grade} · " + ("매진" if t.sold_out else f"잔여 {t.seats_left}/{t.seats_total}석") \
            + f" · {t.fare:,}원"
        await interaction.response.defer(ephemeral=True, thinking=True)
        if images is not None:
            try:
                png = await asyncio.to_thread(images.render_seats, title, sub, t)
                embed = discord.Embed(title=f"🪑 {title}", description=sub)
                embed.set_image(url=f"attachment://{SEAT_IMAGE}")
                await interaction.followup.send(embed=embed, file=discord.File(png, filename=SEAT_IMAGE),
                                                ephemeral=True)
                return
            except Exception:
                log.exception("좌석 이미지 실패, 글자로 대신 보여줌")
        text = f"**{title}**  {sub}"
        if not t.sold_out:
            text += f"\n```\n{render_seat_map(t.seats_total, t.free_seats)}\n```"
        await interaction.followup.send(text, ephemeral=True)


class SeatBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.bus = None
        self.train = None

    async def setup_hook(self):
        await travel.state.start()  # 지하철 데이터는 뒤에서 불러온다
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("로그인: %s", self.user)


client = SeatBot()
tree = client.tree


def _terminal_autocomplete(attr: str):
    async def autocomplete(interaction: discord.Interaction, current: str):
        try:
            names = await getattr(interaction.client, attr).terminals()
        except Exception:
            return []
        cur = current.strip()
        label = place_label if attr == "bus" else str
        choices = []
        for n in names:
            text = label(n)
            if cur in text:
                choices.append(app_commands.Choice(name=text, value=n))
        return choices[:25]

    return autocomplete


async def city_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.strip()
    return [app_commands.Choice(name=n, value=n) for n in CITIES if cur in n][:25]


def same_city(a: str, b: str) -> bool:
    try:
        return resolve_place(a)[0] == resolve_place(b)[0]
    except KeyError:
        return False


async def _start(interaction: discord.Interaction, kind: str, dep: str, arr: str):
    provider = client.bus if kind == "bus" else client.train
    if dep == arr:
        await interaction.response.send_message("출발지와 도착지가 같아요.", ephemeral=True)
        return
    try:
        names = await provider.terminals()
    except ProviderUnavailable as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return
    if dep not in names or arr not in names:
        await interaction.response.send_message("목록에 있는 이름을 골라주세요. (입력하면 자동완성이 떠요)", ephemeral=True)
        return
    if kind == "bus" and same_city(dep, arr):
        await interaction.response.send_message("출발과 도착이 같은 도시예요.", ephemeral=True)
        return
    await Flow(kind, provider, dep, arr, interaction.user.id).start(interaction)


@tree.command(name="버스", description="고속·시외버스 잔여석과 자리를 확인해요")
@app_commands.rename(dep="출발", arr="도착")
@app_commands.describe(dep="출발 도시/터미널", arr="도착 도시/터미널")
@app_commands.autocomplete(dep=_terminal_autocomplete("bus"), arr=_terminal_autocomplete("bus"))
async def bus_cmd(interaction: discord.Interaction, dep: str, arr: str):
    await _start(interaction, "bus", dep, arr)


@tree.command(name="기차", description="기차 시간표와 예약 가능 여부를 확인해요")
@app_commands.rename(dep="출발", arr="도착")
@app_commands.describe(dep="출발 역/도시", arr="도착 역/도시")
@app_commands.autocomplete(dep=_terminal_autocomplete("train"), arr=_terminal_autocomplete("train"))
async def train_cmd(interaction: discord.Interaction, dep: str, arr: str):
    await _start(interaction, "train", dep, arr)


@tree.command(name="중간도시", description="여러 명의 출발지에서 모이기 좋은 중간 도시를 찾아요")
@app_commands.rename(a="출발지1", b="출발지2", c="출발지3", d="출발지4", e="출발지5")
@app_commands.autocomplete(a=city_autocomplete, b=city_autocomplete, c=city_autocomplete,
                           d=city_autocomplete, e=city_autocomplete)
async def mid_cmd(interaction: discord.Interaction, a: str, b: str,
                  c: str | None = None, d: str | None = None, e: str | None = None):
    origins = [x for x in (a, b, c, d, e) if x]
    try:
        cands = find_midpoints(origins)
    except KeyError as err:
        await interaction.response.send_message(f"모르는 도시예요: {err.args[0]}. 자동완성 목록에서 골라주세요.", ephemeral=True)
        return
    except ValueError as err:
        await interaction.response.send_message(str(err), ephemeral=True)
        return
    view = travel.MidpointBusView(origins, cands[0].city, lambda i, dep, arr: _start(i, "bus", dep, arr))
    await interaction.response.send_message(embed=midpoint_embed(origins, cands), view=view)


travel.register(tree)


async def start_health_server(port: int):
    """Render 무료 웹 서비스용. 포트를 열어두고 /health 에 응답한다."""
    from aiohttp import web

    async def health(_request):
        state = "ready" if client.is_ready() else "starting"
        return web.json_response({"status": "ok", "bot": state})

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("헬스체크 서버: 포트 %s", port)
    return runner


async def amain():
    port = os.getenv("PORT")  # Render 가 넣어줌. 로컬에선 보통 비어 있음
    runner = await start_health_server(int(port)) if port else None
    if port and os.getenv("PROBE", "1") == "1":  # (임시) 새 API 응답 형식 확인
        from probe import run_probe
        asyncio.get_running_loop().create_task(run_probe())
    token = os.getenv("DISCORD_TOKEN")
    try:
        if not token:
            msg = "DISCORD_TOKEN 이 비어 있어요. .env 또는 Render 환경변수를 확인하세요."
            if runner is None:
                raise SystemExit(msg)
            log.error("%s 토큰을 넣으면 자동으로 다시 시작돼요.", msg)
            await asyncio.Event().wait()  # 서버는 살려둔다
        client.bus = get_bus_provider(os.getenv("BUS_PROVIDER", "mock"))
        client.train = get_train_provider(os.getenv("TRAIN_PROVIDER", "mock"))
        async with client:
            await client.start(token)
    finally:
        await travel.state.close()
        if runner is not None:
            await runner.cleanup()


def main():
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
