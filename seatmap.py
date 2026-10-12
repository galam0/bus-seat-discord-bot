"""좌석 배치 계산 (이미지와 텍스트가 같이 씀)."""

AISLE = 2  # 통로 칸 위치


def seats_per_row(total: int) -> int:
    return 3 if total <= 28 else 4  # 우등 2+1, 일반 2+2


def seat_rows(total: int) -> list[list[tuple[int, int]]]:
    """줄마다 [(칸 위치, 좌석 번호)]. 칸 위치: 0,1 = 왼쪽 / 2 = 통로 / 3,(4) = 오른쪽.

    마지막 줄이 한 자리 남으면 실제 버스처럼 통로까지 막아 한 줄로 앉힌다.
    (28석 = 3석 x 8줄 + 4석 / 45석 = 4석 x 10줄 + 5석)
    """
    per = seats_per_row(total)
    cols = [0, 1, 3] if per == 3 else [0, 1, 3, 4]
    rows: list[list[tuple[int, int]]] = []
    n = 1
    last_full = total % per == 1 and total > per
    body = total - (per + 1) if last_full else total
    while n <= body:
        row = []
        for c in cols:
            if n > body:
                break
            row.append((c, n))
            n += 1
        rows.append(row)
    if last_full:
        rows.append([(c, n + i) for i, c in enumerate(sorted(cols + [AISLE]))])
    return rows


def render_seat_map(total: int, free) -> str:
    """텍스트 배치도 (이미지를 못 만들 때 대신 씀). 숫자 = 빈 자리, ■■ = 찬 자리."""
    free = set(free)
    width = 4 if seats_per_row(total) == 3 else 5
    lines = ["[앞]"]
    for row in seat_rows(total):
        cells = ["  "] * width
        for c, num in row:
            cells[c] = f"{num:02d}" if num in free else "■■"
        if all(c != AISLE for c, _ in row):
            cells[AISLE] = "|"
        lines.append(" ".join(cells).rstrip())
    lines.append("")
    lines.append("숫자 = 빈 자리 / ■■ = 찬 자리")
    return "\n".join(lines)
