from datetime import date, timedelta

import discord

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


class DateView(_OwnerOnly):
    """날짜 버튼 (오늘부터 7일)."""

    def __init__(self, owner_id: int, on_pick, today: date, timeout: float = 180):
        super().__init__(owner_id, timeout)
        for d in date_options(today):
            btn = discord.ui.Button(
                label=date_label(d, today),
                style=discord.ButtonStyle.primary if d == today else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_callback(on_pick, d)
            self.add_item(btn)

    @staticmethod
    def _make_callback(on_pick, d: date):
        async def callback(interaction: discord.Interaction):
            await on_pick(interaction, d)

        return callback


class ResultView(_OwnerOnly):
    """결과 화면: (버스면) 자리 보기 선택 메뉴 + 다른 날짜 버튼."""

    def __init__(self, owner_id: int, on_back, trips=None, on_seat=None, timeout: float = 300):
        super().__init__(owner_id, timeout)
        if trips and on_seat:
            options = [
                discord.SelectOption(
                    label=f"{t.depart} [{t.kind}] {t.grade}",
                    description="매진" if t.sold_out else f"잔여 {t.seats_left}석",
                    value=str(i),
                )
                for i, t in enumerate(trips[:25])
            ]
            select = discord.ui.Select(placeholder="🪑 자리 보기 — 시간을 골라요", options=options)

            async def select_cb(interaction: discord.Interaction):
                await on_seat(interaction, int(select.values[0]))

            select.callback = select_cb
            self.add_item(select)

        back = discord.ui.Button(label="다른 날짜", emoji="📅", style=discord.ButtonStyle.secondary)

        async def back_cb(interaction: discord.Interaction):
            await on_back(interaction)

        back.callback = back_cb
        self.add_item(back)
