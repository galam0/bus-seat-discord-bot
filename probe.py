"""(임시) 서울시 공개 API 응답 형식 확인용. 샘플 키로 몇 줄만 받아 로그에 남긴다."""
import json
import logging
from urllib.parse import quote

import aiohttp

log = logging.getLogger("seatbot.probe")

URLS = [
    "http://openapi.seoul.go.kr:8088/sample/json/subwayStationMaster/1/1000/",
    "http://openapi.seoul.go.kr:8088/sample/json/SearchSTNBySubwayLineInfo/100/105/",
    "http://swopenapi.seoul.go.kr/api/subway/sample/json/realtimeStationArrival/1/5/" + quote("서울"),
    "http://swopenapi.seoul.go.kr/api/subway/sample/json/realtimeStationArrival/0/5/" + quote("서울"),
]


async def run_probe():
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        for url in URLS:
            try:
                async with s.get(url) as r:
                    text = await r.text()
                    log.info("PROBE %s -> %s %s", url, r.status, text[:1500].replace("\n", " "))
                    try:
                        data = json.loads(text)
                        log.info("PROBE keys %s", json.dumps(data, ensure_ascii=False)[:1500])
                    except ValueError:
                        pass
            except Exception as e:
                log.info("PROBE %s -> 실패 %r", url, e)
