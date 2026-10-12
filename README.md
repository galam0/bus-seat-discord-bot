# 버스 잔여석 디스코드 봇

| 명령어 | 하는 일 |
|---|---|
| `/버스 출발 도착` | 날짜 버튼 → 고속·시외 합친 편별 잔여석(`[고속]` `[시외]` 표시) + 시간 고르면 자리 배치도(나만 보임) |
| `/기차 출발 도착` | 날짜 버튼 → 시간표 + 일반/특실 예약 가능 여부 (석수는 없음) |
| `/중간도시 출발지1 출발지2 …` | 2~5명 출발지에서 모이기 좋은 도시 TOP3 (직선거리 기반 추정) |

## 실행
```
pip install -r requirements.txt
cp .env.example .env   # DISCORD_TOKEN 채우기
python bot.py
```
테스트: `python -m unittest -v` (discord.py가 있어야 전부 돌아가요)

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
- `providers.py` 데이터 공급자 / `midpoint.py` 중간도시 / `cities.py` 도시 좌표
