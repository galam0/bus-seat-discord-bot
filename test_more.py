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
        kw = i.followup.send.await_args.kwargs
        self.assertIn("지금 보통", kw["embed"].title)
        self.assertIn("03시", kw["embed"].description)  # 가장 한산할 때
        self.assertEqual(kw["file"].filename, "crowd.png")
        # 글자 버전(이미지 실패 때)
        e = self.travel.crowd_embed(congestion.parse_crowd(CROWD))
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
        self.assertEqual(labels, ["다시 뽑기", "지도", "근처 맛집 목록", "카카오맵에서 근처 맛집 더 보기"])
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
        link = i.followup.send.await_args.kwargs["view"].children[0]
        self.assertIn("map.kakao.com", link.url)

    def test_nearby_not_approved_offers_map(self):
        self.travel.state.session.get = MagicMock(return_value=_Resp(
            {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}}))
        from urllib.parse import unquote
        i = fake_interaction()
        run(self.travel.send_nearby(i, "도시:강릉", "숙소"))
        kw = i.followup.send.await_args.kwargs
        self.assertIn("카카오맵", i.followup.send.await_args.args[0])
        self.assertEqual(unquote(kw["view"].children[0].url), "https://map.kakao.com/link/search/강릉 숙소")
        j = fake_interaction()
        run(self.travel.send_nearby(j, "장소:경포대", "맛집"))  # 이름 검색도 막히면 입력한 이름으로
        self.assertEqual(unquote(j.followup.send.await_args.kwargs["view"].children[0].url),
                         "https://map.kakao.com/link/search/경포대 맛집")

    def test_unknown_place(self):
        empty = {"response": {"header": {"resultCode": "0000"}, "body": {"items": ""}}}
        self.travel.state.session.get = MagicMock(return_value=_Resp(empty))
        i = fake_interaction()
        run(self.travel.send_food(i, "없는곳123"))
        self.assertIn("못 찾았어요", i.followup.send.await_args.args[0])

    def test_resolve_point(self):
        t = self.travel
        self.assertEqual(run(t.resolve_point("도시:전주")).kind, "도시")
        self.assertIsNone(run(t.resolve_point("도시:뉴욕")))
        self.assertEqual(run(t.resolve_point("전주")).label, "전주")
        # 이름 검색: 관광공사 searchKeyword2 첫 결과의 좌표
        pt = run(t.resolve_point("가 식당"))
        self.assertEqual((pt.kind, pt.label, pt.key), ("장소", "가 식당", "장소:가 식당"))
        self.assertAlmostEqual(pt.lat, 35.82)
        url = t.state.session.get.call_args.args[0]
        self.assertTrue(url.endswith("/searchKeyword2"))

    def test_nearby(self):
        i = fake_interaction()
        run(self.travel.send_nearby(i, "장소:경포대", "숙소"))
        kw = i.followup.send.await_args.kwargs
        self.assertIn("근처 숙소", kw["embed"].title)
        self.assertIn("](https://map.kakao.com/link/search/", kw["embed"].description)
        self.assertIn("1km 안", kw["embed"].footer)
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels, ["맛집", "숙소", "관광지", "문화시설", "레포츠", "카카오맵에서 근처 숙소 더 보기"])
        self.assertTrue(kw["view"].children[1].disabled)
        from urllib.parse import unquote
        self.assertEqual(unquote(kw["view"].children[5].url),
                         "https://map.kakao.com/link/search/가 식당 숙소")
        params = self.travel.state.session.get.call_args.kwargs["params"]
        self.assertEqual((params["contentTypeId"], params["radius"]), ("32", "1000"))
        # 다른 종류 버튼은 다시 장소를 찾지 않고 바로 바꿈
        calls = self.travel.state.session.get.call_count
        b = fake_interaction()
        run(kw["view"].children[0].callback(b))
        self.assertIn("근처 맛집", b.edit_original_response.await_args.kwargs["embed"].title)
        self.assertEqual(self.travel.state.session.get.call_count, calls + 1)

    def test_nearby_widens(self):
        empty = {"response": {"header": {"resultCode": "0000"}, "body": {"items": ""}}}
        self.travel.state.session.get = MagicMock(side_effect=[_Resp(empty), _Resp(FOOD)])
        i = fake_interaction()
        run(self.travel.send_nearby(i, "도시:전주", "맛집"))
        e = i.followup.send.await_args.kwargs["embed"]
        self.assertIn("5km까지 넓혀", e.description)
        self.assertIn("5km 안", e.footer)

    def test_station_point(self):
        from types import SimpleNamespace
        net = SimpleNamespace(by_name={"강남": ["a", "b"]},
                              nodes={"a": SimpleNamespace(lat=37.49, lon=127.02),
                                     "b": SimpleNamespace(lat=37.51, lon=127.04)})
        saved = self.travel.state.subway.net
        self.travel.state.subway.net = net
        try:
            pt = run(self.travel.resolve_point("역:강남"))
            self.assertEqual((pt.label, pt.kind), ("강남역", "역"))
            self.assertAlmostEqual(pt.lat, 37.50)
            self.assertEqual(run(self.travel.resolve_point("강남역")).key, "역:강남")
        finally:
            self.travel.state.subway.net = saved


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


MID = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [{
    "regId": "11D20000", "rnSt4Am": 20, "rnSt4Pm": 30, "rnSt5Am": 60, "rnSt5Pm": 70,
    "wf4Am": "맑음", "wf4Pm": "구름많음", "wf5Am": "흐리고 비", "wf5Pm": "흐림", "rnSt8": 40, "wf8": "흐림"}]}}}}


class WeekendTest(unittest.TestCase):
    def test_days(self):
        import weekend
        self.assertEqual(weekend.weekend_days(date(2026, 10, 12)), [date(2026, 10, 17), date(2026, 10, 18)])  # 월
        self.assertEqual(weekend.weekend_days(date(2026, 10, 17)), [date(2026, 10, 17), date(2026, 10, 18)])  # 토
        self.assertEqual(weekend.weekend_days(date(2026, 10, 18)), [date(2026, 10, 18)])  # 일

    def test_zone_and_tmfc(self):
        import weekend
        from datetime import datetime
        self.assertEqual(weekend.mid_zone("강릉", "강원"), "11D20000")
        self.assertEqual(weekend.mid_zone("춘천", "강원"), "11D10000")
        self.assertEqual(weekend.mid_zone("여수", "전남"), "11F20000")
        self.assertEqual(weekend.mid_tmfc(datetime(2026, 10, 12, 21)), "202610121800")
        self.assertEqual(weekend.mid_tmfc(datetime(2026, 10, 12, 9)), "202610120600")
        self.assertEqual(weekend.mid_tmfc(datetime(2026, 10, 12, 3)), "202610111800")

    def test_parse_mid(self):
        import weekend
        days = weekend.parse_mid_land(MID, "202610130600")
        self.assertEqual((days[date(2026, 10, 17)].text, days[date(2026, 10, 17)].pop), ("구름많음", 30))
        self.assertEqual((days[date(2026, 10, 18)].text, days[date(2026, 10, 18)].pop), ("비", 70))
        self.assertEqual(days[date(2026, 10, 21)].text, "흐림")
        with self.assertRaises(RuntimeError):
            weekend.parse_mid_land({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은"}}}, "x")

    def test_from_short_and_best(self):
        import weekend
        from datetime import datetime
        from weather import Forecast, Slot
        sat = date(2026, 10, 17)
        slots = [Slot(datetime(2026, 10, 17, h), 10.0 + h / 2, "", "맑음" if h < 15 else "비", 60 if h >= 15 else 0)
                 for h in range(0, 24)]
        fc = Forecast(slots, {"20261017": (8.0, 19.0)})
        d = weekend.from_short(fc, sat)
        self.assertEqual((d.text, d.pop, d.lo, d.hi, d.source), ("비", 60, 8.0, 19.0, "단기"))
        self.assertIsNone(weekend.from_short(fc, date(2026, 10, 18)))
        good, bad = weekend.Day("맑음", 0, None, None, "중기"), weekend.Day("비", 80, None, None, "중기")
        self.assertEqual(weekend.pick_best([("A", [bad, bad]), ("B", [good, bad]), ("C", [good, None])]), "B")

    def test_command_uses_mid_when_short_misses(self):
        import travel
        from datetime import datetime
        from weather import Forecast
        saved = (travel.state.kma_key, travel.state.session)
        travel.state.kma_key = "k"
        travel.state.session = MagicMock()
        travel.state.session.get = MagicMock(return_value=_Resp(MID))
        travel._short_cache.clear()
        travel._mid_cache.clear()
        try:
            with patch.object(travel, "fetch_forecast", AsyncMock(return_value=Forecast([], {}))), \
                    patch.object(travel, "datetime") as dt:
                dt.now.return_value = datetime(2026, 10, 13, 10, 0)  # 화요일 -> 주말은 중기
                dt.strptime = datetime.strptime
                i = fake_interaction()
                run(travel.send_weekend(i, ["강릉", "부산"]))
            kw = i.followup.send.await_args.kwargs
            self.assertEqual(kw["file"].filename, "weekend.png")
            self.assertEqual([b.label for b in kw["view"].children], ["강릉", "부산"])
            params = travel.state.session.get.call_args.kwargs["params"]
            self.assertEqual(params["tmFc"], "202610130600")
        finally:
            travel.state.kma_key, travel.state.session = saved

    def test_command_mid_not_approved(self):
        import travel
        from datetime import datetime
        from weather import Forecast
        saved = (travel.state.kma_key, travel.state.session)
        travel.state.kma_key = "k"
        travel.state.session = MagicMock()
        travel.state.session.get = MagicMock(return_value=_Resp(
            {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"returnAuthMsg": "등록되지 않은 서비스키"}}}))
        travel._short_cache.clear()
        travel._mid_cache.clear()
        try:
            with patch.object(travel, "fetch_forecast", AsyncMock(return_value=Forecast([], {}))), \
                    patch.object(travel, "datetime") as dt:
                dt.now.return_value = datetime(2026, 10, 13, 10, 0)
                i = fake_interaction()
                run(travel.send_weekend(i, ["강릉"]))
            self.assertIn("중기예보", i.followup.send.await_args.args[0])
            bad = fake_interaction()
            run(travel.send_weekend(bad, ["뉴욕"]))
            self.assertIn("모르는 도시", bad.response.send_message.await_args.args[0])
        finally:
            travel.state.kma_key, travel.state.session = saved


class CardRenderTest(unittest.TestCase):
    def test_all_cards_render(self):
        import cards
        from models import TrainTrip
        from weekend import Day
        trips = [TrainTrip("KTX 101", "05:13", "07:51", grade="KTX", fare=59800),
                 TrainTrip("KTX 103", "06:00", "08:40", "예약가능", "매진")]
        pngs = [
            cards.render_trains("서울 → 부산", "오늘", trips, "출처"),
            cards.render_trains("서울 → 부산", "오늘", [], "출처"),
            cards.render_board("강남역", [("2호선", (0, 160, 70), "교대 방면 · 내선", [("성수행", "3분 후", "지금 역삼")]),
                                       ("신분당선", (210, 0, 60), "양재 방면", [])], "21:00 기준"),
            cards.render_last_train("강남", "평일", [("2호선", (0, 160, 70), "내선순환", ("05:30", "성수"),
                                                      [("00:21", "홍대입구"), ("00:46", "서울대입구")]),
                                                     ("2호선", (0, 160, 70), "외선순환", None, [])]),
            cards.render_weekend("주말", ["토", "일"], [("강릉", [Day("맑음", 0, 1.0, 9.0, "단기"), None])], "강릉"),
            cards.render_crowd(congestion.parse_crowd(CROWD), congestion.LEVELS, congestion.ORDER,
                               congestion.fmt_people),
        ]
        for p in pngs:
            self.assertTrue(p.read().startswith(b"\x89PNG"))


def _rt(*rows):
    keys = ("subwayId", "updnLine", "trainLineNm", "barvlDt", "arvlMsg2", "arvlMsg3", "arvlCd", "btrainNo")
    return {"errorMessage": {"code": "INFO-000"}, "realtimeArrivalList": [dict(zip(keys, r)) for r in rows]}


class ArrivalAlertTest(unittest.TestCase):
    def setUp(self):
        import travel
        self.t = travel
        self.saved = (list(travel.state.realtime_keys), travel.state.client)
        travel.state.realtime_keys = ["k"]
        travel._rt_cache.clear()
        travel._watches.clear()

    def tearDown(self):
        for w in list(self.t._watches.values()):
            if w.task:
                w.task.cancel()
        self.t._watches.clear()
        self.t.state.realtime_keys, self.t.state.client = self.saved

    def test_eta(self):
        from subway import parse_arrivals
        a = parse_arrivals(_rt(
            ("1002", "내선", "성수행 - 교대방면", "190", "3분 후", "역삼", "99", "2001"),
            ("1002", "내선", "성수행 - 교대방면", "0", "전역 도착", "역삼", "5", "2003"),
            ("1002", "외선", "성수행 - 역삼방면", "0", "강남 출발", "강남", "2", "2005"),
            ("1002", "외선", "성수행 - 역삼방면", "0", "[3]번째 전역 (서초)", "서초", "99", "2007"),
            ("1002", "외선", "성수행 - 역삼방면", "0", "강남 진입", "강남", "0", "2009"),
            ("1002", "외선", "성수행 - 역삼방면", "0", "운행 중", "", "99", "2011"),
        ))
        etas = {x.train_no: x.eta() for x in a}
        self.assertEqual(etas, {"2001": 190, "2003": 120, "2005": -1, "2007": 360, "2009": 0, "2011": None})

    def test_setup_pick_and_fire(self):
        t = self.t
        first = _rt(("1002", "내선", "성수행 - 교대방면", "100", "2분 후", "역삼", "99", "A1"),
                    ("1002", "내선", "성수행 - 교대방면", "600", "10분 후", "선릉", "99", "A2"),
                    ("1002", "외선", "성수행 - 역삼방면", "300", "5분 후", "서초", "99", "B1"))
        later = _rt(("1002", "내선", "성수행 - 교대방면", "150", "2분 후", "역삼", "99", "A2"))
        responses = [first, first, later]
        fake = AsyncMock(side_effect=lambda st: responses.pop(0) if responses else later)
        user = MagicMock()
        user.send = AsyncMock()
        client = MagicMock()
        client.get_user = MagicMock(return_value=user)
        t.state.client = client

        async def go():
            with patch.object(t, "fetch_realtime", fake), patch.object(t, "ALERT_POLL", 0.01):
                i = fake_interaction()
                await t.send_alert_setup(i, "강남", 3)
                view = i.followup.send.await_args.kwargs["view"]
                self.assertEqual([o.label for o in view.sel.options],
                                 ["2호선 · 교대 방면 (내선)", "2호선 · 역삼 방면 (외선)"])
                p = fake_interaction(user_id=42)
                p.channel_id = 9
                view.sel.values = ["2호선|내선"]
                await view.picked(p)
                msg = p.response.edit_message.await_args.kwargs["content"]
                self.assertIn("3분 안", msg)
                self.assertIn("그 다음 열차", msg)  # A1 은 이미 2분 거리라 건너뜀
                w = t._watches[42]
                self.assertEqual(w.skip, {"A1"})
                await asyncio.wait_for(w.task, 2)
            self.assertNotIn(42, t._watches)
            text = user.send.await_args.args[0]
            self.assertIn("강남역", text)
            self.assertIn("성수행", text)
            self.assertIn("약 2분 뒤", text)

        run(go())

    def test_dm_blocked_goes_to_channel(self):
        t = self.t
        user = MagicMock()
        user.send = AsyncMock(side_effect=Exception("Cannot send messages to this user"))
        ch = MagicMock()
        ch.send = AsyncMock()
        client = MagicMock()
        client.get_user = MagicMock(return_value=user)
        client.get_channel = MagicMock(return_value=ch)
        t.state.client = client
        from datetime import datetime
        w = t.Watch(7, 5, "강남", "2호선|내선", "2호선", 3, datetime.now(t.KST), set())
        run(t._notify(w, "곧 와요"))
        self.assertEqual(ch.send.await_args.args[0], "<@7> 곧 와요")

    def test_cancel(self):
        t = self.t
        from datetime import datetime

        async def go():
            w = t.Watch(5, None, "강남", "k", "2호선", 3, datetime.now(t.KST), set())
            w.task = asyncio.create_task(asyncio.sleep(10))
            t._watches[5] = w
            view = t.AlertCancelView(5)
            i = fake_interaction()
            await view.cancel(i, None)
            self.assertIn("껐어요", i.response.edit_message.await_args.kwargs["content"])
            self.assertNotIn(5, t._watches)
            await asyncio.sleep(0)
            self.assertTrue(w.task.cancelled())

        run(go())

    def test_no_arrivals(self):
        with patch.object(self.t, "fetch_realtime", AsyncMock(return_value=_rt())):
            i = fake_interaction()
            run(self.t.send_alert_setup(i, "소요산"))
        self.assertIn("도착 정보가 없어요", i.followup.send.await_args.args[0])


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

    def test_images(self):
        import guide
        for key in [None, *guide.SECTIONS]:
            self.assertTrue(guide.help_png(key).startswith(b"\x89PNG"))
        # 처음 화면 짧은 설명이 모든 명령어에 있음
        for _, _, cmds in guide.SECTIONS.values():
            for c, _, _ in cmds:
                key = c if c.startswith("/여행") else c.split(" ")[0]
                self.assertIn(key, guide.SHORT)
        import cards
        self.assertEqual(cards.clean("여행 카드의 🗓️ 버튼 `/여행`"), "여행 카드의 버튼 /여행")

    def test_text_fallback(self):
        import guide
        with patch.object(guide, "help_png", side_effect=OSError):
            embed, files = guide.help_message("이동")
        self.assertEqual(files, [])
        self.assertIn("이동", embed.title)

    def test_buttons_switch(self):
        import guide
        i = fake_interaction()
        run(guide.send_help(i, "지하철"))
        kw = i.response.send_message.await_args.kwargs
        self.assertTrue(kw["ephemeral"])
        self.assertIn("지하철", kw["embed"].title)
        self.assertEqual(kw["files"][0].filename, "help.png")
        labels = [b.label for b in kw["view"].children]
        self.assertEqual(labels, ["처음", "이동", "지하철", "여행지", "같이 여행"])
        self.assertTrue(kw["view"].children[2].disabled)
        b = fake_interaction()
        run(kw["view"].children[0].callback(b))
        ekw = b.response.edit_message.await_args.kwargs
        self.assertIn("도움말", ekw["embed"].title)
        self.assertEqual(ekw["attachments"][0].filename, "help.png")


if __name__ == "__main__":
    unittest.main()
