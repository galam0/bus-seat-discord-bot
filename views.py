from datetime import date, timedelta

import discord

from embeds import page_count, page_range

WEEK = "월화수목금토일"
_PREFIX = {0: "오늘", 1: "내일", 2: "모레"}


def date_options(today: date, n: int = 7) -> list[date]:
    return [today + timedelta(days=i) for i in range(n)]


def date_label(d: date, today: date) -> str:
    base = f"{d.month}/{d.day}({WEEK[d.weekday()]})"
    prefix = _PREFIX.get((d - today).days, "")
    return f"{prefix} {base}".strip()


class _OwnerOnly(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("명령어를 쓴 사람만 누를 수 있어요.", ephemeral=True)
            return False
        return True


def _button(label, callback, *, emoji=None, disabled=False, row=None, primary=False):
    btn = discord.ui.Button(
        label=label,
        emoji=emoji,
        disabled=disabled,
        row=row,
        style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary,
    )
    btn.callback = callback
    return btn


class DateView(_OwnerOnly):
    """날짜 버튼 (오늘부터 7일)."""

    def __init__(self, owner_id: int, on_pick, today: date, timeout: float = 180):
        super().__init__(owner_id, timeout)
        for d in date_options(today):
            self.add_item(_button(date_label(d, today), self._make_callback(on_pick, d), primary=d == today))

    @staticmethod
    def _make_callback(on_pick, d: date):
        async def callback(interaction: discord.Interaction):
            await on_pick(interaction, d)

        return callback


class ResultView(_OwnerOnly):
    """결과 화면.

    1줄: (버스면) 자리 보기 선택 메뉴 — 지금 쪽에 보이는 편만
    2줄: ◀ 이전 / 다음 ▶ (쪽이 2개 이상일 때) / 📅 다른 날짜
    """

    def __init__(self, owner_id: int, on_back, *, trips=(), page: int = 0,
                 on_page=None, on_seat=None, timeout: float = 300):
        super().__init__(owner_id, timeout)
        n = len(trips)
        pages = page_count(n)
        page = min(max(page, 0), pages - 1)
        self.page = page
        start, end = page_range(n, page)

        if on_seat and n:
            options = [
                discord.SelectOption(
                    label=f"{t.depart} {t.route or t.grade}"[:100],
                    description=f"[{t.kind}] {t.grade} · " + ("매진" if t.sold_out else f"잔여 {t.seats_left}석"),
                    value=str(i),  # 전체 목록 기준 번호
                )
                for i, t in enumerate(trips[start:end], start=start)
            ]
            select = discord.ui.Select(placeholder="🪑 자리 보기 — 시간을 골라요", options=options, row=0)

            async def select_cb(interaction: discord.Interaction):
                await on_seat(interaction, int(select.values[0]))

            select.callback = select_cb
            self.add_item(select)

        if on_page and pages > 1:
            async def prev_cb(interaction: discord.Interaction):
                await on_page(interaction, page - 1)

            async def next_cb(interaction: discord.Interaction):
                await on_page(interaction, page + 1)

            self.add_item(_button("이전", prev_cb, emoji="◀", disabled=page == 0, row=1))
            self.add_item(_button("다음", next_cb, emoji="▶", disabled=page >= pages - 1, row=1))

        async def back_cb(interaction: discord.Interaction):
            await on_back(interaction)

        self.add_item(_button("다른 날짜", back_cb, emoji="📅", row=1))
