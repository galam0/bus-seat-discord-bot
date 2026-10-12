import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from cities import CITIES
from embeds import bus_embed, midpoint_embed, train_embed
from midpoint import find_midpoints
from providers import ProviderUnavailable, get_bus_provider, get_train_provider
from seatmap import render_seat_map
from views import DateView, ResultView, date_label

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


class Flow:
    """한 번의 조회 흐름: 날짜 고르기 -> 결과 -> (다른 날짜 | 자리 보기)."""

    def __init__(self, kind: str, provider, dep: str, arr: str, owner_id: int):
        self.kind = kind  # "bus" | "train"
        self.provider = provider
        self.dep, self.arr, self.owner_id = dep, arr, owner_id
        self.trips: list = []

    def _prompt(self) -> str:
        return f"**{self.dep} → {self.arr}**  날짜를 골라주세요."

    def _date_view(self) -> DateView:
        return DateView(self.owner_id, self.pick, today_kst())

    async def start(self, interaction: discord.Interaction):
        await interaction.response.send_message(self._prompt(), view=self._date_view())

    async def back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=self._prompt(), embed=None, view=self._date_view())

    async def pick(self, interaction: discord.Interaction, day: date):
        label = date_label(day, today_kst())
        await interaction.response.edit_message(
            content=f"**{self.dep} → {self.arr}** {label} 조회 중…", embed=None, view=None
        )
        try:
            result = await self.provider.search(self.dep, self.arr, day)
        except ProviderUnavailable as e:
            await interaction.edit_original_response(content=f"⚠️ {e}", view=self._date_view())
            return
        except Exception:
            log.exception("조회 실패")
            await interaction.edit_original_response(
                content="조회 중 오류가 났어요. 잠시 후 다시 시도해 주세요.", view=self._date_view()
            )
            return

        if self.kind == "bus":  # BusSearch (고속+시외 통합)
            self.trips = drop_departed(result.trips, day)
            embed = bus_embed(self.dep, self.arr, label, self.trips, result.sources, result.missing)
            view = ResultView(self.owner_id, self.back, self.trips, self.seat)
        else:
            self.trips = drop_departed(result, day)
            embed = train_embed(self.dep, self.arr, label, self.trips, self.provider.label)
            view = ResultView(self.owner_id, self.back)
        await interaction.edit_original_response(content=None, embed=embed, view=view)

    async def seat(self, interaction: discord.Interaction, idx: int):
        t = self.trips[idx]
        head = f"**{self.dep} → {self.arr}  {t.depart} [{t.kind}] {t.grade}**"
        if t.sold_out:
            text = f"{head}\n🔴 매진이에요."
        else:
            text = f"{head}\n잔여 {t.seats_left}/{t.seats_total}석\n```\n{render_seat_map(t.seats_total, t.free_seats)}\n```"
        await interaction.response.send_message(text, ephemeral=True)


class SeatBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.bus = None
        self.train = None

    async def setup_hook(self):
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
        return [app_commands.Choice(name=n, value=n) for n in names if cur in n][:25]

    return autocomplete


async def city_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.strip()
    return [app_commands.Choice(name=n, value=n) for n in CITIES if cur in n][:25]


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
    await interaction.response.send_message(embed=midpoint_embed(origins, cands))


def main():
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN 이 비어 있어요. .env 를 확인하세요.")
    client.bus = get_bus_provider(os.getenv("BUS_PROVIDER", "mock"))
    client.train = get_train_provider(os.getenv("TRAIN_PROVIDER", "mock"))
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
