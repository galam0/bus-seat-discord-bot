"""서울 주요 장소 실시간 혼잡도: 서울시 실시간 인구데이터 (citydata_ppltn).

요청: http://openapi.seoul.go.kr:8088/{key}/json/citydata_ppltn/1/5/{장소 이름}
응답: {"SeoulRtd.citydata_ppltn": [{AREA_NM, AREA_CONGEST_LVL, AREA_CONGEST_MSG, AREA_PPLTN_MIN/MAX,
        PPLTN_TIME, FCST_PPLTN: [{FCST_TIME, FCST_CONGEST_LVL, FCST_PPLTN_MIN/MAX}]}]}
장소 이름이 정확해야 해서, 시작할 때 목록을 한 번 확인해 맞는 이름만 자동완성에 보여준다.
"""
import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import quote

log = logging.getLogger("seatbot.congestion")

URL = "http://openapi.seoul.go.kr:8088/{key}/json/citydata_ppltn/1/5/{name}"

LEVELS = {"여유": ("🟢", 0x2EB872), "보통": ("🟡", 0xF5C518), "약간 붐빔": ("🟠", 0xF5A623), "붐빔": ("🔴", 0xE74C3C)}
ORDER = ["여유", "보통", "약간 붐빔", "붐빔"]

# 서울시 실시간 도시데이터 장소 (서울시 공개 목록 기준, 시작할 때 실제로 되는 이름만 남긴다)
AREAS = [
    # 관광특구
    "강남 MICE 관광특구", "동대문 관광특구", "명동 관광특구", "이태원 관광특구", "잠실 관광특구",
    "종로·청계 관광특구", "홍대 관광특구",
    # 고궁·문화유산
    "경복궁", "광화문·덕수궁", "보신각", "서울 암사동 유적", "창덕궁·종묘",
    # 인구밀집지역 (역)
    "가산디지털단지역", "강남역", "건대입구역", "고덕역", "고속터미널역", "교대역", "구로디지털단지역", "구로역",
    "군자역", "대림역", "동대문역", "뚝섬역", "미아사거리역", "발산역", "사당역", "삼각지역", "서울대입구역",
    "서울식물원·마곡나루역", "서울역", "선릉역", "성신여대입구역", "수유역", "신논현역·논현역", "신도림역",
    "신림역", "신촌·이대역", "양재역", "역삼역", "연신내역", "오목교역·목동운동장", "왕십리역", "용산역",
    "이태원역", "장지역", "장한평역", "천호역", "총신대입구(이수)역", "충정로역", "합정역", "혜화역",
    "홍대입구역(2호선)", "회기역", "잠실역", "잠실새내역", "청량리역", "노원역", "쌍문역", "창동역",
    # 발달상권
    "4·19 카페거리", "가락시장", "가로수길", "광장(전통)시장", "김포공항", "노량진", "덕수궁길·정동길",
    "방배역 먹자골목", "북촌한옥마을", "서촌", "성수카페거리", "수유리 먹자골목", "쌍문동 맛집거리",
    "압구정로데오거리", "여의도", "연남동", "영등포 타임스퀘어", "외대앞", "용리단길", "이태원 앤틱가구거리",
    "인사동·익선동", "창동 신경제 중심지", "청담동 명품거리", "청량리 제기동 일대 전통시장", "해방촌·경리단길",
    "DDP(동대문디자인플라자)", "DMC(디지털미디어시티)", "북창동 먹자골목", "남대문시장", "익선동", "을지로3가역",
    "망원동", "문래동", "신촌 스타광장", "고척돔", "잠실롯데타워 일대",
    # 공원
    "강서한강공원", "광나루한강공원", "광화문광장", "국립중앙박물관·용산가족공원", "난지한강공원", "남산공원",
    "노들섬", "뚝섬한강공원", "망원한강공원", "반포한강공원", "북서울꿈의숲", "불광천", "서리풀공원·몽마르뜨공원",
    "서울광장", "서울대공원", "서울숲공원", "아차산", "양화한강공원", "어린이대공원", "여의도한강공원",
    "월드컵공원", "응봉산", "이촌한강공원", "잠실종합운동장", "잠실한강공원", "잠원한강공원", "청계산",
    "청와대", "북악산", "인왕산", "낙산공원·이화마을", "보라매공원", "올림픽공원", "청계천", "덕수궁",
]


@dataclass
class Forecast:
    time: str  # 'HH시'
    level: str
    low: int
    high: int


@dataclass
class Crowd:
    area: str
    level: str
    message: str
    low: int
    high: int
    time: str
    forecast: list[Forecast]

    @property
    def emoji(self) -> str:
        return LEVELS.get(self.level, ("⚪", 0))[0]

    @property
    def color(self) -> int:
        return LEVELS.get(self.level, ("", 0x888888))[1]

    def quietest(self) -> Forecast | None:
        if not self.forecast:
            return None
        return min(self.forecast, key=lambda f: (ORDER.index(f.level) if f.level in ORDER else 9, f.low))


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def parse_crowd(data: dict) -> Crowd | None:
    rows = data.get("SeoulRtd.citydata_ppltn")
    if not rows:
        return None
    r = rows[0]
    fc = []
    for f in r.get("FCST_PPLTN") or []:
        t = str(f.get("FCST_TIME", ""))
        fc.append(Forecast(f"{t[11:13]}시" if len(t) >= 13 else t, str(f.get("FCST_CONGEST_LVL", "")),
                           _int(f.get("FCST_PPLTN_MIN")), _int(f.get("FCST_PPLTN_MAX"))))
    return Crowd(str(r.get("AREA_NM", "")), str(r.get("AREA_CONGEST_LVL", "")), str(r.get("AREA_CONGEST_MSG", "")),
                 _int(r.get("AREA_PPLTN_MIN")), _int(r.get("AREA_PPLTN_MAX")), str(r.get("PPLTN_TIME", "")), fc)


async def fetch_crowd(session, key: str, area: str) -> Crowd | None:
    async with session.get(URL.format(key=key, name=quote(area))) as r:
        data = await r.json(content_type=None)
    return parse_crowd(data)


class AreaList:
    """시작할 때 이름이 맞는지 확인해서 되는 장소만 남긴다. 확인 전에는 전체 목록을 쓴다."""

    def __init__(self):
        self.valid: list[str] = list(AREAS)
        self.checked = False

    async def verify(self, session, key: str, delay: float = 0.15):
        ok, bad = [], []
        for name in AREAS:
            try:
                crowd = await fetch_crowd(session, key, name)
            except Exception:
                crowd = None
            (ok if crowd else bad).append(name)
            await asyncio.sleep(delay)
        if ok:
            self.valid = ok
            self.checked = True
        log.info("혼잡도 장소 확인: %d곳 사용, 이름이 안 맞는 곳 %d: %s", len(ok), len(bad), ", ".join(bad)[:1500])

    def search(self, text: str, limit: int = 25) -> list[str]:
        t = text.strip().replace(" ", "")
        if not t:
            return self.valid[:limit]
        norm = lambda s: s.replace(" ", "")  # noqa: E731
        starts = [a for a in self.valid if norm(a).startswith(t)]
        rest = [a for a in self.valid if t in norm(a) and a not in starts]
        return (starts + rest)[:limit]


def fmt_people(low: int, high: int) -> str:
    def f(n):
        return f"{n / 10000:.1f}만".replace(".0만", "만") if n >= 10000 else f"{n:,}"

    return f"{f(low)}~{f(high)}명"
