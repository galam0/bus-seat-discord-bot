"""주말 날씨 비교: 단기예보(3일쯤)로 되는 날은 단기, 그 너머는 기상청 중기예보(육상)로."""
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MID_URL = "http://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst"
DEFAULT_CITIES = ["강릉", "속초", "부산", "경주", "전주", "여수", "제주"]
_YEONGDONG = {"강릉", "속초", "양양", "동해", "삼척", "고성"}
_ZONE = {"서울": "11B00000", "인천": "11B00000", "경기": "11B00000", "대전": "11C20000", "세종": "11C20000",
         "충남": "11C20000", "충북": "11C10000", "광주": "11F20000", "전남": "11F20000", "전북": "11F10000",
         "대구": "11H10000", "경북": "11H10000", "부산": "11H20000", "울산": "11H20000", "경남": "11H20000",
         "제주": "11G00000"}
SEVERITY = ["맑음", "구름많음", "흐림", "소나기", "비", "비/눈", "눈"]


def mid_zone(city: str, sido: str | None) -> str | None:
    if sido == "강원":
        return "11D20000" if city in _YEONGDONG else "11D10000"
    return _ZONE.get(sido or "")


def weekend_days(today: date) -> list[date]:
    """이번 주말: 토요일이면 오늘·내일, 일요일이면 오늘, 평일이면 다가오는 토·일."""
    wd = today.weekday()
    if wd == 5:
        return [today, today + timedelta(days=1)]
    if wd == 6:
        return [today]
    sat = today + timedelta(days=5 - wd)
    return [sat, sat + timedelta(days=1)]


@dataclass
class Day:
    text: str  # 맑음/구름많음/흐림/비/...
    pop: int | None  # 강수확률 % (낮 기준 최대)
    lo: float | None
    hi: float | None
    source: str  # 단기 / 중기

    def score(self) -> float:
        """작을수록 좋은 날씨."""
        return SEVERITY.index(self.text) * 12 + (self.pop or 0) if self.text in SEVERITY else 50


def _worst(texts) -> str:
    texts = [t for t in texts if t in SEVERITY]
    return max(texts, key=SEVERITY.index) if texts else "-"


def from_short(fc, day: date) -> Day | None:
    """단기예보(weather.Forecast)에서 그날 낮(9~18시) 요약."""
    slots = [s for s in fc.slots if s.when.date() == day]
    if not slots:
        return None
    daytime = [s for s in slots if 9 <= s.when.hour <= 18] or slots
    rainy = [s.text for s in daytime if s.text in ("비", "비/눈", "눈", "소나기")]
    if rainy:
        text = _worst(rainy)
    else:
        counts: dict[str, int] = {}
        for s in daytime:
            counts[s.text] = counts.get(s.text, 0) + 1
        text = max(counts, key=lambda t: (counts[t], SEVERITY.index(t) if t in SEVERITY else 0))
    pops = [s.pop for s in daytime if s.pop is not None]
    lo, hi = fc.daily.get(day.strftime("%Y%m%d"), (None, None))
    temps = [s.temp for s in slots if s.temp is not None]
    if lo is None and temps:
        lo = min(temps)
    if hi is None and temps:
        hi = max(temps)
    return Day(text, max(pops) if pops else None, lo, hi, "단기")


def mid_tmfc(now: datetime) -> str:
    """가장 최근 중기예보 발표 시각 (06시·18시, 발표 뒤 조금 기다림)."""
    if now.hour >= 19:
        return now.strftime("%Y%m%d") + "1800"
    if now.hour >= 7:
        return now.strftime("%Y%m%d") + "0600"
    return (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"


def _mid_text(wf: str) -> str:
    wf = wf or ""
    if "비/눈" in wf or "눈/비" in wf:
        return "비/눈"
    if "소나기" in wf:
        return "소나기"
    if "비" in wf:
        return "비"
    if "눈" in wf:
        return "눈"
    if "흐" in wf:
        return "흐림"
    if "구름" in wf:
        return "구름많음"
    return "맑음" if "맑" in wf else "-"


_KEY = re.compile(r"^(wf|rnSt)(\d+)(Am|Pm)?$")


def parse_mid_land(data: dict, tmfc: str) -> dict[date, Day]:
    if "OpenAPI_ServiceResponse" in data:
        raise RuntimeError(data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {}).get("returnAuthMsg", "키 오류"))
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in (None, "00"):
        raise RuntimeError(f"중기예보 오류: {header.get('resultMsg')}")
    items = data["response"]["body"]["items"]["item"]
    item = items[0] if isinstance(items, list) else items
    base = datetime.strptime(tmfc[:8], "%Y%m%d").date()
    wf: dict[int, list[str]] = {}
    rn: dict[int, list[int]] = {}
    for k, v in item.items():
        m = _KEY.match(k)
        if not m:
            continue
        n = int(m.group(2))
        if m.group(1) == "wf":
            wf.setdefault(n, []).append(str(v))
        else:
            try:
                rn.setdefault(n, []).append(int(v))
            except (TypeError, ValueError):
                pass
    out = {}
    for n in sorted(set(wf) | set(rn)):
        text = _worst(_mid_text(w) for w in wf.get(n, []))
        out[base + timedelta(days=n)] = Day(text, max(rn[n]) if rn.get(n) else None, None, None, "중기")
    return out


async def fetch_mid_land(session, key: str, zone: str, tmfc: str) -> dict[date, Day]:
    params = {"serviceKey": key, "pageNo": "1", "numOfRows": "10", "dataType": "JSON", "regId": zone, "tmFc": tmfc}
    async with session.get(MID_URL, params=params) as r:
        text = await r.text()
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError(f"중기예보 응답을 읽을 수 없어요: {text[:150]}")
    return parse_mid_land(data, tmfc)


def pick_best(rows: list[tuple[str, list[Day | None]]]) -> str | None:
    scored = [(sum(d.score() for d in days) / len(days), city) for city, days in rows
              if days and all(d is not None for d in days)]
    return min(scored)[1] if scored else None
