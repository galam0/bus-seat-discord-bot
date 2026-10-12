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
    from embeds import MAX_ROWS, bus_embed, midpoint_embed
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
    i.edit_original_response = AsyncMock()
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
    def test_premium_layout(self):
        s = render_seat_map(28, {1, 3})
        self.assertIn("01", s)
        self.assertIn("03", s)
        self.assertNotIn("02", s.split("\n")[1])  # 02는 찬 자리
        # 28석 = 3석짜리 줄 9개(통로 표시 있음) + 마지막 1석 줄
        self.assertEqual(sum(1 for l in s.split("\n") if "|" in l), 9)

    def test_standard_layout_rows_of_four(self):
        s = render_seat_map(45, set(range(1, 46)))
        self.assertIn("01 02  |  03 04", s)


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
            self.assertEqual(len(v.children), 7)
            await v.children[2].callback(fake_interaction())
            self.assertEqual(picked, [date(2026, 10, 14)])
            stranger = fake_interaction(user_id=2)
            self.assertFalse(await v.interaction_check(stranger))
            stranger.response.send_message.assert_awaited()
            self.assertTrue(await v.interaction_check(fake_interaction(1)))

        run(go())

    def test_result_view_has_select_and_back(self):
        trips = run(MockBusProvider().search("서울", "대전", TODAY)).trips

        async def go():
            v = ResultView(1, AsyncMock(), trips, AsyncMock())
            self.assertEqual(len(v.children), 2)
            self.assertEqual(len(v.children[0].options), min(25, len(trips)))
            self.assertIn("[", v.children[0].options[0].label)

        run(go())


@needs_discord
class EmbedTest(unittest.TestCase):
    def test_sold_out_and_cap(self):
        trips = [BusTrip("07:00", "09:00", "우등", 20000, 28, ())] * (MAX_ROWS + 3)
        e = bus_embed("서울", "대전", "오늘", trips, ["테스트"])
        self.assertIn("매진", e.description)
        self.assertIn("외 3편", e.description)
        self.assertLess(len(e.description), 4096)

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
            text = s.response.send_message.await_args.args[0]
            self.assertIn("```", text)
            self.assertTrue(s.response.send_message.await_args.kwargs["ephemeral"])

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

    def test_train_flow_has_no_select(self):
        async def go():
            flow = Flow("train", MockTrainProvider(), "서울", "부산", 1)
            i = fake_interaction()
            await flow.pick(i, date(2026, 10, 20))
            view = i.edit_original_response.await_args.kwargs["view"]
            self.assertEqual(len(view.children), 1)

        run(go())


@needs_discord
class CommandRegistrationTest(unittest.TestCase):
    def test_commands_registered(self):
        names = {c.name for c in bot.tree.get_commands()}
        self.assertEqual(names, {"버스", "기차", "중간도시"})


if __name__ == "__main__":
    unittest.main()
