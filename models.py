from dataclasses import dataclass, field


@dataclass(frozen=True)
class BusTrip:
    depart: str  # "07:30"
    arrive: str  # "11:10"
    grade: str  # 우등 / 일반 / 프리미엄
    fare: int  # 원
    seats_total: int
    free_seats: tuple[int, ...]  # 비어 있는 좌석 번호
    kind: str = "고속"  # 고속 / 시외
    dep_terminal: str = ""  # 출발 터미널 (예: 진주고속)
    arr_terminal: str = ""  # 도착 터미널 (예: 서울경부)

    @property
    def route(self) -> str:
        if self.dep_terminal and self.arr_terminal:
            return f"{self.dep_terminal} → {self.arr_terminal}"
        return ""

    @property
    def seats_left(self) -> int:
        return len(self.free_seats)

    @property
    def sold_out(self) -> bool:
        return self.seats_left == 0


@dataclass(frozen=True)
class BusSearch:
    """여러 곳을 합친 버스 조회 결과."""

    trips: list[BusTrip]
    sources: list[str]  # 조회에 성공한 곳
    missing: list[str] = field(default_factory=list)  # 조회에 실패한 곳


@dataclass(frozen=True)
class TrainTrip:
    name: str  # "KTX 101"
    depart: str
    arrive: str
    general: str  # 예약가능 / 매진 / 입석
    special: str
