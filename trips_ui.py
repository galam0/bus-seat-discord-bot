"""/여행 명령어와 여행 카드 버튼, 출발 전날 알림."""
import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from cities import PLACES
from trips import DEFAULT_CHECKS, REMIND_HOUR, Poll, Trip, TripBook, day_label, parse_day, parse_time

log = logging.getLogger("seatbot.trips")
KST = ZoneInfo("Asia/Seoul")

book: TripBook | None = None
_hooks: dict = {}  # start_bus, send_weather, send_tour, send_food (bot/travel 에서 넣어 줌)
TIMELINE_IMAGE = "schedule.png"


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


# ---------------------------------------------------------------- 카드

def trip_embed(trip: Trip, today: date) -> discord.Embed:
    s, e = trip.start_date, trip.end_date
    period = day_label(s) if s == e else f"{day_label(s)} ~ {day_label(e)}"
    lines = [f"📅 {period} · {trip.nights()} · **{trip.dday(today)}**"]
    if trip.place:
        lines.append(f"📍 {trip.place}")
    members = " ".join(f"<@{m}>" for m in trip.members[:20]) or "아직 없어요"
    lines.append(f"👥 참가 {len(trip.members)}명: {members}")
    embed = discord.Embed(title=f"🧳 {trip.name}", description="\n".join(lines), color=0x5B8CFF)

    by_day: dict[str, list] = {}
    for it in trip.items:
        by_day.setdefault(it.day, []).append(it)
    days = sorted(by_day)
    for d in days[:20]:
        rows = [f"`{it.time}` {it.text}" if it.time else f"• {it.text}" for it in by_day[d]]
        dd = date.fromisoformat(d)
        n = (dd - s).days + 1
        embed.add_field(name=f"{n}일차 · {day_label(dd)}", value="\n".join(rows)[:1024], inline=False)
    if not days:
        embed.add_field(name="일정", value="아직 없어요. **📝 일정 추가**로 넣어 보세요.", inline=False)
    extras = []
    if trip.checklist:
        done = sum(c.done for c in trip.checklist)
        extras.append(f"🎒 준비물 {done}/{len(trip.checklist)} 챙김")
    open_polls = [p for p in trip.polls if not p.closed]
    if open_polls:
        extras.append(f"🗳️ 진행 중인 투표 {len(open_polls)}개: " + ", ".join(p.question for p in open_polls[:3]))
    if extras:
        embed.add_field(name="같이 정할 것", value="\n".join(extras)[:1024], inline=False)
    saved = "" if book and book.persistent else " · ⚠️ 임시 저장(봇이 다시 켜지면 사라져요)"
    embed.set_footer(text=f"출발 전날 저녁 {REMIND_HOUR}시에 참가자에게 알림을 보내요{saved}")
    return embed


def trip_view(trip: Trip) -> discord.ui.View:
    """버튼은 custom_id 로만 구분해서 봇이 다시 켜져도 동작한다 (on_interaction 에서 처리)."""
    v = discord.ui.View(timeout=None)
    tid = trip.id

    def b(label, emoji, action, style=discord.ButtonStyle.secondary, row=0):
        v.add_item(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"trip:{action}:{tid}", row=row))

    b("참가 / 빠지기", "🙋", "join", discord.ButtonStyle.primary)
    b("일정 추가", "📝", "add")
    b("준비물", "🎒", "check")
    b("일정표", "🗓️", "timeline")
    b("새로고침", "🔄", "refresh")
    if trip.place in PLACES:
        b("날씨", "🌤️", "weather", row=1)
        b("관광", "🏞️", "tour", row=1)
        b("뭐먹지", "🍽️", "food", row=1)
        b("버스", "🚌", "bus", row=1)
    return v


# ---------------------------------------------------------------- 모달

class AddItemModal(discord.ui.Modal):
    def __init__(self, trip: Trip):
        super().__init__(title=f"{trip.name} 일정 추가"[:45])
        self.trip = trip
        self.day = discord.ui.TextInput(label="날짜", placeholder=f"예: {trip.start_date.month}/{trip.start_date.day}",
                                        default=f"{trip.start_date.month}/{trip.start_date.day}", max_length=12)
        self.time = discord.ui.TextInput(label="시간 (없어도 돼요)", placeholder="예: 14:30, 오후 2시",
                                         required=False, max_length=10)
        self.text = discord.ui.TextInput(label="할 일", placeholder="예: 경포대 산책, 숙소 체크인", max_length=100)
        for x in (self.day, self.time, self.text):
            self.add_item(x)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            d = parse_day(self.day.value, min(today_kst(), self.trip.start_date))
            t = parse_time(self.time.value)
            await book.add_item(self.trip, d, t, self.text.value, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.edit_message(embed=trip_embed(self.trip, today_kst()), view=trip_view(self.trip))


class CreateTripModal(discord.ui.Modal):
    """다른 기능(여행지 룰렛 등)에서 바로 여행을 만들 때."""

    def __init__(self, place: str):
        super().__init__(title=f"{place} 여행 만들기"[:45])
        self.place = place
        self.name = discord.ui.TextInput(label="여행 이름", default=f"{place} 여행", max_length=40)
        self.start = discord.ui.TextInput(label="시작 날짜", placeholder="예: 10/25, 내일", max_length=12)
        self.end = discord.ui.TextInput(label="끝 날짜 (비우면 당일치기)", placeholder="예: 10/26", required=False,
                                        max_length=12)
        for x in (self.name, self.start, self.end):
            self.add_item(x)

    async def on_submit(self, interaction: discord.Interaction):
        today = today_kst()
        try:
            s = parse_day(self.start.value, today)
            e = parse_day(self.end.value, s) if self.end.value.strip() else s
            trip = await book.create(interaction.guild_id, interaction.channel_id, self.name.value, s, e,
                                     self.place, interaction.user.id)
        except ValueError as err:
            await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
            return
        await interaction.response.send_message(content="🧳 새 여행이 생겼어요! 같이 갈 사람은 **🙋 참가**를 눌러 주세요.",
                                                embed=trip_embed(trip, today), view=trip_view(trip))


class BusFromModal(discord.ui.Modal):
    def __init__(self, trip: Trip):
        super().__init__(title=f"{trip.place} 가는 버스"[:45])
        self.trip = trip
        self.origin = discord.ui.TextInput(label="출발 도시 또는 터미널", placeholder="예: 서울, 동서울", max_length=20)
        self.add_item(self.origin)

    async def on_submit(self, interaction: discord.Interaction):
        await _hooks["start_bus"](interaction, self.origin.value.strip(), self.trip.place)


# ---------------------------------------------------------------- 버튼 처리

async def handle_component(interaction: discord.Interaction) -> bool:
    """trip: / poll: / check: 으로 시작하는 버튼·메뉴면 처리하고 True."""
    cid = str((interaction.data or {}).get("custom_id", ""))
    if cid.startswith("poll:"):
        await _handle_poll(interaction, cid)
        return True
    if cid.startswith("check:"):
        await _handle_check(interaction, cid)
        return True
    if not cid.startswith("trip:"):
        return False
    try:
        _, action, tid = cid.split(":")
        trip = book.trips.get(int(tid)) if book else None
    except ValueError:
        trip = None
    if trip is None:
        await interaction.response.send_message("이 여행은 지워졌거나 찾을 수 없어요.", ephemeral=True)
        return True
    if action == "join":
        joined = await book.toggle_member(trip, interaction.user.id)
        await interaction.response.edit_message(embed=trip_embed(trip, today_kst()), view=trip_view(trip))
        await interaction.followup.send("🙋 참가했어요!" if joined else "빠졌어요.", ephemeral=True)
    elif action == "add":
        await interaction.response.send_modal(AddItemModal(trip))
    elif action == "refresh":
        await interaction.response.edit_message(embed=trip_embed(trip, today_kst()), view=trip_view(trip))
    elif action == "weather":
        await _hooks["send_weather"](interaction, trip.place)
    elif action == "tour":
        await _hooks["send_tour"](interaction, trip.place, "관광지")
    elif action == "food":
        await _hooks["send_food"](interaction, trip.place)
    elif action == "timeline":
        await send_timeline(interaction, trip)
    elif action == "bus":
        await interaction.response.send_modal(BusFromModal(trip))
    elif action == "check":
        if not trip.checklist:
            await book.add_checks(trip, DEFAULT_CHECKS, interaction.user.id)
        await interaction.response.send_message(embed=check_embed(trip), view=check_view(trip))
    return True


# ---------------------------------------------------------------- 일정표 사진

async def send_timeline(interaction: discord.Interaction, trip: Trip):
    from trip_images import render_timeline

    await interaction.response.defer(thinking=True)
    try:
        png = await asyncio.to_thread(render_timeline, trip, today_kst())
    except Exception:
        log.exception("일정표 이미지 실패")
        await interaction.followup.send(embed=trip_embed(trip, today_kst()), view=trip_view(trip))
        return
    embed = discord.Embed(title=f"🗓️ {trip.name} 일정표", color=0x5B8CFF)
    embed.set_image(url=f"attachment://{TIMELINE_IMAGE}")
    if not trip.items:
        embed.description = "아직 일정이 없어요. 여행 카드의 **📝 일정 추가**나 `/여행 일정추가`로 넣어 보세요."
    await interaction.followup.send(embed=embed, file=discord.File(png, filename=TIMELINE_IMAGE))


# ---------------------------------------------------------------- 투표

def _bar(n: int, total: int, width: int = 10) -> str:
    filled = round(width * n / total) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def poll_embed(trip: Trip, poll: Poll) -> discord.Embed:
    voters = poll.total_voters()
    top = max((len(v) for v in poll.votes), default=0)
    lines = []
    for i, (opt, v) in enumerate(zip(poll.options, poll.votes)):
        crown = " 👑" if poll.closed and top and len(v) == top else ""
        who = " ".join(f"<@{u}>" for u in v[:8]) + (" 외" if len(v) > 8 else "")
        lines.append(f"**{i + 1}. {opt}**{crown}\n{_bar(len(v), max(voters, 1))} {len(v)}표 {who}".rstrip())
    state = "🔒 마감됨" if poll.closed else "여러 개 골라도 돼요 · 다시 누르면 취소"
    e = discord.Embed(title=f"🗳️ {poll.question}", description="\n".join(lines),
                      color=0x999999 if poll.closed else 0xF5A623)
    e.set_author(name=f"🧳 {trip.name}")
    e.set_footer(text=f"{voters}명 참여 · {state}")
    return e


def poll_view(trip: Trip, poll: Poll) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for i, opt in enumerate(poll.options):
        v.add_item(discord.ui.Button(label=f"{i + 1}. {opt}"[:80], style=discord.ButtonStyle.secondary,
                                     custom_id=f"poll:{trip.id}:{poll.id}:{i}", row=i // 5,
                                     disabled=poll.closed))
    if not poll.closed:
        v.add_item(discord.ui.Button(label="마감", emoji="🔒", style=discord.ButtonStyle.danger,
                                     custom_id=f"poll:{trip.id}:{poll.id}:close", row=2))
    return v


async def _handle_poll(interaction: discord.Interaction, cid: str):
    try:
        _, tid, pid, what = cid.split(":")
        trip = book.trips.get(int(tid))
        poll = book.poll(trip, int(pid)) if trip else None
    except ValueError:
        trip = poll = None
    if not poll:
        await interaction.response.send_message("이 투표는 지워졌거나 찾을 수 없어요.", ephemeral=True)
        return
    if what == "close":
        if interaction.user.id not in (poll.by, trip.creator) and not _can_manage(interaction, trip):
            await interaction.response.send_message("투표를 만든 사람이나 여행을 만든 사람만 마감할 수 있어요.",
                                                    ephemeral=True)
            return
        await book.close_poll(trip, poll)
    else:
        try:
            await book.toggle_vote(trip, poll, int(what), interaction.user.id)
        except (ValueError, IndexError) as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
    await interaction.response.edit_message(embed=poll_embed(trip, poll), view=poll_view(trip, poll))


# ---------------------------------------------------------------- 준비물

def check_embed(trip: Trip) -> discord.Embed:
    lines = []
    for c in trip.checklist:
        box = "✅" if c.done else "⬜"
        owner = f" — <@{c.owner}>" if c.owner else " — *아직 없음*"
        text = f"~~{c.text}~~" if c.done else c.text
        lines.append(f"{box} {text}{owner}")
    done = sum(c.done for c in trip.checklist)
    e = discord.Embed(title=f"🎒 {trip.name} 준비물 ({done}/{len(trip.checklist)})",
                      description="\n".join(lines) or "아직 없어요. **➕ 추가**로 넣어 보세요.", color=0x2EB872)
    e.set_footer(text="🙋 메뉴: 내가 챙길게요(다시 고르면 취소) · ✅ 메뉴: 챙겼어요 표시")
    return e


def check_view(trip: Trip) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    items = trip.checklist[:25]
    if items:
        claim = discord.ui.Select(placeholder="🙋 내가 챙길게요", custom_id=f"check:{trip.id}:claim", row=0,
                                  options=[discord.SelectOption(label=c.text[:100], value=str(i),
                                                                description=("담당 있음" if c.owner else "담당 없음"))
                                           for i, c in enumerate(items)])
        done = discord.ui.Select(placeholder="✅ 챙겼어요 / 취소", custom_id=f"check:{trip.id}:done", row=1,
                                 options=[discord.SelectOption(label=c.text[:100], value=str(i),
                                                               description=("챙김" if c.done else "아직"))
                                          for i, c in enumerate(items)])
        v.add_item(claim)
        v.add_item(done)
    v.add_item(discord.ui.Button(label="추가", emoji="➕", style=discord.ButtonStyle.primary,
                                 custom_id=f"check:{trip.id}:add", row=2))
    v.add_item(discord.ui.Button(label="새로고침", emoji="🔄", style=discord.ButtonStyle.secondary,
                                 custom_id=f"check:{trip.id}:refresh", row=2))
    return v


class AddChecksModal(discord.ui.Modal):
    def __init__(self, trip: Trip):
        super().__init__(title=f"{trip.name} 준비물 추가"[:45])
        self.trip = trip
        self.items = discord.ui.TextInput(label="준비물 (쉼표나 줄바꿈으로 여러 개)", placeholder="예: 수영복, 선크림, 카드",
                                          style=discord.TextStyle.paragraph, max_length=400)
        self.add_item(self.items)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await book.add_checks(self.trip, split_list(self.items.value), interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.edit_message(embed=check_embed(self.trip), view=check_view(self.trip))


def split_list(text: str) -> list[str]:
    return [x.strip() for x in text.replace("\n", ",").split(",") if x.strip()]


async def _handle_check(interaction: discord.Interaction, cid: str):
    try:
        _, tid, what = cid.split(":")
        trip = book.trips.get(int(tid))
    except ValueError:
        trip = None
    if not trip:
        await interaction.response.send_message("이 여행은 지워졌거나 찾을 수 없어요.", ephemeral=True)
        return
    if what == "add":
        await interaction.response.send_modal(AddChecksModal(trip))
        return
    if what in ("claim", "done"):
        values = (interaction.data or {}).get("values") or []
        try:
            idx = int(values[0])
            trip.checklist[idx]
        except (IndexError, ValueError):
            await interaction.response.send_message("목록이 바뀌었어요. 🔄 새로고침 후 다시 골라 주세요.", ephemeral=True)
            return
        if what == "claim":
            await book.toggle_claim(trip, idx, interaction.user.id)
        else:
            await book.toggle_done(trip, idx)
    await interaction.response.edit_message(embed=check_embed(trip), view=check_view(trip))


# ---------------------------------------------------------------- 알림

async def reminder_loop(client: discord.Client, every: int = 300):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await send_due_reminders(client, now_kst())
        except Exception:
            log.exception("여행 알림 실패")
        await asyncio.sleep(every)


async def send_due_reminders(client, now: datetime):
    if not book:
        return
    for trip in book.due_reminders(now.replace(tzinfo=None)):
        ch = client.get_channel(trip.channel)
        if ch is None:
            try:
                ch = await client.fetch_channel(trip.channel)
            except Exception:
                ch = None
        trip.reminded = True
        await book.save(trip)
        if ch is None:
            continue
        when = "내일" if trip.start_date > now.date() else "오늘"
        mentions = " ".join(f"<@{m}>" for m in trip.members)
        await ch.send(content=f"⏰ {when} **{trip.name}** 출발이에요! {mentions}".strip(),
                      embed=trip_embed(trip, now.date()), view=trip_view(trip),
                      allowed_mentions=discord.AllowedMentions(users=True))


# ---------------------------------------------------------------- 명령어

async def trip_autocomplete(interaction: discord.Interaction, current: str):
    if not book or interaction.guild_id is None:
        return []
    cur = current.strip()
    return [app_commands.Choice(name=f"{t.name} · {day_label(t.start_date)}"[:100], value=t.name)
            for t in book.in_guild(interaction.guild_id, today_kst()) if cur in t.name][:25]


async def place_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.strip()
    names = list(PLACES)
    starts = [n for n in names if n.startswith(cur)]
    rest = [n for n in names if cur in n and n not in starts]
    return [app_commands.Choice(name=n, value=n) for n in (starts + rest)[:25]]


def _guild_only(interaction) -> bool:
    return interaction.guild_id is not None


def _can_manage(interaction, trip: Trip) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return interaction.user.id == trip.creator or bool(perms and perms.manage_guild)


group = app_commands.Group(name="여행", description="서버에서 같이 가는 여행 일정을 만들고 모아요")


@group.command(name="만들기", description="새 여행을 만들어요")
@app_commands.rename(name="이름", start="시작날짜", end="끝날짜", place="장소")
@app_commands.describe(name="예: 강릉 1박2일", start="예: 10/25, 내일", end="비우면 당일치기",
                       place="도시 (날씨·관광·버스 버튼이 생겨요)")
@app_commands.autocomplete(place=place_autocomplete)
async def create_cmd(interaction: discord.Interaction, name: str, start: str, end: str | None = None,
                     place: str | None = None):
    if not _guild_only(interaction):
        await interaction.response.send_message("서버 안에서만 쓸 수 있어요.", ephemeral=True)
        return
    today = today_kst()
    try:
        s = parse_day(start, today)
        e = parse_day(end, s) if end else s
        trip = await book.create(interaction.guild_id, interaction.channel_id, name, s, e,
                                 (place or "").strip(), interaction.user.id)
    except ValueError as err:
        await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
        return
    except Exception:
        log.exception("여행 만들기 실패")
        await interaction.response.send_message("저장하다 오류가 났어요. 잠시 후 다시 해 주세요.", ephemeral=True)
        return
    await interaction.response.send_message(content="🧳 새 여행이 생겼어요! 같이 갈 사람은 **🙋 참가**를 눌러 주세요.",
                                            embed=trip_embed(trip, today), view=trip_view(trip))


@group.command(name="목록", description="이 서버의 다가오는 여행을 보여줘요")
async def list_cmd(interaction: discord.Interaction):
    if not _guild_only(interaction):
        await interaction.response.send_message("서버 안에서만 쓸 수 있어요.", ephemeral=True)
        return
    today = today_kst()
    trips = book.in_guild(interaction.guild_id, today)
    if not trips:
        await interaction.response.send_message("다가오는 여행이 없어요. `/여행 만들기`로 만들어 보세요!", ephemeral=True)
        return
    lines = []
    for t in trips[:15]:
        place = f" · 📍{t.place}" if t.place else ""
        lines.append(f"**{t.name}** — {day_label(t.start_date)} · {t.nights()} · {t.dday(today)}{place} · 👥{len(t.members)}")
    embed = discord.Embed(title="🧳 다가오는 여행", description="\n".join(lines), color=0x5B8CFF)
    embed.set_footer(text="자세히 보려면 /여행 보기")
    await interaction.response.send_message(embed=embed)


@group.command(name="보기", description="여행 카드를 다시 띄워요")
@app_commands.rename(name="여행")
@app_commands.autocomplete(name=trip_autocomplete)
async def show_cmd(interaction: discord.Interaction, name: str):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요. 자동완성에서 골라 주세요.", ephemeral=True)
        return
    await interaction.response.send_message(embed=trip_embed(trip, today_kst()), view=trip_view(trip))


@group.command(name="일정표", description="여행 일정을 날짜별 타임라인 사진으로 보여줘요")
@app_commands.rename(name="여행")
@app_commands.autocomplete(name=trip_autocomplete)
async def timeline_cmd(interaction: discord.Interaction, name: str):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요. 자동완성에서 골라 주세요.", ephemeral=True)
        return
    await send_timeline(interaction, trip)


@group.command(name="일정추가", description="여행에 일정을 넣어요")
@app_commands.rename(name="여행", day="날짜", text="할일", time="시간")
@app_commands.describe(day="예: 10/25", text="예: 경포대 산책", time="예: 14:30 (없어도 돼요)")
@app_commands.autocomplete(name=trip_autocomplete)
async def add_cmd(interaction: discord.Interaction, name: str, day: str, text: str, time: str | None = None):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요. 자동완성에서 골라 주세요.", ephemeral=True)
        return
    try:
        d = parse_day(day, min(today_kst(), trip.start_date))
        await book.add_item(trip, d, parse_time(time or ""), text, interaction.user.id)
    except ValueError as err:
        await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
        return
    await interaction.response.send_message(content="📝 일정을 넣었어요.", embed=trip_embed(trip, today_kst()),
                                            view=trip_view(trip))


@group.command(name="일정삭제", description="여행 일정 하나를 지워요")
@app_commands.rename(name="여행", number="번호")
@app_commands.describe(number="위에서부터 몇 번째 일정인지 (1부터)")
@app_commands.autocomplete(name=trip_autocomplete)
async def remove_item_cmd(interaction: discord.Interaction, name: str, number: int):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요.", ephemeral=True)
        return
    if not (1 <= number <= len(trip.items)):
        await interaction.response.send_message(f"1~{len(trip.items)} 사이 번호를 적어 주세요.", ephemeral=True)
        return
    item = trip.items[number - 1]
    if item.by != interaction.user.id and not _can_manage(interaction, trip):
        await interaction.response.send_message("그 일정을 넣은 사람이나 여행을 만든 사람만 지울 수 있어요.", ephemeral=True)
        return
    await book.remove_item(trip, number - 1)
    await interaction.response.send_message(content=f"🗑️ '{item.text}' 일정을 지웠어요.",
                                            embed=trip_embed(trip, today_kst()), view=trip_view(trip))


@group.command(name="투표", description="여행에서 같이 정할 것을 투표해요 (날짜·장소 등)")
@app_commands.rename(name="여행", question="질문", options="선택지")
@app_commands.describe(question="예: 언제 갈까?", options="쉼표로 나눠서 2~10개 (예: 10/25, 11/1, 11/8)")
@app_commands.autocomplete(name=trip_autocomplete)
async def poll_cmd(interaction: discord.Interaction, name: str, question: str, options: str):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요. 자동완성에서 골라 주세요.", ephemeral=True)
        return
    try:
        poll = await book.add_poll(trip, question, split_list(options), interaction.user.id)
    except ValueError as err:
        await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
        return
    await interaction.response.send_message(embed=poll_embed(trip, poll), view=poll_view(trip, poll))


@group.command(name="준비물", description="여행 준비물 체크리스트를 띄우거나 항목을 넣어요")
@app_commands.rename(name="여행", items="항목")
@app_commands.describe(items="쉼표로 나눠서 (비우면 기본 목록으로 시작)")
@app_commands.autocomplete(name=trip_autocomplete)
async def check_cmd(interaction: discord.Interaction, name: str, items: str | None = None):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요. 자동완성에서 골라 주세요.", ephemeral=True)
        return
    try:
        if items:
            await book.add_checks(trip, split_list(items), interaction.user.id)
        elif not trip.checklist:
            await book.add_checks(trip, DEFAULT_CHECKS, interaction.user.id)
    except ValueError as err:
        await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
        return
    await interaction.response.send_message(embed=check_embed(trip), view=check_view(trip))


@group.command(name="삭제", description="여행을 지워요 (만든 사람만)")
@app_commands.rename(name="여행")
@app_commands.autocomplete(name=trip_autocomplete)
async def delete_cmd(interaction: discord.Interaction, name: str):
    trip = book.find(interaction.guild_id, name) if interaction.guild_id else None
    if not trip:
        await interaction.response.send_message("그런 여행이 없어요.", ephemeral=True)
        return
    if not _can_manage(interaction, trip):
        await interaction.response.send_message("여행을 만든 사람(또는 서버 관리자)만 지울 수 있어요.", ephemeral=True)
        return
    await book.delete(trip)
    await interaction.response.send_message(f"🗑️ **{trip.name}** 여행을 지웠어요.")


def register(tree: app_commands.CommandTree, *, start_bus, send_weather, send_tour, send_food=None):
    _hooks.update(start_bus=start_bus, send_weather=send_weather, send_tour=send_tour, send_food=send_food)
    tree.add_command(group)

