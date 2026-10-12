# travel — 여행 디스코드 봇

| 명령어 | 하는 일 |
|---|---|
| `/버스 출발 도착` | 날짜 버튼(오늘~2주 뒤) → 고속·시외 합친 편 목록 이미지(터미널·잔여석·요금) + 시간 고르면 좌석 배치도 이미지(나만 보임) |
| `/기차 출발 도착` | 날짜 버튼(오늘~2주 뒤) → 시간표 + 일반/특실 예약 가능 여부 (석수는 없음, 글자로) |
| `/중간도시 출발지1 출발지2 …` | 2~5명 출발지에서 모이기 좋은 도시 TOP3 + 각자 그 도시로 가는 버스 바로 조회 버튼 |
| `/지하철 출발역 도착역` | 수도권 지하철 경로 이미지 (환승·역 수·예상 시간) + 지도로 보기 / 출발역 실시간 도착 버튼 |
| `/노선도 [노선]` | 역 좌표로 직접 그린 노선도 (비우면 서울 시내 전체, 노선을 고르면 그 노선 전체) |
| `/도착 역` | 지하철 실시간 도착 정보 (서울 시내 역) |
| `/날씨 도시` | 기상청 단기예보 3시간 간격 + 최저/최고 기온 |

## 실행
```
pip install -r requirements.txt
cp .env.example .env   # DISCORD_TOKEN 채우기
python bot.py
```
테스트: `python -m unittest test_all test_travel -v` (discord.py가 있어야 전부 돌아가요)

버스 결과는 이미지 한 장에 10편씩 보여주고 `◀ 이전` `다음 ▶`로 넘겨요. 오늘 날짜는 이미 출발한 편을 빼고 보여줘요.

## API 키 (Render 환경변수)
| 변수 | 어디서 | 쓰는 곳 |
|---|---|---|
| `SEOUL_API_KEY` | 서울 열린데이터광장 일반 인증키 | /지하철 /노선도 |
| `SEOUL_SUBWAY_KEY` | 서울 열린데이터광장 실시간 지하철 인증키 | /도착 |
| `KMA_API_KEY` | 공공데이터포털 "기상청_단기예보 조회서비스" 일반 인증키(Decoding) | /날씨 |

키가 없는 기능은 안내 메시지만 보여주고, 나머지 기능은 그대로 동작해요.

## 지하철 데이터
- 서울시 `subwayStationMaster`(역 좌표)와 `SearchSTNBySubwayLineInfo`(역 코드)를 시작할 때 한 번 받아요. 출처: 서울특별시 (공공누리 1유형)
- 역 사이 연결은 공개 데이터에 없어서 외부코드 순서 + 좌표로 추정해요 (`subway.py` 맨 위 설명). 시작 로그에 노선별 연결이 찍혀요.
- 노선도는 공식 노선도를 따라 그리지 않고 역 좌표로 직접 그려요 (실제 거리 비율).
- 예상 시간은 역 사이 거리(평균 33km/h) + 정차 30초 + 환승 5분으로 계산한 대략값이에요.

## Render 배포 (무료 웹 서비스)
- Build: `pip install -r requirements.txt` / Start: `python bot.py` / Health check: `/health`
- 환경변수 `DISCORD_TOKEN` 을 넣으면 자동으로 다시 시작돼요. 토큰이 없어도 서버는 떠 있고 로그에 안내가 나와요.
- 무료 플랜은 15분 동안 접속이 없으면 잠들어요. UptimeRobot 같은 곳에서 `/health` 를 5분마다 호출하면 깨어 있어요.

## 현재 상태
- `BUS_PROVIDER=mock`, `TRAIN_PROVIDER=mock` : 테스트 데이터로 전체 흐름 동작 (실제 좌석 아님)
- `BUS_PROVIDER=real` 은 아래 세 곳을 동시에 조회해서 시간순으로 합쳐요. 아직 셋 다 연동 전이에요.

  | 이름 | 클래스 | 종류 |
  |---|---|---|
  | `kobus` | `KobusProvider` | 고속 (고속버스 통합예매) |
  | `bustago` | `BustagoProvider` | 시외 (버스타고) |
  | `tmoney` | `TmoneyIntercityProvider` | 시외 (티머니 시외버스) |

  `kobus,bustago` 처럼 쉼표로 일부만 켤 수도 있어요.
- 한 곳이 실패해도 나머지 결과는 보여주고, 결과 위에 "○○ 조회 실패 — 일부 노선이 빠졌을 수 있어요"를 띄워요.
- 실제 연동은 각 클래스의 `terminals`, `search` 두 메서드만 채우면 돼요.
  `search`는 `kind`를 채운 `list[BusTrip]`을 돌려주면 나머지(합치기·버튼·표·배치도)는 그대로 동작해요.

## 파일
- `bot.py` 명령어와 조회 흐름 / `views.py` 날짜 버튼·결과 화면 / `embeds.py` 표시
- `providers.py` 데이터 공급자 / `midpoint.py` 중간도시 / `cities.py` 도시 좌표·터미널
- `images.py` 편 목록·좌석 배치도 이미지 (Pillow) / `seatmap.py` 좌석 배치 계산
- `fonts/` 한글만 추린 Noto Sans KR (SIL OFL 1.1, `fonts/OFL.txt`)
- `travel.py` 여행 명령어 / `subway.py` 지하철 데이터·경로 / `subway_images.py` 경로·노선도 이미지 / `weather.py` 기상청 예보
