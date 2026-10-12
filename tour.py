"""관광 정보: 한국관광공사 국문 관광정보 서비스 (공공데이터포털 B551011/KorService2)."""
from dataclasses import dataclass
from datetime import date
from math import asin, cos, radians, sin, sqrt

BASE = "https://apis.data.go.kr/B551011/KorService2"

# 종류 이름 -> contentTypeId
KINDS = {"관광지": 12, "맛집": 39, "축제": 15, "숙소": 32, "문화시설": 14, "레포츠": 28}
KIND_EMOJI = {"관광지": "🏞️", "맛집": "🍽️", "축제": "🎉", "숙소": "🏨", "문화시설": "🏛️", "레포츠": "🏄"}


def _km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


@dataclass
class Spot:
    title: str
    addr: str
    image: str
    km: float | None
    start: str = ""  # 축제 시작 YYYYMMDD
    end: str = ""
    cat: str = ""  # 음식 종류 (한식·카페 등, 알 때만)

    def period(self) -> str:
        if not self.start:
            return ""
        f = lambda s: f"{int(s[4:6])}/{int(s[6:8])}"  # noqa: E731
        return f"{f(self.start)}~{f(self.end)}" if self.end and self.end != self.start else f(self.start)


# 음식점 소분류 (관광공사 서비스 분류 코드 cat3)
FOOD_CATS = {"A05020100": "한식", "A05020200": "양식", "A05020300": "일식", "A05020400": "중식",
             "A05020700": "이색음식점", "A05020900": "카페·찻집", "A05021000": "클럽"}


def food_cat(it: dict) -> str:
    return FOOD_CATS.get(str(it.get("cat3") or ""), "")


def _items(data: dict) -> list[dict]:
    if "OpenAPI_ServiceResponse" in data:
        raise RuntimeError(data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {}).get("returnAuthMsg", "키 오류"))
    header = data.get("response", {}).get("header", {})
    if header.get("resultCode") not in (None, "0000", "00"):
        raise RuntimeError(f"관광공사 응답 오류: {header.get('resultMsg')}")
    items = data["response"]["body"].get("items") or {}
    if isinstance(items, str):  # 결과가 없으면 "" 로 온다
        return []
    item = items.get("item") or []
    return item if isinstance(item, list) else [item]


def parse_spots(data: dict, center: tuple[float, float]) -> list[Spot]:
    out = []
    for it in _items(data):
        try:
            km = _km(center, (float(it["mapy"]), float(it["mapx"])))
        except (KeyError, TypeError, ValueError):
            km = None
        out.append(Spot(str(it.get("title", "")).strip(), str(it.get("addr1", "")).strip(),
                        str(it.get("firstimage") or it.get("firstimage2") or ""), km,
                        str(it.get("eventstartdate") or ""), str(it.get("eventenddate") or ""), food_cat(it)))
    return out


def _common(key: str) -> dict:
    return {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "travelbot", "_type": "json", "pageNo": "1"}


async def _get(session, url: str, params: dict) -> dict:
    async with session.get(url, params=params) as r:
        text = await r.text()
    import json

    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError(f"관광공사 응답을 읽을 수 없어요: {text[:150]}")


async def fetch_spots(session, key: str, kind: str, center: tuple[float, float], area_code: int | None,
                      today: date, radius_m: int = 15000, limit: int = 8) -> list[Spot]:
    lat, lon = center
    if kind == "축제":
        # 진행 중이거나 앞으로 열릴 축제 (도시 30km 안)
        params = {**_common(key), "numOfRows": "100", "arrange": "A",
                  "eventStartDate": today.replace(day=1).strftime("%Y%m%d")}
        if area_code:
            params["areaCode"] = str(area_code)
        spots = parse_spots(await _get(session, f"{BASE}/searchFestival2", params), center)
        spots = [s for s in spots if (s.end or s.start) >= today.strftime("%Y%m%d")
                 and (s.km is None or s.km <= 30)]
        spots.sort(key=lambda s: (s.start, s.km or 0))
        return spots[:limit]
    params = {**_common(key), "numOfRows": str(limit), "arrange": "E", "mapX": f"{lon:.6f}", "mapY": f"{lat:.6f}",
              "radius": str(radius_m), "contentTypeId": str(KINDS[kind])}
    return parse_spots(await _get(session, f"{BASE}/locationBasedList2", params), center)
