import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from subway import build_network, find_route, line_color, line_name, parse_arrivals, parse_fr
from tests_subway_fixture import line_rows, master_rows
from weather import Forecast, base_datetime, latlon_to_grid, parse_forecast, pick_slots

try:
    import discord  # noqa: F401

    HAS_DISCORD = True
except ModuleNotFoundError:
    HAS_DISCORD = False
needs_discord = unittest.skipUnless(HAS_DISCORD, "discord.py 미설치")


def run(coro):
    return asyncio.run(coro)


def net():
    return build_network(master_rows(), line_rows())


def edges(n, line):
    return {frozenset((n.nodes[a].name, n.nodes[b].name)) for a, b in n.lines[line]}


# 실제 API 응답(서울역, 샘플 키)에서 가져온 한 줄
REALTIME_SAMPLE = {
    "errorMessage": {"status": 200, "code": "INFO-000", "total": 2},
    "realtimeArrivalList": [
        {"subwayId": "1001", "updnLine": "상행", "trainLineNm": "광운대행 - 시청방면", "statnNm": "서울",
         "btrainSttus": "일반", "barvlDt": "0", "arvlMsg2": "서울 출발", "arvlMsg3": "서울", "arvlCd": "2"},
        {"subwayId": "1063", "updnLine": "상행", "trainLineNm": "문산행 - 신촌(경의중앙선)방면 (급행)",
         "statnNm": "서울", "btrainSttus": "급행", "barvlDt": "185", "arvlMsg2": "3분 후",
         "arvlMsg3": "공덕", "arvlCd": "99"},
    ],
}


class LineNameTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(line_name("01호선"), "1호선")
        self.assertEqual(line_name("2호선"), "2호선")
        self.assertEqual(line_name("경의중앙선"), "경의중앙선")
        self.assertEqual(line_name("수인분당선"), "수인분당선")
        self.assertEqual(line_name("신분당선"), "신분당선")
        self.assertEqual(line_name("공항철도1호선"), "공항철도")
        self.assertEqual(line_color("2호선"), "#00A84D")

    def test_parse_fr(self):
        self.assertEqual(parse_fr("P549"), ("P", 549, None))
        self.assertEqual(parse_fr("211-1"), ("", 211, 1))
        self.assertIsNone(parse_fr(""))


class NetworkTest(unittest.TestCase):
    def test_branches_attach_to_right_station(self):
        n = net()
        e2 = edges(n, "2호선")
        self.assertIn(frozenset(("성수", "용답")), e2)  # 211-1 -> 211
        self.assertIn(frozenset(("용답", "신답")), e2)
        self.assertIn(frozenset(("신도림", "도림천")), e2)  # 234-1 -> 234
        self.assertIn(frozenset(("시청", "충정로")), e2)  # 순환
        e1 = edges(n, "1호선")
        self.assertIn(frozenset(("구로", "가산디지털단지")), e1)  # P142 -> 141
        self.assertNotIn(frozenset(("구일", "가산디지털단지")), e1)

    def test_names_and_transfers(self):
        n = net()
        self.assertIn("서울", n.by_name)  # '서울역' -> '서울'
        self.assertEqual(n.lines_at("서울"), ["1호선", "4호선"])
        a, b = n.by_name["서울"]
        self.assertIn(b, n.adj[a])

    def test_routes(self):
        n = net()
        r = find_route(n, "서울", "남부터미널")
        self.assertEqual([l.line for l in r.legs], ["4호선", "2호선", "3호선"])
        self.assertEqual(r.transfers, 2)
        self.assertEqual(r.legs[-1].stations, ["교대", "남부터미널"])
        r = find_route(n, "용답", "시청")
        self.assertEqual(r.legs[0].stations[:2], ["용답", "성수"])
        self.assertEqual(r.transfers, 0)  # 시청은 1·2호선 둘 다 있지만 환승하지 않음
        self.assertIsNone(find_route(n, "서울", "서울"))
        self.assertIsNone(find_route(n, "서울", "없는역"))

    def test_link_fixes(self):
        from subway import apply_fixes
        n = net()
        self.assertIn(frozenset(("서초", "교대")), edges(n, "2호선"))
        apply_fixes(n, {"2호선": {"remove": [("교대", "서초")], "add": [("서초", "강남")]},
                        "없는선": {"add": [("가", "나")]}})
        e2 = edges(n, "2호선")
        self.assertNotIn(frozenset(("서초", "교대")), e2)
        self.assertIn(frozenset(("서초", "강남")), e2)
        a, b = n.by_name["서초"][0], n.by_name["교대"]
        self.assertTrue(all(x not in n.adj[a] for x in b))
        apply_fixes(n, {"2호선": {"remove": [("교대", "서초")]}})  # 이미 없으면 경고만

    def test_missing_coords_still_builds(self):
        n = build_network([], line_rows())
        self.assertIsNotNone(find_route(n, "을지로입구", "을지로4가"))


class SubwayLoadTest(unittest.TestCase):
    def test_tries_next_key(self):
        from subway import SubwayData
        import subway as sw

        calls = []

        async def fake_fetch(session, key, service):
            calls.append(key)
            if key == "bad":
                raise RuntimeError("INFO-100")
            return master_rows() if service == "subwayStationMaster" else line_rows()

        d = SubwayData(["bad", "good", None, "good"])
        self.assertEqual(d.keys, ["bad", "good"])
        with patch.object(sw, "_fetch_rows", fake_fetch):
            run(d.load(None))
        self.assertTrue(d.ready)
        self.assertEqual(calls, ["bad", "good", "good"])

    def test_no_keys(self):
        from subway import SubwayData
        d = SubwayData([None, ""])
        run(d.load(None))
        self.assertIn("SEOUL_API_KEY", d.error)


class ImageTest(unittest.TestCase):
    def setUp(self):
        try:
            import subway_images  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("Pillow 미설치")

    def test_renders(self):
        import subway_images
        from PIL import Image
        n = net()
        r = find_route(n, "서울", "남부터미널")
        for png in (subway_images.render_route(r, "서울", "남부터미널"),
                    subway_images.render_map(n),
                    subway_images.render_map(n, line="2호선"),
                    subway_images.render_map(n, route=r)):
            img = Image.open(png)
            self.assertEqual(img.format, "PNG")
            self.assertLessEqual(img.height, img.width * 1.4)

    def test_long_leg_folds(self):
        from subway_images import MAX_MIDDLE, _leg_rows
        rows = _leg_rows([f"역{i}" for i in range(12)])
        self.assertEqual([k for k, _ in rows].count("fold"), 1)
        self.assertLessEqual(len(rows), MAX_MIDDLE + 3)
        self.assertEqual(rows[0], ("board", "역0"))
        self.assertEqual(rows[-1], ("alight", "역11"))


class ArrivalTest(unittest.TestCase):
    def test_parse(self):
        a = parse_arrivals(REALTIME_SAMPLE)
        self.assertEqual([x.line for x in a], ["1호선", "경의중앙선"])
        self.assertEqual(a[0].message, "서울 출발")
        self.assertEqual(a[1].message, "3분 5초 후 (공덕)")
        self.assertTrue(a[1].express)
        self.assertEqual(parse_arrivals({"status": 500, "message": "없음"}), [])


def _kma(items):
    return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}}


class WeatherTest(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(latlon_to_grid(37.5665, 126.9780), (60, 127))  # 서울
        self.assertEqual(latlon_to_grid(35.1796, 129.0756), (98, 76))  # 부산

    def test_base_time(self):
        self.assertEqual(base_datetime(datetime(2026, 10, 12, 16, 5)), ("20261012", "1400"))
        self.assertEqual(base_datetime(datetime(2026, 10, 12, 2, 5)), ("20261011", "2300"))
        self.assertEqual(base_datetime(datetime(2026, 10, 12, 2, 15)), ("20261012", "0200"))

    def test_parse_and_pick(self):
        items = []
        for h, tmp, sky, pty, pop in ((16, 18, "1", "0", 0), (17, 17, "3", "0", 10), (19, 15, "4", "1", 60),
                                       (22, 13, "4", "0", 30)):
            t = f"{h:02d}00"
            items += [{"category": "TMP", "fcstDate": "20261012", "fcstTime": t, "fcstValue": str(tmp)},
                      {"category": "SKY", "fcstDate": "20261012", "fcstTime": t, "fcstValue": sky},
                      {"category": "PTY", "fcstDate": "20261012", "fcstTime": t, "fcstValue": pty},
                      {"category": "POP", "fcstDate": "20261012", "fcstTime": t, "fcstValue": str(pop)}]
        items.append({"category": "TMX", "fcstDate": "20261012", "fcstTime": "1500", "fcstValue": "19.0"})
        fc = parse_forecast(_kma(items))
        self.assertEqual(fc.daily["20261012"], (None, 19.0))
        picked = pick_slots(fc, datetime(2026, 10, 12, 16, 20))
        self.assertEqual([s.when.hour for s in picked], [16, 19, 22])
        self.assertEqual(picked[1].text, "비")
        self.assertEqual(picked[0].sky, "☀️")

    def test_error(self):
        with self.assertRaises(RuntimeError):
            parse_forecast({"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}})


def fake_interaction(user_id=1):
    i = MagicMock()
    i.user.id = user_id
    i.response.send_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


class _Resp:
    def __init__(self, data):
        self.data = data

    async def json(self, content_type=None):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@needs_discord
class TravelCommandTest(unittest.TestCase):
    def setUp(self):
        import travel
        self.travel = travel
        self.saved = (travel.state.subway.net, list(travel.state.realtime_keys), travel.state.kma_key,
                      travel.state.session)
        travel.state.subway.net = net()

    def tearDown(self):
        s = self.travel.state
        s.subway.net, s.realtime_keys, s.kma_key, s.session = self.saved

    def test_route_sends_image_and_buttons(self):
        i = fake_interaction()
        run(self.travel.subway_route(i, "서울", "남부터미널"))
        kw = i.followup.send.await_args.kwargs
        self.assertEqual(kw["files"][0].filename, "route.png")
        self.assertIn("환승 2회", kw["embed"].description)
        self.assertIsInstance(kw["view"], self.travel.RouteView)

    def test_route_unknown_station(self):
        i = fake_interaction()
        run(self.travel.subway_route(i, "서울", "뉴욕"))
        self.assertIn("모르는 역", i.response.send_message.await_args.args[0])

    def test_not_ready(self):
        self.travel.state.subway.net = None
        self.travel.state.subway.error = "서울시 API 키(SEOUL_API_KEY)가 아직 없어요."
        self.travel.state._loading = None
        i = fake_interaction()
        run(self.travel.subway_route(i, "서울", "남부터미널"))
        self.assertIn("SEOUL_API_KEY", i.response.send_message.await_args.args[0])

    def test_map(self):
        i = fake_interaction()
        run(self.travel.subway_map(i, "2호선"))
        self.assertEqual(i.followup.send.await_args.kwargs["file"].filename, "subway_map.png")

    def test_arrivals(self):
        s = self.travel.state
        s.realtime_keys = ["k"]
        s.session = MagicMock()
        s.session.get = MagicMock(return_value=_Resp(REALTIME_SAMPLE))
        i = fake_interaction()
        run(self.travel.send_arrivals(i, "서울"))
        embed = i.followup.send.await_args.kwargs["embed"]
        self.assertEqual([f[0] for f in embed.fields], ["1호선", "경의중앙선"])
        self.assertIn("%EC%84%9C%EC%9A%B8", s.session.get.call_args.args[0])  # 역 이름 인코딩

    def test_arrivals_falls_back_to_other_key(self):
        s = self.travel.state
        s.realtime_keys = ["wrong", "right"]
        bad = {"status": 500, "code": "INFO-100", "message": "인증키가 유효하지 않습니다."}
        s.session = MagicMock()
        s.session.get = MagicMock(side_effect=[_Resp(bad), _Resp(REALTIME_SAMPLE)])
        i = fake_interaction()
        run(self.travel.send_arrivals(i, "서울"))
        self.assertEqual(len(i.followup.send.await_args.kwargs["embed"].fields), 2)
        self.assertEqual(s.realtime_keys, ["right", "wrong"])  # 되는 키가 앞으로

    def test_arrivals_no_key(self):
        self.travel.state.realtime_keys = []
        i = fake_interaction()
        run(self.travel.send_arrivals(i, "서울"))
        self.assertIn("SEOUL_SUBWAY_KEY", i.response.send_message.await_args.args[0])

    def test_weather(self):
        s = self.travel.state
        s.kma_key = "k"
        fc = Forecast([], {})
        from weather import Slot
        fc.slots = [Slot(datetime(2099, 1, 1, h), 10.0 + h, "☀️", "맑음", 20) for h in range(0, 24)]
        with patch.object(self.travel, "fetch_forecast", AsyncMock(return_value=fc)):
            i = fake_interaction()
            run(self.travel.send_weather(i, "서울"))
        embed = i.followup.send.await_args.kwargs["embed"]
        self.assertIn("서울 날씨", embed.title)

    def test_weather_no_key(self):
        self.travel.state.kma_key = None
        i = fake_interaction()
        run(self.travel.send_weather(i, "서울"))
        self.assertIn("KMA_API_KEY", i.response.send_message.await_args.args[0])

    def test_midpoint_buttons(self):
        async def go():
            calls = []

            async def start_bus(interaction, dep, arr):
                calls.append((dep, arr))

            v = self.travel.MidpointBusView(["서울", "부산", "상주"], "상주", start_bus)
            self.assertEqual([c.label for c in v.children], ["서울 → 상주 버스", "부산 → 상주 버스"])
            await v.children[1].callback(fake_interaction(7))
            self.assertEqual(calls, [("부산", "상주")])

        run(go())

    def test_match_names(self):
        self.assertEqual(self.travel.match_names(["강남", "신강남", "강남구청"], "강남"), ["강남", "강남구청", "신강남"])


if __name__ == "__main__":
    unittest.main()
