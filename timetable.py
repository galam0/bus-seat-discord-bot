"""지하철 첫차·막차: 서울시 SearchSTNTimeTableByIDService (역 코드별 시간표).

요청: /{key}/json/SearchSTNTimeTableByIDService/1/1000/{STATION_CD}/{WEEK_TAG}/{INOUT_TAG}/
- WEEK_TAG: 1 평일, 2 토요일, 3 휴일
- INOUT_TAG: 1 상행(2호선은 내선), 2 하행(2호선은 외선)
응답 row: LEFTTIME, ARRIVETIME, SUBWAYSNAME(출발역), SUBWAYENAME(행선지), EXPRESS_YN ...
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date

URL = "http://openapi.seoul.go.kr:8088/{key}/json/SearchSTNTimeTableByIDService/1/1000/{cd}/{week}/{inout}/"
WEEK_NAME = {1: "평일", 2: "토요일", 3: "휴일"}


def week_tag(d: date) -> int:
    """공휴일은 모르니 요일로만 (토 2, 일 3)."""
    return {5: 2, 6: 3}.get(d.weekday(), 1)


def _sort_key(hms: str) -> int:
    """'00:30:00' 같은 자정 넘은 시각을 그날 마지막 쪽으로."""
    try:
        h, m, *_ = (int(x) for x in hms.split(":"))
    except ValueError:
        return -1
    if h < 4:
        h += 24
    return h * 60 + m


def _clock(hms: str) -> str:
    """'24:46:00' -> '00:46' (자정 넘은 시각을 시계 표기로)."""
    h, m = hms.split(":")[:2]
    return f"{int(h) % 24:02d}:{m}"


@dataclass
class Departure:
    time: str  # 'HH:MM'
    dest: str  # 행선지
    express: bool


@dataclass
class DirectionTimes:
    label: str  # '상행' / '내선' ...
    main_dests: list[str]  # 주로 가는 행선지
    first: Departure | None
    lasts: list[Departure]  # 마지막 몇 대 (늦은 순)
    count: int


def parse_rows(data: dict) -> list[dict]:
    body = data.get("SearchSTNTimeTableByIDService")
    if not body:
        code = (data.get("RESULT") or {}).get("CODE", "")
        if code == "INFO-200":  # 해당 데이터 없음
            return []
        raise RuntimeError(f"시간표 응답 이상: {str(data)[:150]}")
    return body.get("row") or []


def summarize(rows: list[dict], label: str, n_last: int = 3) -> DirectionTimes:
    deps = []
    for r in rows:
        t = str(r.get("LEFTTIME") or r.get("ARRIVETIME") or "")
        if _sort_key(t) < 0 or t.startswith("00:00:00"):
            continue
        dest = str(r.get("SUBWAYENAME") or "").strip()
        if not dest or dest == str(r.get("STATION_NM") or ""):
            continue  # 이 역이 종착인 열차는 탈 수 없음
        deps.append(Departure(_clock(t), dest, str(r.get("EXPRESS_YN") or "") in ("D", "Y", "E")))
    deps.sort(key=lambda d: (_sort_key(d.time), d.time))
    common = [d for d, _ in Counter(d.dest for d in deps).most_common(2)]
    return DirectionTimes(label, common, deps[0] if deps else None, list(reversed(deps[-n_last:])), len(deps))


def direction_label(line: str, inout: int) -> str:
    if line == "2호선":
        return "내선순환" if inout == 1 else "외선순환"
    return "상행" if inout == 1 else "하행"


async def fetch_direction(session, key: str, station_cd: str, week: int, inout: int) -> list[dict]:
    url = URL.format(key=key, cd=station_cd, week=week, inout=inout)
    async with session.get(url) as r:
        data = await r.json(content_type=None)
    return parse_rows(data)
