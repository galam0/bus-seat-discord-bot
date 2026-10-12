"""(임시) 새 기능용 공개 API 응답 형식 확인. 키 값은 로그에 남기지 않는다."""
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
    seoul = [k for k in (os.getenv("SEOUL_API_KEY"), os.getenv("SEOUL_SUBWAY_KEY")) if k]
    kma = os.getenv("KMA_API_KEY")
    keys = [*seoul, kma]
    tests = []
    for k in seoul[:1]:
        base = f"http://openapi.seoul.go.kr:8088/{k}/json"
        tests += [
            ("시간표ID", f"{base}/SearchSTNTimeTableByIDService/1/3/0222/1/1/"),
            ("시간표ID2", f"{base}/SearchSTNTimeTableByIDService/1/3/0222/2/2/"),
            ("첫막차", f"{base}/SearchFirstAndLastTrainbyLineServiceNew/1/3/2호선/1/1/"),
            ("첫막차2", f"{base}/SearchFirstAndLastTrainbyLineService/1/3/"),
            ("막차ID", f"{base}/SearchLastTrainTimeByIDService/1/3/0222/1/1/"),
            ("첫차ID", f"{base}/SearchFirstTrainTimeByIDService/1/3/0222/1/1/"),
        ]
    if kma:
        common = f"serviceKey={quote(kma, safe='')}"
        tour = "MobileOS=ETC&MobileApp=travel&_type=json&numOfRows=2&pageNo=1"
        tests += [
            ("관광2", f"https://apis.data.go.kr/B551011/KorService2/locationBasedList2?{common}&{tour}"
                     f"&mapX=126.978&mapY=37.5665&radius=2000&contentTypeId=12&arrange=E"),
            ("관광1", f"https://apis.data.go.kr/B551011/KorService1/locationBasedList1?{common}&{tour}"
                     f"&mapX=126.978&mapY=37.5665&radius=2000&contentTypeId=12&arrange=E"),
            ("축제2", f"https://apis.data.go.kr/B551011/KorService2/searchFestival2?{common}&{tour}"
                     f"&eventStartDate=20261012&areaCode=1"),
            ("미세먼지", f"https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty?{common}"
                       f"&returnType=json&numOfRows=2&pageNo=1&sidoName={quote('서울')}&ver=1.0"),
        ]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        for name, url in tests:
            try:
                async with s.get(url) as r:
                    text = await r.text()
                log.info("PROBE %s %s %s", name, r.status, _mask(text, *keys)[:1200].replace("\n", " "))
            except Exception as e:
                log.info("PROBE %s 실패 %r", name, e)
