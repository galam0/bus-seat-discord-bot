"""/어디가지: 조건에 맞는 여행지를 랜덤으로 뽑는다."""
import random
from dataclasses import dataclass

from cities import PLACES
from midpoint import est_minutes, fmt_minutes, haversine_km

THEMES = ["바다", "산·자연", "역사·문화", "도시·맛집"]
THEME_EMOJI = {"바다": "🌊", "산·자연": "⛰️", "역사·문화": "🏯", "도시·맛집": "🍜"}

# 여행지 -> (테마들, 한 줄 소개)
INFO: dict[str, tuple[tuple[str, ...], str]] = {
    "서울": (("도시·맛집", "역사·문화"), "고궁부터 한강까지, 볼 게 끝없는 수도"),
    "인천": (("바다", "도시·맛집"), "차이나타운과 월미도, 바다 보이는 항구 도시"),
    "수원": (("역사·문화", "도시·맛집"), "수원화성 성곽길과 통닭거리"),
    "의정부": (("도시·맛집",), "부대찌개 거리의 원조 동네"),
    "평택": (("도시·맛집",), "송탄 미군기지 근처 이국적인 거리"),
    "춘천": (("산·자연", "도시·맛집"), "닭갈비·막국수와 호수 드라이브"),
    "강릉": (("바다", "도시·맛집"), "경포대·안목 커피거리, 동해 바다"),
    "속초": (("바다", "산·자연"), "설악산과 속초 중앙시장, 바다 한가득"),
    "원주": (("산·자연",), "소금산 출렁다리와 뮤지엄산"),
    "충주": (("산·자연",), "충주호와 활옥동굴"),
    "제천": (("산·자연",), "청풍호 케이블카와 의림지"),
    "천안": (("도시·맛집", "역사·문화"), "호두과자와 독립기념관"),
    "서산": (("바다", "역사·문화"), "해미읍성과 간월암"),
    "당진": (("바다",), "왜목마을 서해 일출"),
    "공주": (("역사·문화",), "공산성과 무령왕릉, 백제의 옛 수도"),
    "논산": (("역사·문화", "산·자연"), "탑정호 출렁다리와 관촉사"),
    "청주": (("역사·문화", "도시·맛집"), "수암골과 상당산성"),
    "세종": (("도시·맛집", "산·자연"), "호수공원과 국립세종수목원"),
    "대전": (("도시·맛집", "산·자연"), "성심당 빵지순례와 장태산"),
    "전주": (("역사·문화", "도시·맛집"), "한옥마을과 비빔밥, 먹거리 천국"),
    "익산": (("역사·문화",), "미륵사지와 백제 유적"),
    "군산": (("역사·문화", "바다"), "근대 거리와 선유도"),
    "광주": (("도시·맛집", "역사·문화"), "양림동 골목과 무등산"),
    "목포": (("바다", "도시·맛집"), "해상케이블카와 낭만 항구"),
    "순천": (("산·자연",), "순천만 습지와 국가정원"),
    "여수": (("바다", "도시·맛집"), "여수 밤바다와 해상케이블카"),
    "김천": (("산·자연",), "직지사와 부항댐 출렁다리"),
    "구미": (("산·자연",), "금오산 등산"),
    "상주": (("산·자연",), "경천섬과 자전거길"),
    "안동": (("역사·문화", "도시·맛집"), "하회마을과 안동찜닭"),
    "대구": (("도시·맛집",), "동성로와 막창, 김광석길"),
    "포항": (("바다", "도시·맛집"), "호미곶 일출과 영일대 해수욕장"),
    "경주": (("역사·문화",), "황리단길과 불국사, 천년 고도"),
    "울산": (("바다", "산·자연"), "대왕암공원과 태화강 국가정원"),
    "부산": (("바다", "도시·맛집"), "해운대·광안리, 돼지국밥과 밀면"),
    "김해": (("역사·문화",), "가야 유적과 봉하마을"),
    "창원": (("바다", "도시·맛집"), "진해 군항제와 마산 어시장"),
    "진주": (("역사·문화",), "진주성과 남강 유등"),
    "통영": (("바다", "도시·맛집"), "케이블카와 동피랑, 충무김밥"),
    "거제": (("바다",), "바람의 언덕과 외도"),
    "제주": (("바다", "산·자연"), "한라산과 오름, 바다 드라이브 (비행기·배)"),
    "서귀포": (("바다", "산·자연"), "폭포와 올레길 (비행기·배)"),
    "가평": (("산·자연",), "남이섬·아침고요수목원, 캠핑 성지"),
    "양양": (("바다",), "서핑 해변과 낙산사"),
    "단양": (("산·자연",), "도담삼봉과 패러글라이딩"),
    "담양": (("산·자연",), "죽녹원 대나무 숲과 떡갈비"),
    "남해": (("바다",), "독일마을과 다랭이마을"),
    "보령": (("바다",), "대천해수욕장과 머드"),
    "태안": (("바다",), "꽃지해변 노을과 안면도"),
    "포천": (("산·자연",), "산정호수와 아트밸리"),
}
ISLANDS = {"제주", "서귀포"}  # 버스로 못 가는 곳


@dataclass
class Pick:
    place: str
    minutes: int | None  # 버스 예상 (섬이거나 출발지가 없으면 None)
    km: float | None  # 출발지가 없으면 None
    themes: tuple[str, ...]
    blurb: str

    def travel_text(self) -> str:
        if self.km is None:
            return "✈️ 비행기·배로 가요" if self.place in ISLANDS else ""
        if self.minutes is None:
            return "✈️ 비행기·배로 가요"
        return f"🚌 버스로 약 {fmt_minutes(self.minutes)} ({self.km:.0f}km)"


def candidates(origin: str | None, max_minutes: int | None, theme: str | None) -> list[Pick]:
    """origin 이 None 이면 출발지 없이 전국에서 고른다 (시간 조건은 무시)."""
    if origin is not None and origin not in PLACES:
        raise KeyError(origin)
    out = []
    for name, (themes, blurb) in INFO.items():
        if name == origin or name not in PLACES:
            continue
        if theme and theme not in themes:
            continue
        if origin is None:
            out.append(Pick(name, None, None, themes, blurb))
            continue
        km = haversine_km(PLACES[origin], PLACES[name])
        if name in ISLANDS:
            if max_minutes is not None:
                continue  # 시간 조건이 있으면 섬은 빼기
            out.append(Pick(name, None, km, themes, blurb))
            continue
        mins = est_minutes(km)
        if mins < 40:
            continue  # 너무 가까우면 여행 기분이 안 나니 빼기
        if max_minutes is not None and mins > max_minutes:
            continue
        out.append(Pick(name, mins, km, themes, blurb))
    return out


def pick(origin: str | None, max_minutes: int | None, theme: str | None, rng: random.Random | None = None,
         avoid: set[str] | None = None) -> tuple[Pick | None, int]:
    """(뽑힌 곳, 후보 수). 방금 나온 곳(avoid)은 되도록 피한다."""
    rng = rng or random.Random()
    cands = candidates(origin, max_minutes, theme)
    fresh = [c for c in cands if not avoid or c.place not in avoid] or cands
    return (rng.choice(fresh) if fresh else None), len(cands)
