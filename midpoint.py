"""여러 출발지의 중간 도시 계산 (직선거리 기반 추정)."""
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from cities import CITIES

ROAD_FACTOR = 1.25  # 직선거리 -> 도로거리 보정
AVG_KMH = 75  # 버스 평균 속도 가정 (대략 1km당 1분)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def est_minutes(km: float) -> int:
    return round(km * ROAD_FACTOR / AVG_KMH * 60)


def fmt_minutes(m: int) -> str:
    if m < 60:
        return f"{m}분"
    h, r = divmod(m, 60)
    return f"{h}시간" if r == 0 else f"{h}시간 {r}분"


@dataclass(frozen=True)
class Candidate:
    city: str
    legs: tuple[tuple[str, float, int], ...]  # (출발지, km, 분)
    max_min: int
    mean_min: float
    score: float


def find_midpoints(origins: list[str], top: int = 3) -> list[Candidate]:
    """가장 먼 사람의 이동시간과 평균 이동시간을 함께 줄이는 도시를 고른다."""
    origins = list(dict.fromkeys(origins))  # 중복 제거, 순서 유지
    if len(origins) < 2:
        raise ValueError("서로 다른 출발지가 2곳 이상 필요해요.")
    unknown = [o for o in origins if o not in CITIES]
    if unknown:
        raise KeyError(", ".join(unknown))

    result = []
    for city, pos in CITIES.items():
        legs = []
        for o in origins:
            km = haversine_km(CITIES[o], pos)
            legs.append((o, km, est_minutes(km)))
        mins = [m for _, _, m in legs]
        mx, mean = max(mins), sum(mins) / len(mins)
        result.append(Candidate(city, tuple(legs), mx, mean, 0.5 * mx + 0.5 * mean))
    result.sort(key=lambda c: c.score)
    return result[:top]
