"""(임시) 혼잡도·기차 API 응답 형식 확인. 키 값은 로그에 남기지 않는다."""
import logging
import os
from urllib.parse import quote

import aiohttp

log = logging.getLogger("seatbot.probe")


def _mask(text: str, *keys) -> str:
    for k in keys:
        if k:
            text = text.replace(k, "***").replace(quote(k, safe=""), "***")
    return text


async def run_probe():
    seoul = os.getenv("SEOUL_API_KEY")
    kma = os.getenv("KMA_API_KEY")
    tests = []
    if seoul:
        base = f"http://openapi.seoul.go.kr:8088/{seoul}/json"
        for name in ("광화문·덕수궁", "홍대 관광특구", "강남역", "명동 관광특구"):
            tests.append((f"인구 {name}", f"{base}/citydata_ppltn/1/5/{quote(name)}"))
    if kma:
        k = quote(kma, safe="")
        tago = "https://apis.data.go.kr/1613000/TrainInfoService"
        tests += [
            ("기차 도시", f"{tago}/getCtyCodeList?serviceKey={k}&_type=json"),
            ("기차 역", f"{tago}/getCtyAcctoTrainSttnList?serviceKey={k}&_type=json&cityCode=11&numOfRows=5"),
            ("기차 종류", f"{tago}/getVhcleKndList?serviceKey={k}&_type=json"),
            ("기차 조회", f"{tago}/getStrtpntAlocFndTrainInfo?serviceKey={k}&_type=json&numOfRows=3"
                         f"&depPlaceId=NAT010000&arrPlaceId=NAT014445&depPlandTime=20261013"),
        ]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        for name, url in tests:
            try:
                async with s.get(url) as r:
                    text = await r.text()
                log.info("PROBE %s %s %s", name, r.status, _mask(text, seoul, kma)[:1500].replace("\n", " "))
            except Exception as e:
                log.info("PROBE %s 실패 %r", name, e)
