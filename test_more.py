"""투표·준비물·혼잡도·여행지 룰렛·기차 시간표 테스트."""
import asyncio
import random
import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import congestion
import roulette
from providers import ProviderUnavailable, TagoTrainProvider, parse_tago_trains
from trips import DEFAULT_CHECKS, MemoryStore, TripBook
from test_trips import TODAY, _Resp, fake_interaction, needs_discord, run

CROWD = {"SeoulRtd.citydata_ppltn": [{
    "AREA_NM": "홍대 관광특구", "AREA_CONGEST_LVL": "보통", "AREA_CONGEST_MSG": "크게 붐비지는 않아요.",
    "AREA_PPLTN_MIN": "82000", "AREA_PPLTN_MAX": "84000", "PPLTN_TIME": "2026-10-12 18:00",
    "FCST_PPLTN": [
        {"FCST_TIME": "2026-10-12 19:00", "FCST_CONGEST_LVL": "약간 붐빔", "FCST_PPLTN_MIN": "78000", "FCST_PPLTN_MAX": "80000"},
        {"FCST_TIME": "2026-10-12 20:00", "FCST_CONGEST_LVL": "보통", "FCST_PPLTN_MIN": "70000", "FCST_PPLTN_MAX": "72000"},
        {"FCST_TIME": "2026-10-13 03:00", "FCST_CONGEST_LVL": "여유", "FCST_PPLTN_MIN": "9000", "FCST_PPLTN_MAX": "9500"},
    ]}]}


class BookPollCheckTest(unittest.TestCase):
    def make(self):
        b = TripBook(MemoryStore())
        run(b.load())
        t = run(b.create(1, 10, "부산", date(2026, 10, 25), date(2026, 10, 26), "부산", 7))
        return b, t

    def test_poll_rules(self):
        b, t = self.make()
        with self.assertRaises(ValueError):
            run(b.add_poll(t, "언제?", ["10/25"], 7))  # 하나뿐
        p = run(b.add_poll(t, "언제?", ["10/25", " 11/1 ", "10/25"], 7))
        self.assertEqual(p.options, ["10/25", "11/1"])  # 중복·공백 정리
        self.assertTrue(run(b.toggle_vote(t, p, 0, 8)))
        self.assertTrue(run(b.toggle_vote(t, p, 1, 8)))  # 여러 개 가능
        self.assertFalse(run(b.toggle_vote(t, p, 0, 8)))  # 다시 누르면 취소
        self.assertEqual((p.votes, p.total_voters()), ([[], [8]], 1))
        run(b.close_poll(t, p))
        with self.assertRaises(ValueError):
            run(b.toggle_vote(t, p, 0, 9))
        p2 = run(b.add_poll(t, "어디?", ["바다", "산"], 7))
        self.assertEqual(p2.id, 2)

    def test_checklist(self):
        b, t = self.make()
        run(b.add_checks(t, DEFAULT_CHECKS, 7))
        with self.assertRaises(ValueError):
            run(b.add_checks(t, ["신분증"], 7))  # 이미 있음
        run(b.add_checks(t, ["수영복", "  ", "선크림"], 8))
        self.assertEqual(len(t.checklist), len(DEFAULT_CHECKS) + 2)
        self.assertTrue(run(b.toggle_claim(t, 0, 8)))
        self.assertEqual(t.checklist[0].owner, 8)
        self.assertTrue(run(b.toggle_claim(t, 0, 9)))  # 다른 사람이 가져감
        self.assertEqual(t.checklist[0].owner, 9)
        self.assertFalse(run(b.toggle_claim(t, 0, 9)))
        self.assertTrue(run(b.toggle_done(t, 1)))
        run(b.add_checks(t, [f"물건{i}" for i in range(40)], 7))
        self.assertEqual(len(t.checklist), 25)  # 25개까지 (선택 메뉴 한도)


class CongestionTest(unittest.TestCase):
    def test_parse(self):
        c = congestion.parse_crowd(CROWD)
        self.assertEqual((c.area, c.level, c.low, c.emoji), ("홍대 관광특구", "보통", 82000, "🟡"))
        self.assertEqual([f.time for f in c.forecast], ["19시", "20시", "03시"])
        self.assertEqual(c.quietest().time, "03시")
        self.assertIsNone(congestion.parse_crowd({"RESULT": {"CODE": "INFO-200"}}))

    def test_people(self):
        self.assertEqual(congestion.fmt_people(82000, 84000), "8.2만~8.4만명")
        self.assertEqual(congestion.fmt_people(3500, 10000), "3,500~1만명")

    def test_area_search_and_verify(self):
        a = congestion.AreaList()
        self.assertEqual(a.search("홍대")[:2], ["홍대 관광특구", "홍대입구역(2호선)"])
        self.assertIn("성수카페거리", a.search("성수"))
        good = {"홍대 관광특구", "강남역"}

        async def fake(session, key, name):
            return congestion.parse_crowd(CROWD) if name in good else None

        with patch.object(congestion, "fetch_crowd", fake):
            run(a.verify(None, "k", delay=0))
        self.assertEqual(sorted(a.valid), sorted(good))
        self.assertTrue(a.checked)


class RouletteTest(unittest.TestCase):
    def test_candidates(self):
        c = roulette.candidates("서울", 120, None)
        names = {p.place for p in c}
        self.assertIn("춘천", names)
        self.assertNotIn("부산", names)  # 2시간 넘음
        self.assertNotIn("서울", names)
        self.assertNotIn("의정부", names)  # 너무 가까움
        self.assertNotIn("제주", names)  # 시간 조건 있으면 섬 빼기
        self.assertIn("제주", {p.place for p in roulette.candidates("서울", None, "바다")})
        sea = roulette.candidates("서울", 240, "바다")
        self.assertTrue(sea and all("바다" in p.themes for p in sea))

    def test_pick_avoids_seen(self):
        rng = random.Random(1)
        first, n = roulette.pick("서울", 180, "바다", rng)
        seen = {first.place}
        for _ in range(n - 1):
            p, _ = roulette.pick("서울", 180, "바다", rng, avoid=seen)
            self.assertNotIn(p.place, seen)
            seen.add(p.place)
        self.assertEqual(roulette.pick("서울", 30, None)[0], None)

    def test_no_origin(self):
        c = roulette.candidates(None, None, "바다")
        names = {p.place for p in c}
        self.assertIn("부산", names)
        self.assertIn("제주", names)
        self.assertTrue(all(p.km is None for p in c))
        busan = next(p for p in c if p.place == "부산")
        self.assertEqual(busan.travel_text(), "")
        self.assertIn("비행기", next(p for p in c if p.place == "제주").travel_text())
        self.assertEqual(len(roulette.candidates(None, None, None)), len(roulette.INFO))

    def test_every_place_has_info(self):
        from cities import PLACES
        self.assertEqual(set(roulette.INFO) - set(PLACES), set())


TAGO_ROWS = [
    {"depplandtime": 20261013063000, "arrplandtime": 20261013090000, "traingradename": "KTX", "trainno": 101,
     "adultcharge": 59800, "depplacename": "서울", "arrplacename": "부산"},
    {"depplandtime": "20261013052800", "arrplandtime": "20261013102000", "traingradename": "무궁화호",
     "trainno": "1201", "adultcharge": "28600"},
]


class TagoTest(unittest.TestCase):
    def test_parse(self):
        trips = parse_tago_trains(TAGO_ROWS)
        self.assertEqual([(t.depart, t.name, t.fare) for t in trips],
                         [("05:28", "무궁화호 1201", 28600), ("06:30", "KTX 101", 59800)])
        self.assertEqual(trips[1].general, "")

    def test_provider(self):
        def get(url, params=None):
            op = url.rsplit("/", 1)[1]
            self.assertTrue(url.startswith("https://apis.data.go.kr/1613000/TrainInfo/"))
            if op == "GetCtyCodeList":
                return _Resp({"response": {"body": {"items": {"item": [{"citycode": 11}, {"citycode": 26}]}}}})
            if op == "GetCtyAcctoTrainSttnList":
                st = {"11": [{"nodeid": "NAT010000", "nodename": "서울"}],
                      "26": {"nodeid": "NAT014445", "nodename": "부산"}}[params["cityCode"]]
                return _Resp({"response": {"body": {"items": {"item": st}}}})
            if op == "GetStrtpntAlocFndTrainInfo":
                self.assertEqual((params["depPlaceId"], params["arrPlaceId"], params["depPlandTime"]),
                                 ("NAT010000", "NAT014445", "20261013"))
                return _Resp({"response": {"body": {"items": {"item": TAGO_ROWS}}}})
            raise AssertionError(op)

        session = MagicMock()
        session.get = get
        p = TagoTrainProvider("k", session)
        self.assertEqual(run(p.terminals()), ["부산", "서울"])
        trips = run(p.search("서울", "부산", date(2026, 10, 13)))
        self.assertEqual(len(trips), 2)
        with self.assertRaises(ProviderUnavailable):
            run(p.search("서울", "목포", date(2026, 10, 13)))

    def test_not_registered(self):
        session = MagicMock()
        session.get = lambda url, params=None: _Resp(
            {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}})
        with self.assertRaisesRegex(ProviderUnavailable, "활용신청"):
            run(TagoTrainProvider("k", session).terminals())
        with self.assertRaisesRegex(ProviderUnavailable, "KMA_API_KEY"):
            run(TagoTrainProvider("", session).terminals())


@needs_discord
class PollCheckUITest(unittest.TestCase):
    def setUp(self):
        import trips_ui
        self.ui = trips_ui
        self.saved = trips_ui.book
        trips_ui.book = TripBook(MemoryStore())
        run(trips_ui.book.load())
        p = patch.object(trips_ui, "today_kst", return_value=TODAY)
        p.start()
        self.addCleanup(p.stop)
        run(trips_ui.create_cmd(fake_interaction(user_id=7), "부산 여행", "10/25", "10/26", "부산"))
        self.trip = trips_ui.book.trips[1]

    def tearDown(self):
        self.ui.book = self.saved

    def test_poll_flow(self):
        i = fake_interaction(user_id=7)
        run(self.ui.poll_cmd(i, "부산 여행", "언제 갈까?", "10/25, 11/1, 11/8"))
        kw = i.response.send_message.await_args.kwargs
        ids = [c.custom_id for c in kw["view"].children]
        self.assertEqual(ids, ["poll:1:1:0", "poll:1:1:1", "poll:1:1:2", "poll:1:1:close"])
        v = fake_interaction(user_id=8, custom_id="poll:1:1:1")
        run(self.ui.handle_component(v))
        e = v.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("1표 <@8>", e.description)
        self.assertIn("1명 참여", e.footer)
        no = fake_interaction(user_id=9, custom_id="poll:1:1:close")
        run(self.ui.handle_component(no))
        self.assertIn("마감할 수 있어요", no.response.send_message.await_args.args[0])
        yes = fake_interaction(user_id=7, custom_id="poll:1:1:close")
        run(self.ui.handle_component(yes))
        kw = yes.response.edit_message.await_args.kwargs
        self.assertIn("👑", kw["embed"].description)
        self.assertTrue(all(c.disabled for c in kw["view"].children))
        late = fake_interaction(user_id=10, custom_id="poll:1:1:0")
        run(self.ui.handle_component(late))
        self.assertIn("마감된 투표", late.response.send_message.await_args.args[0])
        bad = fake_interaction()
        run(self.ui.poll_cmd(bad, "부산 여행", "?", "하나만"))
        self.assertIn("2~10개", bad.response.send_message.await_args.args[0])
        # 카드에 진행 중 투표 요약
        card = self.ui.trip_embed(self.trip, TODAY)
        self.assertFalse(any("진행 중인 투표" in v for _, v in card.fields))  # 마감돼서 없음

    def test_checklist_flow(self):
        i = fake_interaction(user_id=7)
        run(self.ui.check_cmd(i, "부산 여행", None))  # 비우면 기본 목록
        kw = i.response.send_message.await_args.kwargs
        self.assertEqual(len(self.trip.checklist), len(DEFAULT_CHECKS))
        self.assertIn("⬜ 신분증", kw["embed"].description)
        selects = [c for c in kw["view"].children if hasattr(c, "options")]
        self.assertEqual([s.custom_id for s in selects], ["check:1:claim", "check:1:done"])
        c = fake_interaction(user_id=8, custom_id="check:1:claim")
        c.data["values"] = ["0"]
        run(self.ui.handle_component(c))
        self.assertIn("신분증 — <@8>", c.response.edit_message.await_args.kwargs["embed"].description)
        d = fake_interaction(user_id=8, custom_id="check:1:done")
        d.data["values"] = ["0"]
        run(self.ui.handle_component(d))
        self.assertIn("✅ ~~신분증~~", d.response.edit_message.await_args.kwargs["embed"].description)
        stale = fake_interaction(custom_id="check:1:done")
        stale.data["values"] = ["99"]
        run(self.ui.handle_component(stale))
        self.assertIn("새로고침", stale.response.send_message.await_args.args[0])
        add = fake_interaction(custom_id="check:1:add")
        run(self.ui.handle_component(add))
        m = add.response.send_modal.await_args.args[0]
        m.items.value = "수영복\n선크림, 카드"
        s = fake_interaction(user_id=9)
        run(m.on_submit(s))
        self.assertEqual([x.text for x in self.trip.checklist[-3:]], ["수영복", "선크림", "카드"])
        card = self.ui.trip_embed(self.trip, TODAY)
        self.assertTrue(any("준비물 1/9 챙김" in v for _, v in card.fields))
        btn = fake_interaction(custom_id="trip:check:1")
        run(self.ui.handle_component(btn))
        self.assertIn("준비물", btn.response.send_message.await_args.kwargs["embed"].title)

    def test_create_from_modal(self):
        m = self.ui.CreateTripModal("강릉")
        m.start.value, m.end.value = "10/30", ""
        i = fake_interaction(user_id=5)
        run(m.on_submit(i))
        trip = self.ui.book.find(100, "강릉 여행")
        self.assertEqual((trip.place, trip.start, trip.end, trip.members), ("강릉", "2026-10-30", "2026-10-30", [5]))


@needs_discord
class CrowdRouletteTrainUITest(unittest.TestCase):
    def setUp(self):
        import travel
        self.travel = travel
        s = travel.state
        self.saved = (list(s.subway.keys), s.session, list(s.areas.valid))
        s.subway.keys = ["k"]

    def tearDown(self):
        s = self.travel.state
        s.subway.keys, s.session, s.areas.valid = self.saved

    def test_crowd(self):
        s = self.travel.state
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(CROWD))
        i = fake_interaction()
        run(self.travel.send_crowd(i, "홍대 관광특구"))
        e = i.followup.send.await_args.kwargs["embed"]
        self.assertIn("지금 보통", e.title)
        self.assertIn("8.2만~8.4만명", e.description)
        self.assertEqual(e.fields[0][0], "앞으로 6시간")
        self.assertIn("03시", e.fields[1][1])
        many = fake_interaction()
        run(self.travel.send_crowd(many, "홍대"))  # 여러 곳이 맞으면 고르게
        self.assertIn("홍대 관광특구", many.response.send_message.await_args.args[0])
        none = fake_interaction()
        run(self.travel.send_crowd(none, "뉴욕"))
        self.assertIn("그런 장소가 없어요", none.response.send_message.await_args.args[0])

    def test_roulette(self):
        bus = AsyncMock()
        i = fake_interaction()
        with patch.object(roulette.random, "Random", return_value=random.Random(3)):
            run(self.travel.send_roulette(i, "서울", "3시간", "바다", start_bus=bus))
        kw = i.response.send_message.await_args.kwargs
        place = kw["view"].pick.place
        self.assertIn(f"📍 {place}", kw["embed"].description)
        self.assertIn("3시간", kw["embed"].footer)
        labels = [c.label for c in kw["view"].children]
        self.assertEqual(labels, ["다시 뽑기", "날씨", "관광", "버스", "여행으로 만들기"])
        r = fake_interaction()
        run(kw["view"].reroll(r))
        new = r.response.edit_message.await_args.kwargs["view"].pick.place
        self.assertNotEqual(new, place)  # 방금 나온 곳은 피함
        b = fake_interaction()
        run(kw["view"].bus(b))
        bus.assert_awaited_with(b, "서울", place)
        none = fake_interaction()
        run(self.travel.send_roulette(none, "서울", "1시간", "바다"))
        self.assertIn("조건에 맞는 곳이 없어요", none.response.send_message.await_args.args[0])

    def test_roulette_no_origin(self):
        i = fake_interaction()
        run(self.travel.send_roulette(i, None, "2시간", None))
        kw = i.response.send_message.await_args.kwargs
        self.assertEqual(kw["embed"].title, "🎲 이번 여행지는…")
        self.assertIn("출발지를 넣어야", kw["embed"].footer)
        self.assertIn(f"후보 {len(roulette.INFO)}곳", kw["embed"].footer)
        bus = next(c for c in kw["view"].children if c.label == "버스")
        self.assertTrue(bus.disabled)
        r = fake_interaction()
        run(kw["view"].reroll(r))
        self.assertEqual(r.response.edit_message.await_args.kwargs["embed"].title, "🎲 이번 여행지는…")

    def test_train_embed_real(self):
        from embeds import train_embed
        e = train_embed("서울", "부산", "10/13(화)", parse_tago_trains(TAGO_ROWS), "국토교통부 열차정보")
        self.assertIn("`06:30→09:00` **KTX 101** · 59,800원 · 2시간 30분", e.description)
        self.assertIn("KTX 1편", e.description)
        self.assertIn("시간표·요금만", e.footer)


FOOD = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [
    {"title": "가 식당", "addr1": "전북 전주시 1", "mapx": "127.15", "mapy": "35.82", "cat3": "A05020100",
     "firstimage": "https://example.com/a.jpg"},
    {"title": "나 카페", "addr1": "전북 전주시 2", "mapx": "127.14", "mapy": "35.81", "cat3": "A05020900"},
    {"title": "다 국수", "addr1": "", "mapx": "127.13", "mapy": "35.80", "cat3": "Z"},
]}}}}


class FoodTest(unittest.TestCase):
    def setUp(self):
        import travel
        self.travel = travel
        self.saved = (travel.state.session, travel.state.kma_key)
        travel.state.kma_key = "k"
        travel.state.session = MagicMock()
        travel.state.session.get = MagicMock(return_value=_Resp(FOOD))
        travel._food_cache.clear()

    def tearDown(self):
        self.travel.state.session, self.travel.state.kma_key = self.saved
        self.travel._food_cache.clear()

    def test_parse_cat(self):
        import tour
        spots = tour.parse_spots(FOOD, (35.82, 127.15))
        self.assertEqual([s.cat for s in spots], ["한식", "카페·찻집", ""])

    def test_pick_and_reroll(self):
        i = fake_interaction()
        run(self.travel.send_food(i, "전주"))
        kw = i.followup.send.await_args.kwargs
        self.assertEqual(kw["embed"].title, "🍽️ 전주에서 뭐 먹지?")
        self.assertIn("3곳 중", kw["embed"].footer)
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels, ["다시 뽑기", "지도", "맛집 목록"])
        self.assertTrue(kw["view"].children[1].url.startswith("https://map.kakao.com/link/search/"))
        first = kw["view"].seen
        seen_titles = set(first)
        view = kw["view"]
        for _ in range(2):  # 다시 뽑으면 방금 나온 곳은 안 나옴
            r = fake_interaction()
            run(view.reroll(r))
            view = r.edit_original_response.await_args.kwargs["view"]
            new = next(iter(view.seen - seen_titles), None)
            self.assertIsNotNone(new)
            seen_titles |= view.seen
        self.assertEqual(self.travel.state.session.get.call_count, 1)  # 캐시

    def test_not_approved(self):
        self.travel.state.session.get = MagicMock(return_value=_Resp(
            {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}}))
        i = fake_interaction()
        run(self.travel.send_food(i, "전주"))
        self.assertIn("활용신청", i.followup.send.await_args.args[0])

    def test_unknown_city(self):
        i = fake_interaction()
        run(self.travel.send_food(i, "뉴욕"))
        self.assertIn("자동완성", i.response.send_message.await_args.args[0])


class TimelineImageTest(unittest.TestCase):
    def test_render(self):
        from trips import Item, Trip
        from trip_images import _days, render_timeline
        t = Trip(1, 1, 1, "긴 여행", "2026-10-01", "2026-10-20", "부산", 1)
        t.items = [Item("2026-10-05", "10:00", "가" * 100, 1), Item("2026-10-05", "", "자유 시간", 1)]
        self.assertEqual(_days(t), [date(2026, 10, 5)])  # 10일 넘으면 일정 있는 날만
        png = render_timeline(t, date(2026, 10, 5)).read()
        self.assertTrue(png.startswith(b"\x89PNG"))
        empty = Trip(2, 1, 1, "당일", "2026-10-25", "2026-10-25", "", 1)
        self.assertTrue(render_timeline(empty, date(2026, 10, 12)).read().startswith(b"\x89PNG"))


class BusTestNoticeTest(unittest.TestCase):
    def test_notice(self):
        from embeds import TEST_NOTICE, bus_embed, bus_image_embed, is_test
        self.assertTrue(is_test(["테스트(고속)", "테스트(시외)"]))
        self.assertFalse(is_test(["고속버스 통합예매"]))
        e = bus_image_embed("서울", "강릉", "오늘", [], None, 0, test=True)
        self.assertIn(TEST_NOTICE, e.description)
        self.assertIn(TEST_NOTICE, bus_embed("서울", "강릉", "오늘", [], ["테스트(고속)"]).description)
        self.assertNotIn(TEST_NOTICE, bus_embed("서울", "강릉", "오늘", [], ["고속버스 통합예매"]).description)


class GuideTest(unittest.TestCase):
    def test_home_and_sections(self):
        import guide
        home = guide.help_embed(None)
        self.assertIn("도움말", home.title)
        self.assertEqual(len(home.fields), len(guide.SECTIONS))
        for name in guide.SECTIONS:
            e = guide.help_embed(name)
            self.assertIn(name, e.title)
            for field_name, value in e.fields:
                self.assertLessEqual(len(value), 1024)
                self.assertIn("예) `/", value)
        self.assertIn("도움말", guide.help_embed("없는 분류").title)

    def test_buttons_switch(self):
        import guide
        i = fake_interaction()
        run(guide.send_help(i, "지하철"))
        kw = i.response.send_message.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertIn("지하철", kw["embed"].title)
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels, ["처음", "이동", "지하철", "여행지", "같이 여행"])
        self.assertTrue(kw["view"].children[2].disabled)
        b = fake_interaction()
        run(kw["view"].children[0].callback(b))
        self.assertIn("도움말", b.response.edit_message.await_args.kwargs["embed"].title)


if __name__ == "__main__":
    unittest.main()
