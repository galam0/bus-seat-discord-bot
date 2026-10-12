"""여행 일정: 서버(길드)별 여행을 만들고, 참가자·일정을 모으고, 출발 전날 알림을 보낸다.

저장: TRAVEL_DATABASE_URL 이 있으면 Neon(Postgres)에 HTTP 로 저장, 없으면 메모리(재시작하면 사라짐).
DB 를 자주 깨우지 않도록 시작할 때 한 번 다 읽어 메모리에 두고, 바뀔 때만 쓴다.
"""
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

log = logging.getLogger("seatbot.trips")

WEEK = "월화수목금토일"
REMIND_HOUR = 20  # 출발 전날 저녁 8시에 알림


# ---------------------------------------------------------------- 자료형

@dataclass
class Item:
    day: str  # YYYY-MM-DD
    time: str  # 'HH:MM' 또는 ''
    text: str
    by: int


@dataclass
class Poll:
    id: int
    question: str
    options: list[str]
    votes: list[list[int]]  # 선택지별 투표한 사람
    by: int
    closed: bool = False

    def total_voters(self) -> int:
        return len({u for v in self.votes for u in v})


@dataclass
class Check:
    text: str
    owner: int = 0  # 챙기기로 한 사람 (0 = 아직 없음)
    done: bool = False
    by: int = 0


DEFAULT_CHECKS = ["신분증", "보조배터리", "충전기", "세면도구", "상비약", "우산"]


@dataclass
class Trip:
    id: int
    guild: int
    channel: int
    name: str
    start: str  # YYYY-MM-DD
    end: str
    place: str
    creator: int
    members: list[int] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    reminded: bool = False
    polls: list[Poll] = field(default_factory=list)
    checklist: list[Check] = field(default_factory=list)

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.end)

    def nights(self) -> str:
        n = (self.end_date - self.start_date).days
        return "당일치기" if n == 0 else f"{n}박 {n + 1}일"

    def dday(self, today: date) -> str:
        d = (self.start_date - today).days
        if d > 0:
            return f"D-{d}"
        if today <= self.end_date:
            return "여행 중"
        return "다녀옴"

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Trip":
        d = dict(d)
        d["items"] = [Item(**i) for i in d.get("items", [])]
        d["polls"] = [Poll(**p) for p in d.get("polls", [])]
        d["checklist"] = [Check(**c) for c in d.get("checklist", [])]
        return Trip(**d)


# ---------------------------------------------------------------- 날짜 읽기

def parse_day(text: str, today: date) -> date:
    """'10/25', '10.25', '1025', '2026-10-25', '오늘', '내일', '모레' -> date. 지난 날짜(월/일만)면 내년."""
    s = text.strip().replace(" ", "")
    rel = {"오늘": 0, "내일": 1, "모레": 2, "글피": 3}
    if s in rel:
        return today + timedelta(days=rel[s])
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[-./월](\d{1,2})일?", s) or re.fullmatch(r"(\d{2})(\d{2})", s)
    if m:
        d = date(today.year, int(m[1]), int(m[2]))
        return d if d >= today else date(today.year + 1, int(m[1]), int(m[2]))
    raise ValueError(f"날짜를 못 읽었어요: '{text}' (예: 10/25, 2026-10-25, 내일)")


def parse_time(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(오전|오후)?\s*(\d{1,2})\s*(?:(?::|시)\s*(?:(\d{1,2})\s*분?)?)?", s)
    if not m:
        raise ValueError(f"시간을 못 읽었어요: '{text}' (예: 14:30, 오후 2시)")
    h, mi = int(m[2]), int(m[3] or 0)
    if m[1] == "오후" and h < 12:
        h += 12
    if m[1] == "오전" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"시간이 이상해요: '{text}'")
    return f"{h:02d}:{mi:02d}"


def day_label(d: date) -> str:
    return f"{d.month}/{d.day}({WEEK[d.weekday()]})"


# ---------------------------------------------------------------- 저장소

class MemoryStore:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.next_id = 1

    async def load_all(self) -> list[Trip]:
        return [Trip.from_json(r) for r in self.rows.values()]

    async def insert(self, trip: Trip) -> int:
        trip.id = self.next_id
        self.next_id += 1
        self.rows[trip.id] = trip.to_json()
        return trip.id

    async def update(self, trip: Trip) -> None:
        self.rows[trip.id] = trip.to_json()

    async def delete(self, trip_id: int) -> None:
        self.rows.pop(trip_id, None)


class NeonStore:
    """Neon 의 HTTP SQL 로 저장 (추가 라이브러리 없이 aiohttp 만 사용)."""

    def __init__(self, url: str, session):
        self.url = url
        self.session = session
        host = urlparse(url).hostname or ""
        hosts = [host]
        if "-pooler" in host:
            hosts.append(host.replace("-pooler", ""))
        self.endpoints = [f"https://{h}/sql" for h in hosts]

    async def _sql(self, query: str, params: list):
        last = None
        for ep in list(self.endpoints):
            try:
                async with self.session.post(
                    ep, json={"query": query, "params": params},
                    headers={"Neon-Connection-String": self.url, "Neon-Raw-Text-Output": "false"},
                ) as r:
                    data = await r.json(content_type=None)
                    if r.status >= 400:
                        raise RuntimeError(f"{r.status} {str(data)[:200]}")
                if ep != self.endpoints[0]:  # 되는 주소를 앞으로
                    self.endpoints.remove(ep)
                    self.endpoints.insert(0, ep)
                return data.get("rows", [])
            except Exception as e:
                last = e
        raise RuntimeError(f"DB 요청 실패: {last}")

    async def check(self) -> str:
        """쓰기 없이 매개변수 전달까지 확인 (정수·JSON)."""
        rows = await self._sql("SELECT $1::bigint AS a, ($2::jsonb)->>'x' AS b", [5, json.dumps({"x": "가"})])
        return f"{rows[0]['a']}/{rows[0]['b']}"

    async def setup(self):
        await self._sql("CREATE TABLE IF NOT EXISTS trips (id BIGSERIAL PRIMARY KEY, guild_id TEXT NOT NULL, "
                        "data JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())", [])

    async def load_all(self) -> list[Trip]:
        rows = await self._sql("SELECT id, data FROM trips", [])
        out = []
        for r in rows:
            d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
            d["id"] = int(r["id"])
            out.append(Trip.from_json(d))
        return out

    async def insert(self, trip: Trip) -> int:
        rows = await self._sql("INSERT INTO trips (guild_id, data) VALUES ($1, $2::jsonb) RETURNING id",
                               [str(trip.guild), json.dumps(trip.to_json(), ensure_ascii=False)])
        trip.id = int(rows[0]["id"])
        await self.update(trip)  # data 안의 id 도 맞춰 둔다
        return trip.id

    async def update(self, trip: Trip) -> None:
        await self._sql("UPDATE trips SET data = $2::jsonb, updated_at = now() WHERE id = $1",
                        [trip.id, json.dumps(trip.to_json(), ensure_ascii=False)])

    async def delete(self, trip_id: int) -> None:
        await self._sql("DELETE FROM trips WHERE id = $1", [trip_id])


# ---------------------------------------------------------------- 여행 목록 (메모리)

class TripBook:
    def __init__(self, store):
        self.store = store
        self.trips: dict[int, Trip] = {}
        self.persistent = isinstance(store, NeonStore)

    async def load(self):
        if hasattr(self.store, "setup"):
            await self.store.setup()
        self.trips = {t.id: t for t in await self.store.load_all()}

    def in_guild(self, guild: int, today: date | None = None, include_past: bool = False) -> list[Trip]:
        out = [t for t in self.trips.values() if t.guild == guild]
        if today and not include_past:
            out = [t for t in out if t.end_date >= today]
        return sorted(out, key=lambda t: (t.start, t.name))

    def find(self, guild: int, name: str) -> Trip | None:
        name = name.strip()
        for t in self.trips.values():
            if t.guild == guild and (t.name == name or str(t.id) == name):
                return t
        return None

    async def create(self, guild: int, channel: int, name: str, start: date, end: date, place: str,
                     creator: int) -> Trip:
        name = name.strip()[:40]
        if not name:
            raise ValueError("여행 이름을 적어 주세요.")
        if end < start:
            raise ValueError("끝나는 날이 시작하는 날보다 빨라요.")
        if (end - start).days > 60:
            raise ValueError("여행 기간은 60일까지만 돼요.")
        if self.find(guild, name):
            raise ValueError(f"'{name}' 여행이 이미 있어요. 다른 이름을 써 주세요.")
        trip = Trip(0, guild, channel, name, start.isoformat(), end.isoformat(), place, creator, [creator])
        await self.store.insert(trip)
        self.trips[trip.id] = trip
        return trip

    async def save(self, trip: Trip):
        await self.store.update(trip)

    async def toggle_member(self, trip: Trip, user: int) -> bool:
        """참가하면 True, 빠지면 False."""
        if user in trip.members:
            trip.members.remove(user)
            joined = False
        else:
            trip.members.append(user)
            joined = True
        await self.save(trip)
        return joined

    async def add_item(self, trip: Trip, day: date, time: str, text: str, by: int) -> Item:
        text = text.strip()[:100]
        if not text:
            raise ValueError("일정 내용을 적어 주세요.")
        if not (trip.start_date <= day <= trip.end_date):
            raise ValueError(f"여행 기간({day_label(trip.start_date)}~{day_label(trip.end_date)}) 안의 날짜로 적어 주세요.")
        if len(trip.items) >= 50:
            raise ValueError("일정은 50개까지만 넣을 수 있어요.")
        item = Item(day.isoformat(), time, text, by)
        trip.items.append(item)
        trip.items.sort(key=lambda i: (i.day, i.time or "99:99"))
        await self.save(trip)
        return item

    async def remove_item(self, trip: Trip, index: int) -> Item:
        item = trip.items.pop(index)
        await self.save(trip)
        return item

    # ---- 투표
    def poll(self, trip: Trip, poll_id: int) -> Poll | None:
        return next((p for p in trip.polls if p.id == poll_id), None)

    async def add_poll(self, trip: Trip, question: str, options: list[str], by: int) -> Poll:
        question = question.strip()[:100]
        opts = [o.strip()[:50] for o in options if o.strip()]
        opts = list(dict.fromkeys(opts))
        if not question:
            raise ValueError("질문을 적어 주세요.")
        if not (2 <= len(opts) <= 10):
            raise ValueError("선택지는 2~10개를 쉼표로 나눠 적어 주세요. (예: 10/25, 11/1, 11/8)")
        if len(trip.polls) >= 20:
            raise ValueError("투표는 여행마다 20개까지만 돼요.")
        pid = max((p.id for p in trip.polls), default=0) + 1
        poll = Poll(pid, question, opts, [[] for _ in opts], by)
        trip.polls.append(poll)
        await self.save(trip)
        return poll

    async def toggle_vote(self, trip: Trip, poll: Poll, index: int, user: int) -> bool:
        """투표하면 True, 취소하면 False. 여러 개 골라도 된다."""
        if poll.closed:
            raise ValueError("마감된 투표예요.")
        voters = poll.votes[index]
        if user in voters:
            voters.remove(user)
            voted = False
        else:
            voters.append(user)
            voted = True
        await self.save(trip)
        return voted

    async def close_poll(self, trip: Trip, poll: Poll) -> None:
        poll.closed = True
        await self.save(trip)

    # ---- 준비물
    async def add_checks(self, trip: Trip, texts: list[str], by: int) -> list[Check]:
        have = {c.text for c in trip.checklist}
        added = []
        for t in texts:
            t = t.strip()[:40]
            if t and t not in have and len(trip.checklist) < 25:
                c = Check(t, by=by)
                trip.checklist.append(c)
                have.add(t)
                added.append(c)
        if not added:
            raise ValueError("새로 넣을 준비물이 없어요. (이미 있거나, 25개가 꽉 찼어요)")
        await self.save(trip)
        return added

    async def toggle_claim(self, trip: Trip, index: int, user: int) -> bool:
        c = trip.checklist[index]
        if c.owner == user:
            c.owner = 0
            claimed = False
        else:
            c.owner = user
            claimed = True
        await self.save(trip)
        return claimed

    async def toggle_done(self, trip: Trip, index: int) -> bool:
        c = trip.checklist[index]
        c.done = not c.done
        await self.save(trip)
        return c.done

    async def remove_check(self, trip: Trip, index: int) -> Check:
        c = trip.checklist.pop(index)
        await self.save(trip)
        return c

    async def delete(self, trip: Trip):
        await self.store.delete(trip.id)
        self.trips.pop(trip.id, None)

    def due_reminders(self, now: datetime) -> list[Trip]:
        """출발 전날 REMIND_HOUR 시 이후 ~ 출발일 사이, 아직 안 보낸 여행."""
        today = now.date()
        out = []
        for t in self.trips.values():
            if t.reminded:
                continue
            if t.start_date == today + timedelta(days=1) and now.hour >= REMIND_HOUR:
                out.append(t)
            elif t.start_date == today and now.hour < 12:  # 봇이 꺼져 있다 늦게 켜진 경우
                out.append(t)
        return out
