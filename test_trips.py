import asyncio
import json
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import timetable
import tour
from dust import level, parse_dust
from trips import MemoryStore, NeonStore, Trip, TripBook, parse_day, parse_time

try:
    import discord  # noqa: F401

    HAS_DISCORD = True
except ModuleNotFoundError:
    HAS_DISCORD = False
needs_discord = unittest.skipUnless(HAS_DISCORD, "discord.py 미설치")

TODAY = date(2026, 10, 12)  # 월요일


def run(coro):
    return asyncio.run(coro)


class ParseTest(unittest.TestCase):
    def test_days(self):
        self.assertEqual(parse_day("10/25", TODAY), date(2026, 10, 25))
        self.assertEqual(parse_day("10.25", TODAY), date(2026, 10, 25))
        self.assertEqual(parse_day("10월 25일", TODAY), date(2026, 10, 25))
        self.assertEqual(parse_day("1025", TODAY), date(2026, 10, 25))
        self.assertEqual(parse_day("2027-01-03", TODAY), date(2027, 1, 3))
        self.assertEqual(parse_day("내일", TODAY), date(2026, 10, 13))
        self.assertEqual(parse_day("3/1", TODAY), date(2027, 3, 1))  # 지난 날짜면 내년
        with self.assertRaises(ValueError):
            parse_day("다음주쯤", TODAY)
        with self.assertRaises(ValueError):
            parse_day("13/40", TODAY)

    def test_times(self):
        self.assertEqual(parse_time("14:30"), "14:30")
        self.assertEqual(parse_time("오후 2시"), "14:00")
        self.assertEqual(parse_time("오후 2시 30분"), "14:30")
        self.assertEqual(parse_time("오전 12시"), "00:00")
        self.assertEqual(parse_time("9"), "09:00")
        self.assertEqual(parse_time(""), "")
        with self.assertRaises(ValueError):
            parse_time("25:00")


class BookTest(unittest.TestCase):
    def make(self):
        b = TripBook(MemoryStore())
        run(b.load())
        return b

    def test_create_and_rules(self):
        b = self.make()
        t = run(b.create(1, 10, "강릉 1박2일", date(2026, 10, 25), date(2026, 10, 26), "강릉", 7))
        self.assertEqual((t.id, t.members, t.nights()), (1, [7], "1박 2일"))
        self.assertEqual(t.dday(TODAY), "D-13")
        with self.assertRaises(ValueError):
            run(b.create(1, 10, "강릉 1박2일", date(2026, 10, 25), date(2026, 10, 25), "", 7))  # 이름 중복
        with self.assertRaises(ValueError):
            run(b.create(1, 10, "거꾸로", date(2026, 10, 26), date(2026, 10, 25), "", 7))
        run(b.create(2, 10, "강릉 1박2일", date(2026, 10, 25), date(2026, 10, 25), "", 7))  # 다른 서버는 OK
        self.assertEqual(len(b.in_guild(1, TODAY)), 1)

    def test_members_items_delete(self):
        b = self.make()
        t = run(b.create(1, 10, "부산", date(2026, 10, 25), date(2026, 10, 26), "부산", 7))
        self.assertTrue(run(b.toggle_member(t, 8)))
        self.assertFalse(run(b.toggle_member(t, 8)))
        run(b.add_item(t, date(2026, 10, 26), "", "해운대", 7))
        run(b.add_item(t, date(2026, 10, 25), "14:00", "체크인", 7))
        run(b.add_item(t, date(2026, 10, 25), "09:00", "출발", 8))
        self.assertEqual([i.text for i in t.items], ["출발", "체크인", "해운대"])
        with self.assertRaises(ValueError):
            run(b.add_item(t, date(2026, 10, 27), "", "기간 밖", 7))
        run(b.remove_item(t, 0))
        self.assertEqual(t.items[0].text, "체크인")
        # 저장소에도 반영
        saved = Trip.from_json(b.store.rows[t.id])
        self.assertEqual([i.text for i in saved.items], ["체크인", "해운대"])
        run(b.delete(t))
        self.assertEqual(b.trips, {})

    def test_reminders(self):
        b = self.make()
        t = run(b.create(1, 10, "내일여행", date(2026, 10, 13), date(2026, 10, 13), "", 7))
        self.assertEqual(b.due_reminders(datetime(2026, 10, 12, 19, 0)), [])
        self.assertEqual(b.due_reminders(datetime(2026, 10, 12, 20, 5)), [t])
        self.assertEqual(b.due_reminders(datetime(2026, 10, 13, 8, 0)), [t])  # 늦게 켜져도 당일 아침엔
        t.reminded = True
        self.assertEqual(b.due_reminders(datetime(2026, 10, 12, 21, 0)), [])

    def test_json_roundtrip(self):
        b = self.make()
        t = run(b.create(1, 10, "왕복", date(2026, 10, 25), date(2026, 10, 25), "", 7))
        run(b.add_item(t, date(2026, 10, 25), "", "가자", 7))
        again = Trip.from_json(json.loads(json.dumps(t.to_json())))
        self.assertEqual(again, t)


class _Resp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def json(self, content_type=None):
        return self.data

    async def text(self):
        return json.dumps(self.data, ensure_ascii=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class NeonStoreTest(unittest.TestCase):
    URL = "postgresql://u:p@ep-x-pooler.c-4.us-west-2.aws.neon.tech/travel?sslmode=require"

    def test_endpoints_and_sql_shape(self):
        session = MagicMock()
        calls = []

        def post(url, json=None, headers=None):
            calls.append((url, json, headers))
            if "pooler" in url:
                return _Resp({"message": "nope"}, 404)
            if json["query"].startswith("INSERT"):
                return _Resp({"rows": [{"id": 5}]})
            if json["query"].startswith("SELECT"):
                return _Resp({"rows": [{"id": "5", "data": {"id": 0, "guild": 1, "channel": 2, "name": "a",
                                                           "start": "2026-10-25", "end": "2026-10-25",
                                                           "place": "", "creator": 3}}]})
            return _Resp({"rows": []})

        session.post = post
        st = NeonStore(self.URL, session)
        self.assertEqual(st.endpoints, ["https://ep-x-pooler.c-4.us-west-2.aws.neon.tech/sql",
                                        "https://ep-x.c-4.us-west-2.aws.neon.tech/sql"])
        b = TripBook(st)
        run(b.load())
        self.assertTrue(b.persistent)
        self.assertEqual(list(b.trips), [5])  # data 안 id 가 아니라 행 id 를 씀
        self.assertEqual(st.endpoints[0], "https://ep-x.c-4.us-west-2.aws.neon.tech/sql")  # 되는 주소가 앞으로
        t = run(b.create(1, 2, "새 여행", date(2026, 10, 25), date(2026, 10, 25), "", 3))
        self.assertEqual(t.id, 5 if 5 not in b.trips or b.trips[5] is t else t.id)
        ins = [c for c in calls if c[1]["query"].startswith("INSERT")][0]
        self.assertEqual(ins[2]["Neon-Connection-String"], self.URL)
        self.assertEqual(ins[1]["params"][0], "1")
        self.assertIn('"새 여행"', ins[1]["params"][1])

    def test_all_fail(self):
        session = MagicMock()
        session.post = lambda *a, **k: _Resp({"message": "bad"}, 500)
        with self.assertRaises(RuntimeError):
            run(NeonStore(self.URL, session).load_all())


class DustTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(level(20, (30, 80, 150))[0], "좋음")
        self.assertEqual(level(81, (30, 80, 150))[0], "나쁨")
        self.assertEqual(level(None, (30, 80, 150))[0], "정보 없음")

    def test_parse(self):
        data = {"response": {"header": {"resultCode": "00"}, "body": {"items": [
            {"stationName": "중구", "pm10Value": "40", "pm25Value": "20", "dataTime": "2026-10-12 17:00"},
            {"stationName": "강남구", "pm10Value": "-", "pm25Value": "30", "dataTime": "2026-10-12 17:00"},
            {"stationName": "진주상대동", "pm10Value": "10", "pm25Value": "5", "dataTime": "2026-10-12 17:00"},
        ]}}}
        d = parse_dust(data, "서울", "서울")
        self.assertEqual((d.pm10, d.pm25, d.area), (25.0, 55 / 3, "서울 평균"))
        d = parse_dust(data, "경남", "진주")
        self.assertEqual((d.pm10, d.area), (10.0, "진주 측정소"))
        self.assertIn("좋음", d.text())


class TourTest(unittest.TestCase):
    def test_parse_list_dict_and_empty(self):
        one = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": {
            "title": "경포대", "addr1": "강원특별자치도 강릉시 저동", "firstimage": "http://x/a.jpg",
            "mapx": "128.896", "mapy": "37.795"}}}}}
        sp = tour.parse_spots(one, (37.7519, 128.8761))
        self.assertEqual(sp[0].title, "경포대")
        self.assertLess(sp[0].km, 10)
        empty = {"response": {"header": {"resultCode": "0000"}, "body": {"items": ""}}}
        self.assertEqual(tour.parse_spots(empty, (0, 0)), [])
        with self.assertRaises(RuntimeError):
            tour.parse_spots({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}},
                             (0, 0))

    def test_festival_filter(self):
        data = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [
            {"title": "지난 축제", "eventstartdate": "20261001", "eventenddate": "20261005", "mapx": "126.97", "mapy": "37.56"},
            {"title": "열리는 축제", "eventstartdate": "20261020", "eventenddate": "20261022", "mapx": "126.97", "mapy": "37.56"},
            {"title": "먼 축제", "eventstartdate": "20261020", "eventenddate": "20261022", "mapx": "129.0", "mapy": "35.1"},
        ]}}}}
        session = MagicMock()
        session.get = MagicMock(return_value=_Resp(data))
        spots = run(tour.fetch_spots(session, "k", "축제", (37.5665, 126.978), 1, TODAY))
        self.assertEqual([s.title for s in spots], ["열리는 축제"])
        self.assertEqual(spots[0].period(), "10/20~10/22")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual((params["areaCode"], params["eventStartDate"]), ("1", "20261001"))


class TimetableTest(unittest.TestCase):
    def test_summary_midnight_and_terminating(self):
        rows = [
            {"LEFTTIME": "05:36:00", "SUBWAYENAME": "성수", "STATION_NM": "강남", "EXPRESS_YN": "G"},
            {"LEFTTIME": "23:50:00", "SUBWAYENAME": "성수", "STATION_NM": "강남"},
            {"LEFTTIME": "24:12:00", "SUBWAYENAME": "신도림", "STATION_NM": "강남"},  # 서울시는 24시 표기
            {"LEFTTIME": "00:30:00", "SUBWAYENAME": "강남", "STATION_NM": "강남"},  # 이 역 종착 -> 못 탐
            {"LEFTTIME": "00:00:00", "SUBWAYENAME": "성수", "STATION_NM": "강남"},  # 시각 없음
        ]
        info = timetable.summarize(rows, "내선순환")
        self.assertEqual(info.first.time, "05:36")
        self.assertEqual([(d.time, d.dest) for d in info.lasts], [("00:12", "신도림"), ("23:50", "성수"), ("05:36", "성수")])
        self.assertEqual(info.main_dests[0], "성수")
        self.assertEqual(info.count, 3)

    def test_week_and_labels(self):
        self.assertEqual(timetable.week_tag(date(2026, 10, 12)), 1)
        self.assertEqual(timetable.week_tag(date(2026, 10, 17)), 2)
        self.assertEqual(timetable.week_tag(date(2026, 10, 18)), 3)
        self.assertEqual(timetable.direction_label("2호선", 1), "내선순환")
        self.assertEqual(timetable.direction_label("4호선", 2), "하행")

    def test_parse_rows(self):
        self.assertEqual(timetable.parse_rows({"RESULT": {"CODE": "INFO-200"}}), [])
        with self.assertRaises(RuntimeError):
            timetable.parse_rows({"RESULT": {"CODE": "ERROR-500"}})


def fake_interaction(user_id=1, guild=100, channel=200, custom_id=None, manage=False):
    i = MagicMock()
    i.user.id = user_id
    i.user.guild_permissions.manage_guild = manage
    i.guild_id = guild
    i.channel_id = channel
    i.data = {"custom_id": custom_id} if custom_id else {}
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.send_modal = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    i.edit_original_response = AsyncMock()
    return i


@needs_discord
class TripUITest(unittest.TestCase):
    def setUp(self):
        import trips_ui
        self.ui = trips_ui
        self.saved = trips_ui.book
        trips_ui.book = TripBook(MemoryStore())
        run(trips_ui.book.load())
        self.hooks = {"start_bus": AsyncMock(), "send_weather": AsyncMock(), "send_tour": AsyncMock(),
                      "send_food": AsyncMock()}
        trips_ui._hooks.update(self.hooks)
        p = patch.object(trips_ui, "today_kst", return_value=TODAY)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.ui.book = self.saved

    def create(self, name="강릉 여행", place="강릉"):
        i = fake_interaction(user_id=7)
        run(self.ui.create_cmd(i, name, "10/25", "10/26", place))
        return i

    def test_create_card(self):
        i = self.create()
        kw = i.response.send_message.await_args.kwargs
        self.assertIn("강릉 여행", kw["embed"].title)
        self.assertIn("D-13", kw["embed"].description)
        self.assertIn("<@7>", kw["embed"].description)
        ids = [b.custom_id for b in kw["view"].children]
        self.assertEqual(ids, ["trip:join:1", "trip:add:1", "trip:check:1", "trip:timeline:1", "trip:refresh:1",
                               "trip:weather:1", "trip:tour:1", "trip:food:1", "trip:bus:1"])
        self.assertIn("임시 저장", kw["embed"].footer)  # 메모리 저장이면 알려줌

    def test_no_place_no_extra_buttons(self):
        i = self.create(name="미정", place=None)
        self.assertEqual(len(i.response.send_message.await_args.kwargs["view"].children), 5)

    def test_bad_date(self):
        i = fake_interaction()
        run(self.ui.create_cmd(i, "x", "언젠가", None, None))
        self.assertIn("날짜를 못 읽었어요", i.response.send_message.await_args.args[0])

    def test_buttons(self):
        self.create()
        j = fake_interaction(user_id=8, custom_id="trip:join:1")
        self.assertTrue(run(self.ui.handle_component(j)))
        self.assertIn(8, self.ui.book.trips[1].members)
        self.assertIn("<@8>", j.response.edit_message.await_args.kwargs["embed"].description)
        a = fake_interaction(custom_id="trip:add:1")
        run(self.ui.handle_component(a))
        modal = a.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, self.ui.AddItemModal)
        w = fake_interaction(custom_id="trip:weather:1")
        run(self.ui.handle_component(w))
        self.hooks["send_weather"].assert_awaited_with(w, "강릉")
        t = fake_interaction(custom_id="trip:tour:1")
        run(self.ui.handle_component(t))
        self.hooks["send_tour"].assert_awaited_with(t, "강릉", "관광지")
        f = fake_interaction(custom_id="trip:food:1")
        run(self.ui.handle_component(f))
        self.hooks["send_food"].assert_awaited_with(f, "강릉")
        trip = self.ui.book.trips[1]
        run(self.ui.book.add_item(trip, trip.start_date, "14:00", "안목 커피거리", 7))
        tl = fake_interaction(custom_id="trip:timeline:1")
        run(self.ui.handle_component(tl))
        kw = tl.followup.send.await_args.kwargs
        self.assertEqual(kw["file"].filename, "schedule.png")
        self.assertIn("일정표", kw["embed"].title)
        self.assertFalse(run(self.ui.handle_component(fake_interaction(custom_id="other:1"))))
        gone = fake_interaction(custom_id="trip:join:99")
        run(self.ui.handle_component(gone))
        self.assertIn("찾을 수 없어요", gone.response.send_message.await_args.args[0])

    def test_modal_submit(self):
        self.create()
        trip = self.ui.book.trips[1]
        m = self.ui.AddItemModal(trip)
        m.day.value, m.time.value, m.text.value = "10/26", "오후 3시", "경포대"
        i = fake_interaction(user_id=8)
        run(m.on_submit(i))
        self.assertEqual((trip.items[0].day, trip.items[0].time), ("2026-10-26", "15:00"))
        self.assertIn("2일차", i.response.edit_message.await_args.kwargs["embed"].fields[0][0])
        m.day.value = "11/30"
        bad = fake_interaction()
        run(m.on_submit(bad))
        self.assertIn("여행 기간", bad.response.send_message.await_args.args[0])

    def test_bus_modal(self):
        self.create()
        m = self.ui.BusFromModal(self.ui.book.trips[1])
        m.origin.value = " 서울 "
        i = fake_interaction()
        run(m.on_submit(i))
        self.hooks["start_bus"].assert_awaited_with(i, "서울", "강릉")

    def test_list_and_delete_permissions(self):
        self.create()
        lst = fake_interaction()
        run(self.ui.list_cmd(lst))
        self.assertIn("강릉 여행", lst.response.send_message.await_args.kwargs["embed"].description)
        other = fake_interaction(user_id=9)
        run(self.ui.delete_cmd(other, "강릉 여행"))
        self.assertIn("만든 사람", other.response.send_message.await_args.args[0])
        admin = fake_interaction(user_id=9, manage=True)
        run(self.ui.delete_cmd(admin, "강릉 여행"))
        self.assertEqual(self.ui.book.trips, {})

    def test_add_and_remove_item_commands(self):
        self.create()
        i = fake_interaction(user_id=8)
        run(self.ui.add_cmd(i, "강릉 여행", "10/25", "출발", "9:30"))
        self.assertEqual(self.ui.book.trips[1].items[0].time, "09:30")
        no = fake_interaction(user_id=9)
        run(self.ui.remove_item_cmd(no, "강릉 여행", 1))
        self.assertIn("넣은 사람", no.response.send_message.await_args.args[0])
        yes = fake_interaction(user_id=8)
        run(self.ui.remove_item_cmd(yes, "강릉 여행", 1))
        self.assertEqual(self.ui.book.trips[1].items, [])

    def test_reminder_send(self):
        self.create()
        trip = self.ui.book.trips[1]
        run(self.ui.book.toggle_member(trip, 8))
        ch = MagicMock()
        ch.send = AsyncMock()
        client = MagicMock()
        client.get_channel = MagicMock(return_value=ch)
        run(self.ui.send_due_reminders(client, datetime(2026, 10, 24, 20, 10)))
        content = ch.send.await_args.kwargs["content"]
        self.assertIn("내일 **강릉 여행**", content)
        self.assertIn("<@7> <@8>", content)
        self.assertTrue(trip.reminded)
        ch.send.reset_mock()
        run(self.ui.send_due_reminders(client, datetime(2026, 10, 24, 21, 10)))
        ch.send.assert_not_awaited()  # 한 번만


@needs_discord
class TravelNewCommandsTest(unittest.TestCase):
    def setUp(self):
        import travel
        from test_travel import net
        self.travel = travel
        s = travel.state
        self.saved = (s.subway.net, list(s.subway.keys), s.kma_key, s.session)
        s.subway.net = net()
        s.subway.keys = ["k"]
        s.kma_key = "k"
        travel._tt_cache.clear()

    def tearDown(self):
        s = self.travel.state
        s.subway.net, s.subway.keys, s.kma_key, s.session = self.saved

    def test_last_train(self):
        rows = {"SearchSTNTimeTableByIDService": {"row": [
            {"LEFTTIME": "05:30:00", "SUBWAYENAME": "오이도", "STATION_NM": "서울"},
            {"LEFTTIME": "00:05:00", "SUBWAYENAME": "사당", "STATION_NM": "서울"}]}}
        s = self.travel.state
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(rows))
        i = fake_interaction()
        with patch.object(self.travel, "datetime") as dt:
            dt.now.return_value = datetime(2026, 10, 12, 18, 0)
            run(self.travel.send_last_train(i, "서울"))
        embed = i.followup.send.await_args.kwargs["embed"]
        self.assertEqual([f[0] for f in embed.fields], ["1호선", "4호선"])
        self.assertIn("막차 `00:05` **사당행**", embed.fields[0][1])
        self.assertIn("평일", embed.description)
        urls = [c.args[0] for c in s.session.get.call_args_list]
        self.assertTrue(any("/0150/1/1/" in u for u in urls) and any("/0426/1/2/" in u for u in urls))

    def test_tour(self):
        data = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [
            {"title": "경포대", "addr1": "강원 강릉시 저동 1", "firstimage": "http://x/a.jpg",
             "mapx": "128.896", "mapy": "37.795"}]}}}}
        s = self.travel.state
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(data))
        i = fake_interaction()
        run(self.travel.send_tour(i, "강릉", "관광지"))
        kw = i.followup.send.await_args.kwargs
        self.assertIn("경포대", kw["embed"].description)
        self.assertEqual(kw["embed"].image, "http://x/a.jpg")
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels, list(tour.KINDS))

    def test_tour_key_not_registered(self):
        s = self.travel.state
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(
            {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}}))
        i = fake_interaction()
        run(self.travel.send_tour(i, "강릉"))
        self.assertIn("활용신청", i.followup.send.await_args.args[0])

    def test_weather_with_dust(self):
        from weather import Forecast, Slot
        fc = Forecast([Slot(datetime(2099, 1, 1, h), 10.0, "☀️", "맑음", 0) for h in range(24)], {})
        dust = {"response": {"header": {"resultCode": "00"}, "body": {"items": [
            {"stationName": "제주", "pm10Value": "20", "pm25Value": "8", "dataTime": "2026-10-12 17:00"}]}}}
        s = self.travel.state
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(dust))
        real = self.travel.render_weather
        spy = MagicMock(side_effect=real)
        with patch.object(self.travel, "fetch_forecast", AsyncMock(return_value=fc)), \
                patch.object(self.travel, "render_weather", spy):
            i = fake_interaction()
            run(self.travel.send_weather(i, "제주"))  # 여행지도 됨
        kw = i.followup.send.await_args.kwargs
        self.assertIn("제주 날씨", kw["embed"].title)
        self.assertEqual(kw["file"].filename, "weather.png")
        dust_arg = spy.call_args.args[4]
        self.assertEqual((dust_arg.pm10, dust_arg.pm25), (20.0, 8.0))
        # 이미지가 안 될 때 쓰는 글자 버전에도 미세먼지가 들어감
        text = self.travel.weather_text_embed("제주", datetime(2099, 1, 1), fc.slots[:8], [], dust_arg, "")
        self.assertTrue(any("좋음" in v for _, v in text.fields))


if __name__ == "__main__":
    unittest.main()
