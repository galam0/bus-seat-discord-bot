"""/도움말: 분류별 명령어 설명. 버튼으로 분류를 넘겨 본다."""
import discord
from discord import app_commands

COLOR = 0x5B8CFF

# 분류 -> (이모지, 한 줄 소개, [(명령어, 하는 일, 예시)])
SECTIONS: dict[str, tuple[str, str, list[tuple[str, str, str]]]] = {
    "이동": ("🚌", "도시 사이를 오갈 때", [
        ("/버스 출발 도착", "고속·시외버스 편 목록과 남은 자리. 날짜는 버튼으로(2주 뒤까지), "
                        "시간을 고르면 좌석 배치도가 나와요.", "/버스 서울 강릉"),
        ("/기차 출발 도착", "KTX·ITX·무궁화 시간표와 요금. 잔여석은 공개 데이터에 없어요.", "/기차 서울 부산"),
        ("/중간도시 출발지1 출발지2 …", "2~5명이 모이기 좋은 도시 TOP3. 각자 그 도시로 가는 버스를 바로 볼 수 있어요.",
         "/중간도시 서울 부산 광주"),
    ]),
    "지하철": ("🚇", "수도권 지하철", [
        ("/지하철 출발역 도착역", "경로·환승·예상 시간 그림. 어느 방향 열차를 타는지, 다음 열차가 언제 오는지 알려주고 "
                               "도착역 근처 맛집·숙소 버튼도 있어요.",
         "/지하철 강남 홍대입구"),
        ("/도착 역", "지금 그 역에 오는 열차 (서울 시내 역).", "/도착 서울"),
        ("/막차 역 [요일]", "노선·방향별 첫차와 막차. 막차 전 열차 두 대까지 보여줘요.", "/막차 강남"),
        ("/노선도 [노선]", "직접 그린 노선도. 비우면 서울 시내 전체, 노선을 고르면 그 노선만.", "/노선도 2호선"),
    ]),
    "여행지": ("🗺️", "어디 갈지, 가서 뭐 할지", [
        ("/어디가지 [출발] [시간] [테마]", "여행지를 랜덤으로 뽑아요. 출발지를 넣으면 버스로 그 시간 안에 갈 수 있는 곳만.",
         "/어디가지 테마:바다"),
        ("/날씨 도시", "3시간 간격 예보, 사흘치 최저·최고, 미세먼지를 사진 한 장으로.", "/날씨 부산"),
        ("/근처 장소 [종류] [거리]", "역·도시·장소 근처 맛집·숙소·관광지를 가까운 순으로. 이름을 누르면 카카오맵이 열려요.",
         "/근처 장소:홍대입구역 종류:숙소"),
        ("/뭐먹지 장소", "역·도시·장소 근처 맛집 하나를 랜덤으로. 다시 뽑기, 카카오맵 지도 버튼이 있어요.", "/뭐먹지 성수역"),
        ("/관광 도시 [종류]", "관광지·맛집·축제·숙소·문화시설·레포츠 (한국관광공사).", "/관광 도시:경주 종류:맛집"),
        ("/혼잡 장소", "서울 주요 장소의 지금 혼잡도와 6시간 예측, 가장 한산한 시간.", "/혼잡 성수카페거리"),
    ]),
    "같이 여행": ("🧳", "친구들이랑 여행 준비 (서버에서)", [
        ("/여행 만들기", "여행 카드를 만들어요. 참가 버튼, 일정, 날씨·관광·버스 바로가기가 붙어요. "
                       "출발 전날 저녁 8시에 알림이 와요.", "/여행 만들기 이름:강릉 1박2일 시작날짜:10/25 끝날짜:10/26 장소:강릉"),
        ("/여행 일정추가 · 일정삭제", "날짜·시간별 일정을 넣고 빼요.", "/여행 일정추가 여행:강릉 1박2일 날짜:10/25 할일:안목 커피거리 시간:14:00"),
        ("/여행 일정표", "일정을 날짜별 타임라인 사진으로. 여행 카드의 🗓️ 버튼으로도 볼 수 있어요.",
         "/여행 일정표 여행:강릉 1박2일"),
        ("/여행 투표", "날짜·숙소 같은 걸 투표로 정해요. 여러 개 고를 수 있고, 만든 사람이 마감해요.",
         "/여행 투표 여행:강릉 1박2일 질문:어디서 잘까? 선택지:펜션, 호텔, 게하"),
        ("/여행 준비물", "같이 채우는 체크리스트. 누가 챙길지 정하고, 챙기면 체크.", "/여행 준비물 여행:강릉 1박2일"),
        ("/여행 목록 · 보기 · 삭제", "다가오는 여행 보기, 카드 다시 띄우기, 지우기(만든 사람만).", "/여행 목록"),
    ]),
}

START_TIPS = [
    "🎲 갈 데가 없으면 → `/어디가지`",
    "🧳 정했으면 → `/여행 만들기` 후 `/여행 투표`·`/여행 준비물`",
    "🚌 표 볼 땐 → `/버스` 또는 `/기차`, 친구들 사는 곳이 다르면 `/중간도시`",
    "🌤️ 가기 전에 → `/날씨`, `/관광`, 가서는 `/뭐먹지`",
]


def home_embed() -> discord.Embed:
    e = discord.Embed(title="🧭 travel 봇 도움말", color=COLOR,
                      description="아래 버튼으로 분류를 고르면 명령어마다 설명과 예시가 나와요.\n\n"
                                  "**이렇게 써보세요**\n" + "\n".join(START_TIPS))
    for name, (emoji, intro, cmds) in SECTIONS.items():
        names = " ".join(f"`{c.split()[0]}`" for c, _, _ in cmds)
        e.add_field(name=f"{emoji} {name} — {intro}", value=names, inline=False)
    e.set_footer(text="명령어 칸은 자동완성을 지원해요. 도시·역 이름을 몇 글자만 쳐도 목록이 떠요.")
    return e


def section_embed(name: str) -> discord.Embed:
    emoji, intro, cmds = SECTIONS[name]
    e = discord.Embed(title=f"{emoji} {name}", description=intro, color=COLOR)
    for cmd, what, example in cmds:
        e.add_field(name=cmd, value=f"{what}\n예) `{example}`", inline=False)
    e.set_footer(text="[ ] 안은 비워도 되는 칸이에요")
    return e


def help_embed(section: str | None) -> discord.Embed:
    return section_embed(section) if section in SECTIONS else home_embed()


class HelpView(discord.ui.View):
    def __init__(self, current: str | None = None):
        super().__init__(timeout=600)
        items = [("처음", "🧭", None)] + [(n, e, n) for n, (e, _, _) in SECTIONS.items()]
        for label, emoji, key in items:
            on = key == current
            b = discord.ui.Button(label=label, emoji=emoji, disabled=on,
                                  style=discord.ButtonStyle.primary if on else discord.ButtonStyle.secondary)
            b.callback = self._go(key)
            self.add_item(b)

    @staticmethod
    def _go(key):
        async def cb(interaction: discord.Interaction):
            await interaction.response.edit_message(embed=help_embed(key), view=HelpView(key))

        return cb


async def send_help(interaction: discord.Interaction, section: str | None = None):
    section = section if section in SECTIONS else None
    await interaction.response.send_message(embed=help_embed(section), view=HelpView(section), ephemeral=True)


def register(tree: app_commands.CommandTree):
    @tree.command(name="도움말", description="travel 봇 명령어와 쓰는 법을 알려줘요")
    @app_commands.rename(section="분류")
    @app_commands.describe(section="바로 볼 분류 (비우면 처음 화면)")
    @app_commands.choices(section=[app_commands.Choice(name=f"{e} {n}", value=n) for n, (e, _, _) in SECTIONS.items()])
    async def help_cmd(interaction: discord.Interaction, section: str | None = None):
        await send_help(interaction, section)
