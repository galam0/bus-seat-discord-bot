"""미세먼지: 에어코리아 대기오염정보 (공공데이터포털 B552584/ArpltnInforInqireSvc)."""
from dataclasses import dataclass

URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"

# 환경부 기준 (㎍/㎥): 좋음 / 보통 / 나쁨 / 매우나쁨
_PM10 = (30, 80, 150)
_PM25 = (15, 35, 75)
LEVELS = (("좋음", "🔵"), ("보통", "🟢"), ("나쁨", "🟠"), ("매우나쁨", "🔴"))


def level(value: float | None, cuts) -> tuple[str, str]:
    if value is None:
        return ("정보 없음", "⚪")
    for i, c in enumerate(cuts):
        if value <= c:
            return LEVELS[i]
    return LEVELS[3]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None  # '-' 등 측정 안 된 값


@dataclass
class Dust:
    pm10: float | None
    pm25: float | None
    stations: int
    time: str
    area: str  # '서울 평균' / '진주(상대동)'

    def text(self) -> str:
        n10, e10 = level(self.pm10, _PM10)
        n25, e25 = level(self.pm25, _PM25)
        v10 = f"{self.pm10:.0f}" if self.pm10 is not None else "-"
        v25 = f"{self.pm25:.0f}" if self.pm25 is not None else "-"
        return f"{e10} 미세먼지 **{n10}** ({v10}㎍/㎥) · {e25} 초미세먼지 **{n25}** ({v25}㎍/㎥)"


def parse_dust(data: dict, sido: str, city: str) -> Dust:
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in (None, "00"):
        raise RuntimeError(f"에어코리아 응답 오류: {header.get('resultMsg')}")
    items = data["response"]["body"].get("items") or []
    if isinstance(items, dict):  # 혹시 {'item': [...]} 모양이면
        items = items.get("item") or []
    # 도시 이름이 측정소 이름에 들어간 곳이 있으면 그것만, 아니면 시도 평균
    near = [it for it in items if city in str(it.get("stationName", ""))]
    use = near or items
    pm10 = [v for it in use if (v := _num(it.get("pm10Value"))) is not None]
    pm25 = [v for it in use if (v := _num(it.get("pm25Value"))) is not None]
    times = sorted(str(it.get("dataTime") or "") for it in use)
    area = f"{city} 측정소" if near else f"{sido} 평균"
    return Dust(sum(pm10) / len(pm10) if pm10 else None, sum(pm25) / len(pm25) if pm25 else None,
                len(use), times[-1] if times else "", area)


async def fetch_dust(session, key: str, sido: str, city: str) -> Dust:
    params = {"serviceKey": key, "returnType": "json", "numOfRows": "200", "pageNo": "1",
              "sidoName": sido, "ver": "1.0"}
    async with session.get(URL, params=params) as r:
        text = await r.text()
    import json

    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError(f"에어코리아 응답을 읽을 수 없어요: {text[:150]}")
    if "OpenAPI_ServiceResponse" in data:
        raise RuntimeError(data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {}).get("returnAuthMsg", "키 오류"))
    return parse_dust(data, sido, city)
