"""기상청 단기예보 (공공데이터포털 VilageFcstInfoService_2.0/getVilageFcst)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, log, pi, pow, sin, tan

URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)  # 발표 시각 (발표 후 10분쯤부터 받을 수 있음)


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 -> 기상청 격자 (nx, ny). 기상청 공개 변환식 (람베르트 정각원추)."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    D = pi / 180.0
    re = RE / GRID
    slat1, slat2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = tan(pi * 0.25 + slat2 * 0.5) / tan(pi * 0.25 + slat1 * 0.5)
    sn = log(cos(slat1) / cos(slat2)) / log(sn)
    sf = pow(tan(pi * 0.25 + slat1 * 0.5), sn) * cos(slat1) / sn
    ro = re * sf / pow(tan(pi * 0.25 + olat * 0.5), sn)
    ra = re * sf / pow(tan(pi * 0.25 + lat * D * 0.5), sn)
    theta = lon * D - olon
    if theta > pi:
        theta -= 2 * pi
    if theta < -pi:
        theta += 2 * pi
    theta *= sn
    return int(ra * sin(theta) + XO + 0.5), int(ro - ra * cos(theta) + YO + 0.5)


def base_datetime(now: datetime) -> tuple[str, str]:
    """지금 받을 수 있는 가장 최근 발표 (base_date, base_time)."""
    t = now - timedelta(minutes=10)
    for h in reversed(BASE_HOURS):
        if t.hour >= h:
            return t.strftime("%Y%m%d"), f"{h:02d}00"
    y = t - timedelta(days=1)
    return y.strftime("%Y%m%d"), "2300"


@dataclass
class Slot:
    when: datetime
    temp: float | None
    sky: str  # 이모지
    text: str  # 맑음/구름많음/흐림/비/...
    pop: int | None  # 강수확률 %


_SKY = {"1": ("☀️", "맑음"), "3": ("⛅", "구름많음"), "4": ("☁️", "흐림")}
_PTY = {"1": ("🌧️", "비"), "2": ("🌨️", "비/눈"), "3": ("❄️", "눈"), "4": ("🌦️", "소나기")}


@dataclass
class Forecast:
    slots: list[Slot]  # 시간 순
    daily: dict[str, tuple[float | None, float | None]]  # 'YYYYMMDD' -> (최저, 최고)


def parse_forecast(data: dict) -> Forecast:
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in (None, "00"):
        raise RuntimeError(f"기상청 응답 오류: {header.get('resultMsg')}")
    items = data["response"]["body"]["items"]["item"]
    by_time: dict[tuple[str, str], dict[str, str]] = {}
    daily: dict[str, list] = {}
    for it in items:
        key = (it["fcstDate"], it["fcstTime"])
        by_time.setdefault(key, {})[it["category"]] = str(it["fcstValue"])
        if it["category"] in ("TMN", "TMX"):
            d = daily.setdefault(it["fcstDate"], [None, None])
            d[0 if it["category"] == "TMN" else 1] = float(it["fcstValue"])
    slots = []
    for (d, t), v in sorted(by_time.items()):
        pty = v.get("PTY", "0")
        sky, text = _PTY[pty] if pty in _PTY else _SKY.get(v.get("SKY", ""), ("🌡️", "-"))
        temp = float(v["TMP"]) if "TMP" in v else None
        pop = int(v["POP"]) if v.get("POP", "").isdigit() else None
        slots.append(Slot(datetime.strptime(d + t, "%Y%m%d%H%M"), temp, sky, text, pop))
    return Forecast(slots, {k: (a, b) for k, (a, b) in daily.items()})


def pick_slots(fc: Forecast, now: datetime, count: int = 8, step_h: int = 3) -> list[Slot]:
    """지금 이후로 step_h 시간 간격 count 개."""
    out = []
    nxt = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    for s in fc.slots:
        if s.when >= nxt:
            out.append(s)
            nxt = s.when + timedelta(hours=step_h)
            if len(out) == count:
                break
    return out


async def fetch_forecast(session, key: str, lat: float, lon: float, now: datetime) -> Forecast:
    nx, ny = latlon_to_grid(lat, lon)
    bd, bt = base_datetime(now)
    params = {"serviceKey": key, "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
              "base_date": bd, "base_time": bt, "nx": str(nx), "ny": str(ny)}
    async with session.get(URL, params=params) as r:
        text = await r.text()
    import json

    try:
        data = json.loads(text)
    except ValueError:
        # 키 오류 등은 XML 로 온다
        raise RuntimeError(f"기상청 응답을 읽을 수 없어요: {text[:150]}")
    return parse_forecast(data)
