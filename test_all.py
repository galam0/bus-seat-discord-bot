import asyncio
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

try:
    import discord  # noqa: F401

    HAS_DISCORD = True
except ModuleNotFoundError:
    HAS_DISCORD = False

if HAS_DISCORD:
    import bot
    from bot import Flow, KST, drop_departed
    from embeds import PAGE_SIZE, bus_embed, midpoint_embed, page_count, page_range, train_embed
    from views import DateView, ResultView, date_label

from midpoint import find_midpoints, fmt_minutes
from models import BusSearch, BusTrip
from providers import (CombinedBusProvider, MockExpressProvider, MockIntercityProvider,
                       MockTrainProvider, ProviderUnavailable, get_bus_provider)
from seatmap import render_seat_map


def MockBusProvider():
    return get_bus_provider("mock")


def KobusBusProvider():
    return get_bus_provider("real")


class _Boom:
    kind, label = "시외", "고장난 곳"

    async def terminals(self):
        raise RuntimeError("network")

    async def search(self, dep, arr, day):
        raise RuntimeError("network")

needs_discord = unittest.skipUnless(HAS_DISCORD, "discord.py 미설치")

TODAY = date(2026, 10, 12)  # 월요일


def run(coro):
    return asyncio.run(coro)


def fake_interaction(user_id=1):
    i = MagicMock()
    i.user.id = user_id
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.edit_original_response = AsyncMock()
    i.followup.send = AsyncMock()
    return i


class MidpointTest(unittest.TestCase):
    def test_seoul_busan_is_in_between(self):
        top = find_midpoints(["서울", "부산"])
        self.assertEqual(len(top), 3)
        self.assertTrue({c.city for c in top} & {"상주", "구미", "김천", "대구"})
        self.assertNotIn(top[0].city, ("서울", "부산"))

    def test_scores_sorted_and_legs(self):
        top = find_midpoints(["서울", "광주", "부산"])
        self.assertEqual([c.score for c in top], sorted(c.score for c in top))
        self.assertEqual(len(top[0].legs), 3)

    def test_errors(self):
        with self.assertRaises(KeyError):
            find_midpoints(["서울", "뉴욕"])
        with self.assertRaises(ValueError):
            find_midpoints(["서울", "서울"])

    def test_fmt(self):
        self.assertEqual(fmt_minutes(45), "45분")
        self.assertEqual(fmt_minutes(120), "2시간")
        self.assertEqual(fmt_minutes(125), "2시간 5분")


class SeatMapTest(unittest.TestCase):
    def test_rows_cover_every_seat_once(self):
        from seatmap import seat_rows
        for total in (28, 45, 41, 21, 31):
            nums = [n for row in seat_rows(total) for _, n in row]
            self.assertEqual(nums, list(range(1, total + 1)), total)

    def test_real_bus_shapes(self):
        from seatmap import AISLE, seat_rows
        r28 = seat_rows(28)  # 우등: 2+1 x 8줄 + 마지막 4석
        self.assertEqual(len(r28), 9)
        self.assertEqual([c for c, _ in r28[0]], [0, 1, 3])
        self.assertEqual(len(r28[-1]), 4)
        self.assertIn(AISLE, [c for c, _ in r28[-1]])
        r45 = seat_rows(45)  # 일반: 2+2 x 10줄 + 마지막 5석
        self.assertEqual(len(r45), 11)
        self.assertEqual(len(r45[-1]), 5)

    def test_text_fallback(self):
        s = render_seat_map(28, {1, 3})
        self.assertIn("01", s)
        self.assertIn("03", s)
        self.assertNotIn("02", s.split("\n")[1])  # 02는 찬 자리
        self.assertEqual(render_seat_map(45, set(range(1, 46))).split("\n")[1], "01 02 | 03 04")


class ImageTest(unittest.TestCase):
    def setUp(self):
        try:
            import images  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("Pillow 미설치")

    def test_schedule_and_seats_png(self):
        import images
        from PIL import Image
        trips = run(MockBusProvider().search("부산", "서울", TODAY)).trips
        for page in (trips[:10], trips[:1], []):
            png = images.render_schedule("부산 → 서울", "오늘", page, "출처", "※ 일부 실패")
            img = Image.open(png)
            self.assertEqual(img.format, "PNG")
            self.assertGreater(img.width, img.height * 0.9 if len(page) == 10 else 0)
        for total in (28, 45):
            t = next(t for t in trips if t.seats_total == total)
            img = Image.open(images.render_seats("제목", "부제", t))
            self.assertGreater(img.width, img.height)  # 가로로 눕힌 버스

    def test_fonts_have_hangul_and_symbols(self):
        import images
        from fontTools.ttLib import TTFont
        cmap = TTFont(images.FONT_DIR / "NotoSansKR-Bold-subset.otf").getBestCmap()
        for ch in "진주고속서울경부→·※석원쪽":
            self.assertIn(ord(ch), cmap, ch)

    def test_duration(self):
        import images
        self.assertEqual(images.duration_text("23:30", "01:10"), "1시간 40분")
        self.assertEqual(images.duration_text("06:00", "06:45"), "45분")


class ProviderTest(unittest.TestCase):
    def test_bus_mock_deterministic_and_valid(self):
        p = MockBusProvider()
        a = run(p.search("서울", "부산", TODAY))
        b = run(p.search("서울", "부산", TODAY))
        self.assertIsInstance(a, BusSearch)
        self.assertEqual(a, b)
        self.assertEqual([t.depart for t in a.trips], sorted(t.depart for t in a.trips))
        for t in a.trips:
            self.assertTrue(set(t.free_seats) <= set(range(1, t.seats_total + 1)))
            self.assertEqual(t.sold_out, t.seats_left == 0)

    def test_mock_has_both_kinds_on_long_route(self):
        r = run(MockBusProvider().search("서울", "강릉", TODAY))
        self.assertEqual({t.kind for t in r.trips}, {"고속", "시외"})
        self.assertEqual(r.sources, ["테스트(고속)", "테스트(시외)"])
        self.assertEqual(r.missing, [])

    def test_short_route_intercity_only(self):
        r = run(MockBusProvider().search("대전", "세종", TODAY))  # 약 20km
        self.assertTrue(r.trips)
        self.assertEqual({t.kind for t in r.trips}, {"시외"})

    def test_partial_failure_keeps_rest(self):
        p = CombinedBusProvider([MockExpressProvider(), _Boom()])
        r = run(p.search("서울", "부산", TODAY))
        self.assertTrue(r.trips)
        self.assertEqual(r.missing, ["고장난 곳"])
        self.assertIn("서울", run(p.terminals()))

    def test_all_fail_raises(self):
        with self.assertRaises(ProviderUnavailable):
            run(CombinedBusProvider([_Boom()]).search("서울", "부산", TODAY))
        with self.assertRaises(ProviderUnavailable):
            run(KobusBusProvider().search("서울", "부산", TODAY))
        with self.assertRaises(ProviderUnavailable):
            run(KobusBusProvider().terminals())

    def test_provider_spec(self):
        self.assertEqual(KobusBusProvider().label, "고속버스 통합예매 + 버스타고 + 티머니 시외버스")
        self.assertEqual(len(get_bus_provider("kobus,bustago,kobus").providers), 2)
        with self.assertRaises(ValueError):
            get_bus_provider("이상한값")

    def test_unknown_city_empty(self):
        self.assertEqual(run(MockBusProvider().search("서울", "뉴욕", TODAY)).trips, [])

    def test_train_mock(self):
        trips = run(MockTrainProvider().search("서울", "부산", TODAY))
        self.assertTrue(trips and trips[0].name.startswith("KTX"))


@needs_discord
class ViewTest(unittest.TestCase):
    def test_date_labels(self):
        self.assertEqual(date_label(TODAY, TODAY), "오늘 10/12(월)")
        self.assertEqual(date_label(date(2026, 10, 13), TODAY), "내일 10/13(화)")
        self.assertEqual(date_label(date(2026, 10, 14), TODAY), "모레 10/14(수)")
        self.assertEqual(date_label(date(2026, 10, 15), TODAY), "10/15(목)")

    def test_date_view_buttons_and_owner_check(self):
        async def go():
            picked = []

            async def on_pick(interaction, d):
                picked.append(d)

            v = DateView(1, on_pick, TODAY)
            self.assertEqual(len(v.children), 15)  # 오늘 ~ 2주 뒤
            self.assertEqual([c.row for c in v.children], [i // 5 for i in range(15)])
            self.assertIn("10/26", v.children[14].label)
            await v.children[2].callback(fake_interaction())
            self.assertEqual(picked, [date(2026, 10, 14)])
            stranger = fake_interaction(user_id=2)
            self.assertFalse(await v.interaction_check(stranger))
            stranger.response.send_message.assert_awaited()
            self.assertTrue(await v.interaction_check(fake_interaction(1)))

        run(go())

    def test_result_view_first_page(self):
        trips = run(MockBusProvider().search("서울", "강릉", TODAY)).trips
        self.assertGreater(len(trips), PAGE_SIZE * 2)

        async def go():
            v = ResultView(1, AsyncMock(), trips=trips, page=0, on_page=AsyncMock(), on_seat=AsyncMock())
            select, prev, nxt, back = v.children
            self.assertEqual([o.value for o in select.options], [str(i) for i in range(PAGE_SIZE)])
            self.assertIn("→", select.options[0].label)  # 터미널 경로
            self.assertIn("[", select.options[0].description)
            self.assertTrue(prev.disabled)
            self.assertFalse(nxt.disabled)
            self.assertEqual(back.label, "다른 날짜")

        run(go())

    def test_result_view_last_page_and_clamp(self):
        trips = run(MockBusProvider().search("서울", "강릉", TODAY)).trips
        last = page_count(len(trips)) - 1

        async def go():
            v = ResultView(1, AsyncMock(), trips=trips, page=99, on_page=AsyncMock(), on_seat=AsyncMock())
            self.assertEqual(v.page, last)
            select, prev, nxt, _ = v.children
            self.assertEqual(select.options[-1].value, str(len(trips) - 1))
            self.assertFalse(prev.disabled)
            self.assertTrue(nxt.disabled)

        run(go())

    def test_single_page_has_no_paging_buttons(self):
        trips = [BusTrip("07:00", "09:00", "우등", 20000, 28, (1,))] * 3

        async def go():
            v = ResultView(1, AsyncMock(), trips=trips, on_page=AsyncMock(), on_seat=AsyncMock())
            self.assertEqual(len(v.children), 2)  # 메뉴 + 다른 날짜

        run(go())

    def test_empty_result_only_back(self):
        async def go():
            v = ResultView(1, AsyncMock(), trips=[], on_page=AsyncMock(), on_seat=AsyncMock())
            self.assertEqual(len(v.children), 1)

        run(go())


@needs_discord
class EmbedTest(unittest.TestCase):
    def test_pages_show_all_trips(self):
        trips = [BusTrip(f"{h:02d}:00", "23:59", "우등", 20000, 28, ()) for h in range(6, 6 + PAGE_SIZE + 3)]
        e0 = bus_embed("서울", "대전", "오늘", trips, ["테스트"], page=0)
        e1 = bus_embed("서울", "대전", "오늘", trips, ["테스트"], page=1)
        self.assertIn("1/2쪽", e0.description)
        self.assertIn("2/2쪽", e1.description)
        self.assertIn("매진", e0.description)
        shown = [l for e in (e0, e1) for l in e.description.split("\n") if l.startswith("`")]
        self.assertEqual(len(shown), len(trips))
        self.assertLess(len(e0.description), 4096)

    def test_page_range(self):
        self.assertEqual(page_range(0, 0), (0, 0))
        self.assertEqual(page_count(0), 1)
        self.assertEqual(page_range(PAGE_SIZE + 1, 1), (PAGE_SIZE, PAGE_SIZE + 1))
        self.assertEqual(page_range(PAGE_SIZE + 1, -5), (0, PAGE_SIZE))

    def test_train_embed_pages(self):
        trips = run(MockTrainProvider().search("서울", "부산", TODAY))
        e = train_embed("서울", "부산", "오늘", trips, "테스트", page=1)
        self.assertIn(f"2/{page_count(len(trips))}쪽", e.description)

    def test_kind_tags_and_missing(self):
        trips = [BusTrip("07:00", "09:00", "우등", 20000, 28, (1,), "고속"),
                 BusTrip("07:10", "09:30", "일반", 15000, 45, (1, 2), "시외")]
        e = bus_embed("서울", "강릉", "오늘", trips, ["고속버스 통합예매"], ["버스타고"])
        self.assertIn("[고속]", e.description)
        self.assertIn("[시외]", e.description)
        self.assertIn("고속 1편 · 시외 1편", e.description)
        self.assertIn("버스타고 조회 실패", e.description)

    def test_midpoint_embed(self):
        e = midpoint_embed(["서울", "부산"], find_midpoints(["서울", "부산"]))
        self.assertEqual(len(e.fields), 3)


@needs_discord
class DropDepartedTest(unittest.TestCase):
    def test_today_filter_only(self):
        trips = [BusTrip(h, "23:59", "일반", 1000, 45, (1,)) for h in ("08:00", "12:00", "18:00")]
        now = datetime(2026, 10, 12, 12, 0, tzinfo=KST)
        self.assertEqual([t.depart for t in drop_departed(trips, TODAY, now)], ["12:00", "18:00"])
        self.assertEqual(len(drop_departed(trips, date(2026, 10, 13), now)), 3)


@needs_discord
class FlowTest(unittest.TestCase):
    def test_pick_then_seat(self):
        async def go():
            flow = Flow("bus", MockBusProvider(), "서울", "대전", 1)
            i = fake_interaction()
            await flow.pick(i, date(2026, 10, 20))
            i.response.edit_message.assert_awaited_once()
            kwargs = i.edit_original_response.await_args.kwargs
            self.assertIn("대전", kwargs["embed"].title)
            self.assertIsInstance(kwargs["view"], ResultView)

            idx = next(n for n, t in enumerate(flow.trips) if not t.sold_out)
            s = fake_interaction()
            await flow.seat(s, idx)
            s.response.defer.assert_awaited_once()
            kw = s.followup.send.await_args.kwargs
            self.assertTrue(kw["ephemeral"])
            self.assertEqual(kw["file"].filename, "seats.png")
            self.assertIn(flow.trips[idx].depart, kw["embed"].title)

        run(go())

    def test_provider_unavailable_shows_warning_and_dates(self):
        async def go():
            flow = Flow("bus", KobusBusProvider(), "서울", "부산", 1)
            i = fake_interaction()
            await flow.pick(i, date(2026, 10, 20))
            kwargs = i.edit_original_response.await_args.kwargs
            self.assertIn("⚠️", kwargs["content"])
            self.assertIsInstance(kwargs["view"], DateView)

        run(go())

    def test_turn_page_then_seat_on_second_page(self):
        async def go():
            flow = Flow("bus", MockBusProvider(), "서울", "강릉", 1)
            await flow.pick(fake_interaction(), date(2026, 10, 20))
            t = fake_interaction()
            await flow.turn(t, 1)
            t.response.defer.assert_awaited_once()
            kwargs = t.edit_original_response.await_args.kwargs
            self.assertEqual(kwargs["attachments"][0].filename, "buses.png")
            self.assertIn("2/", kwargs["embed"].description)
            select = kwargs["view"].children[0]
            idx = int(select.options[0].value)
            self.assertEqual(idx, PAGE_SIZE)
            s = fake_interaction()
            await flow.seat(s, idx)
            self.assertIn(flow.trips[idx].depart, s.followup.send.await_args.kwargs["embed"].title)

        run(go())

    def test_train_flow_has_no_select(self):
        async def go():
            flow = Flow("train", MockTrainProvider(), "서울", "부산", 1)
            i = fake_interaction()
            await flow.pick(i, date(2026, 10, 20))
            view = i.edit_original_response.await_args.kwargs["view"]
            labels = [c.label for c in view.children]
            self.assertEqual(labels, ["이전", "다음", "다른 날짜"])

        run(go())


@needs_discord
class CommandRegistrationTest(unittest.TestCase):
    def test_commands_registered(self):
        names = {c.name for c in bot.tree.get_commands()}
        self.assertEqual(names, {"버스", "기차", "중간도시", "지하철", "노선도", "도착", "날씨", "관광", "막차", "여행", "혼잡", "어디가지", "도움말"})

    def test_help_covers_every_command(self):
        import guide
        listed = " ".join(c for _, _, cmds in guide.SECTIONS.values() for c, _, _ in cmds)
        for c in bot.tree.get_commands():
            if c.name != "도움말":
                self.assertIn(f"/{c.name}", listed)
        import trips_ui
        for sub in trips_ui.group.commands:
            self.assertIn(sub.name, listed)


class TerminalTest(unittest.TestCase):
    def test_resolve(self):
        from cities import place_label, resolve_place, terminals_of
        self.assertEqual(resolve_place("서울"), ("서울", None))
        self.assertEqual(resolve_place("동서울"), ("서울", "동서울"))
        self.assertEqual(resolve_place("강릉터미널"), ("강릉", "강릉터미널"))
        with self.assertRaises(KeyError):
            resolve_place("뉴욕")
        self.assertEqual(place_label("동서울"), "동서울 · 서울")
        self.assertEqual(place_label("서울"), "서울 (터미널 전체)")
        self.assertEqual(terminals_of("진주", "고속"), ["진주고속"])
        for city in ("서울", "부산", "진주", "강릉"):  # 모든 도시에 고속·시외 터미널이 하나씩은 있어야 함
            self.assertTrue(terminals_of(city, "고속") and terminals_of(city, "시외"))

    def test_mock_trips_have_matching_terminals(self):
        from cities import TERMINALS, terminals_of
        r = run(MockBusProvider().search("진주", "서울", TODAY))
        for t in r.trips:
            self.assertIn(t.dep_terminal, terminals_of("진주", t.kind))
            self.assertIn(t.arr_terminal, terminals_of("서울", t.kind))
            self.assertIn("→", t.route)
        # 호남 고속은 센트럴시티, 강원 고속은 동서울
        r = run(MockBusProvider().search("서울", "광주", TODAY))
        self.assertEqual({t.dep_terminal for t in r.trips if t.kind == "고속"}, {"센트럴시티"})
        r = run(MockBusProvider().search("강릉", "서울", TODAY))
        self.assertEqual({t.arr_terminal for t in r.trips if t.kind == "고속"}, {"동서울"})

    def test_autocomplete_lists_terminals(self):
        names = run(MockBusProvider().terminals())
        self.assertIn("서울", names)
        self.assertIn("동서울", names)
        self.assertIn("진주고속", names)


@needs_discord
class TerminalFlowTest(unittest.TestCase):
    def test_embed_shows_route(self):
        trips = [BusTrip("06:00", "10:44", "우등", 35400, 28, (1,), "고속", "진주고속", "서울경부")]
        e = bus_embed("진주", "서울", "오늘", trips, ["테스트"])
        self.assertIn("**진주고속 → 서울경부**", e.description)

    def test_terminal_filter(self):
        async def go():
            flow = Flow("bus", MockBusProvider(), "동서울", "강릉", 1)
            self.assertEqual((flow.q_dep, flow.dep_term), ("서울", "동서울"))
            i = fake_interaction()
            await flow.pick(i, date(2026, 10, 20))
            self.assertTrue(flow.trips)
            self.assertEqual({t.dep_terminal for t in flow.trips}, {"동서울"})
            self.assertIn("동서울 → 강릉", i.edit_original_response.await_args.kwargs["embed"].title)

            all_flow = Flow("bus", MockBusProvider(), "서울", "강릉", 1)
            await all_flow.pick(fake_interaction(), date(2026, 10, 20))
            self.assertGreater(len(all_flow.trips), len(flow.trips))  # 상봉 출발 시외도 포함

        run(go())

    def test_seat_header_has_route(self):
        async def go():
            flow = Flow("bus", MockBusProvider(), "진주", "서울", 1)
            await flow.pick(fake_interaction(), date(2026, 10, 20))
            idx = next(n for n, t in enumerate(flow.trips) if not t.sold_out)
            s = fake_interaction()
            await flow.seat(s, idx)
            self.assertIn(flow.trips[idx].route, s.followup.send.await_args.kwargs["embed"].title)

        run(go())

    def test_text_fallback_without_pillow(self):
        async def go():
            saved = bot.images
            bot.images = None
            try:
                flow = Flow("bus", MockBusProvider(), "진주", "서울", 1)
                i = fake_interaction()
                await flow.pick(i, date(2026, 10, 20))
                kw = i.edit_original_response.await_args.kwargs
                self.assertEqual(kw["attachments"], [])
                self.assertIn("진주", kw["embed"].description)  # 글자 목록
                idx = next(n for n, t in enumerate(flow.trips) if not t.sold_out)
                s = fake_interaction()
                await flow.seat(s, idx)
                self.assertIn("```", s.followup.send.await_args.args[0])
            finally:
                bot.images = saved

        run(go())

    def test_back_clears_image(self):
        async def go():
            flow = Flow("bus", MockBusProvider(), "진주", "서울", 1)
            i = fake_interaction()
            await flow.back(i)
            self.assertEqual(i.response.edit_message.await_args.kwargs["attachments"], [])

        run(go())

    def test_same_city(self):
        self.assertTrue(bot.same_city("동서울", "서울경부"))
        self.assertFalse(bot.same_city("동서울", "강릉"))


if __name__ == "__main__":
    unittest.main()
