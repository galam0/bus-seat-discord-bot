"""데이터 공급자. 봇은 이 인터페이스(terminals/search/label)만 안다.

버스는 여러 곳을 동시에 조회해서 합친다 (CombinedBusProvider).
- 고속버스: kobus (고속버스 통합예매)
- 시외버스: bustago (버스타고), tmoney (티머니 시외버스)
- mock_express / mock_intercity: 테스트 데이터 (같은 입력이면 같은 결과)

실제 조회 공급자는 아직 연동 전이라 ProviderUnavailable 을 낸다.
"""
import asyncio
import logging
import random
from datetime import date

from cities import CITIES, TRAIN_CITIES, place_names, terminals_of
from midpoint import est_minutes, haversine_km
from models import BusSearch, BusTrip, TrainTrip

log = logging.getLogger("seatbot")


class ProviderUnavailable(Exception):
    """사용자에게 그대로 보여줘도 되는 메시지를 담는다."""


NOT_READY = "실제 조회 연동이 아직 안 끝났어요. .env에서 BUS_PROVIDER=mock 으로 두면 테스트 데이터로 동작해요."


def _hhmm(total_minutes: int) -> str:
    m = total_minutes % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


# ---------------------------------------------------------------- 테스트 데이터

# 서울 터미널을 실제 노선 성격과 비슷하게 고르기 위한 지역 묶음
_HONAM = {"광주", "목포", "순천", "여수", "전주", "익산", "군산", "논산"}
_EAST = {"춘천", "강릉", "속초", "원주", "충주", "제천", "안동"}


def _mock_terminal(city: str, other: str, kind: str, rng: random.Random) -> str:
    """city 쪽에서 other 로 오가는 kind 버스가 쓰는 터미널을 고른다."""
    if city == "서울":
        if kind == "고속":
            if other in _HONAM:
                return "센트럴시티"
            if other in _EAST:
                return "동서울"
            return "서울경부" if rng.random() < 0.8 else "동서울"
        if other in _EAST:
            return "동서울" if rng.random() < 0.7 else "상봉"
        return "서울남부" if rng.random() < 0.7 else "동서울"
    options = terminals_of(city, kind)
    # 첫 번째(대표) 터미널이 대부분, 나머지가 가끔
    if len(options) > 1 and rng.random() < 0.3:
        return rng.choice(options[1:])
    return options[0]


class _MockBusBase:
    kind = "고속"
    label = ""
    min_km = 0  # 이보다 가까운 구간은 노선 없음
    first, last = 6 * 60, 23 * 60
    gaps = (30, 40, 50, 60)
    premium_ratio = 0.6
    fare_per_km = (125, 80)  # 우등, 일반 (원/km)

    async def terminals(self) -> list[str]:
        return place_names()

    async def search(self, dep: str, arr: str, day: date) -> list[BusTrip]:
        """dep/arr 는 도시 이름. 각 편에 출발·도착 터미널을 붙여서 돌려준다."""
        if dep not in CITIES or arr not in CITIES:
            return []
        km = haversine_km(CITIES[dep], CITIES[arr])
        if km < self.min_km:
            return []
        ride = max(est_minutes(km), 20)
        trips: list[BusTrip] = []
        t = self.first
        while t <= self.last:
            rng = random.Random(f"{self.kind}|{dep}|{arr}|{day.isoformat()}|{t}")
            premium = rng.random() < self.premium_ratio
            total = 28 if premium else 45
            if rng.random() < 0.12:
                free: tuple[int, ...] = ()
            else:
                free = tuple(sorted(rng.sample(range(1, total + 1), rng.randint(1, total - 2))))
            per_km = self.fare_per_km[0] if premium else self.fare_per_km[1]
            fare = max(int(km * per_km / 100) * 100, 3000)
            trips.append(BusTrip(_hhmm(t), _hhmm(t + ride), "우등" if premium else "일반",
                                 fare, total, free, self.kind,
                                 _mock_terminal(dep, arr, self.kind, rng),
                                 _mock_terminal(arr, dep, self.kind, rng)))
            t += rng.choice(self.gaps)
        return trips


class MockExpressProvider(_MockBusBase):
    kind = "고속"
    label = "테스트(고속)"
    min_km = 80  # 고속버스는 먼 구간 위주


class MockIntercityProvider(_MockBusBase):
    kind = "시외"
    label = "테스트(시외)"
    first, last = 6 * 60 + 20, 21 * 60
    gaps = (40, 60, 80, 90)
    premium_ratio = 0.25
    fare_per_km = (115, 85)


# ---------------------------------------------------------------- 실제 조회 (연동 전)

class _NotReadyBus:
    kind = "고속"
    label = ""

    async def terminals(self) -> list[str]:
        raise ProviderUnavailable(NOT_READY)

    async def search(self, dep: str, arr: str, day: date) -> list[BusTrip]:
        raise ProviderUnavailable(NOT_READY)


class KobusProvider(_NotReadyBus):
    """고속버스 통합예매 (kobus.co.kr)."""
    kind = "고속"
    label = "고속버스 통합예매"


class BustagoProvider(_NotReadyBus):
    """시외버스 버스타고 (bustago.or.kr)."""
    kind = "시외"
    label = "버스타고"


class TmoneyIntercityProvider(_NotReadyBus):
    """티머니 시외버스 (txbus.t-money.co.kr)."""
    kind = "시외"
    label = "티머니 시외버스"


# ---------------------------------------------------------------- 통합

class CombinedBusProvider:
    """여러 공급자를 동시에 조회해서 시간순으로 합친다. 일부가 실패하면 missing 에 남긴다."""

    def __init__(self, providers):
        self.providers = list(providers)
        self.label = " + ".join(p.label for p in self.providers)

    async def terminals(self) -> list[str]:
        results = await asyncio.gather(*(p.terminals() for p in self.providers), return_exceptions=True)
        names: dict[str, None] = {}
        errors = []
        for r in results:
            if isinstance(r, BaseException):
                errors.append(r)
            else:
                names.update(dict.fromkeys(r))
        if not names and errors:
            raise _first_message(errors)
        return list(names)

    async def search(self, dep: str, arr: str, day: date) -> BusSearch:
        results = await asyncio.gather(
            *(p.search(dep, arr, day) for p in self.providers), return_exceptions=True
        )
        trips: list[BusTrip] = []
        ok, missing, errors = [], [], []
        for p, r in zip(self.providers, results):
            if isinstance(r, BaseException):
                if not isinstance(r, ProviderUnavailable):
                    log.warning("%s 조회 실패: %r", p.label, r)
                missing.append(p.label)
                errors.append(r)
            else:
                ok.append(p.label)
                trips.extend(r)
        if not ok:
            raise _first_message(errors)
        trips.sort(key=lambda t: (t.depart, t.kind))
        return BusSearch(trips, ok, missing)


def _first_message(errors) -> ProviderUnavailable:
    for e in errors:
        if isinstance(e, ProviderUnavailable):
            return e
    return ProviderUnavailable("버스 조회에 실패했어요. 잠시 후 다시 시도해 주세요.")


# ---------------------------------------------------------------- 기차

class MockTrainProvider:
    label = "테스트 데이터 (실제 좌석 아님)"

    async def terminals(self) -> list[str]:
        return list(TRAIN_CITIES)

    async def search(self, dep: str, arr: str, day: date) -> list[TrainTrip]:
        if dep not in TRAIN_CITIES or arr not in TRAIN_CITIES:
            raise ProviderUnavailable("기차역이 있는 도시를 목록에서 골라주세요.")
        km = haversine_km(CITIES[dep], CITIES[arr])
        fast = km > 150
        ride = max(int(km * (0.5 if fast else 0.8)), 20)
        trips: list[TrainTrip] = []
        t = 5 * 60 + 30
        n = 0
        while t <= 22 * 60:
            rng = random.Random(f"T|{dep}|{arr}|{day.isoformat()}|{t}")
            general = rng.choice(("예약가능", "예약가능", "예약가능", "매진", "입석"))
            special = rng.choice(("예약가능", "매진"))
            name = f"{'KTX' if fast else 'ITX'} {101 + n}"
            trips.append(TrainTrip(name, _hhmm(t), _hhmm(t + ride), general, special))
            t += rng.choice((30, 45, 60))
            n += 1
        return trips


class TagoTrainProvider:
    """기차 실제 조회 자리. 연동 전."""

    label = "공공데이터포털 열차정보"

    async def terminals(self) -> list[str]:
        raise ProviderUnavailable(NOT_READY)

    async def search(self, dep: str, arr: str, day: date) -> list[TrainTrip]:
        raise ProviderUnavailable(NOT_READY)


# ---------------------------------------------------------------- 설정

BUS_SOURCES = {
    "mock_express": MockExpressProvider,
    "mock_intercity": MockIntercityProvider,
    "kobus": KobusProvider,
    "bustago": BustagoProvider,
    "tmoney": TmoneyIntercityProvider,
}
BUS_PRESETS = {
    "mock": "mock_express,mock_intercity",
    "real": "kobus,bustago,tmoney",
}


def get_bus_provider(spec: str) -> CombinedBusProvider:
    """spec: 'mock' / 'real' / 'kobus,bustago' 처럼 쉼표로 고르기."""
    spec = BUS_PRESETS.get(spec.strip(), spec)
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in BUS_SOURCES]
    if not names or unknown:
        raise ValueError(
            f"BUS_PROVIDER 값이 이상해요: {spec} "
            f"(mock, real 또는 {', '.join(BUS_SOURCES)} 를 쉼표로)"
        )
    return CombinedBusProvider(BUS_SOURCES[n]() for n in dict.fromkeys(names))


def get_train_provider(name: str):
    try:
        return {"mock": MockTrainProvider, "tago": TagoTrainProvider}[name]()
    except KeyError:
        raise ValueError(f"TRAIN_PROVIDER 값이 이상해요: {name} (mock 또는 tago)") from None
