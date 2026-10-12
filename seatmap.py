def render_seat_map(total: int, free) -> str:
    """좌석 배치도를 코드블록용 텍스트로 만든다. 숫자 = 빈 자리, ■■ = 찬 자리."""
    free = set(free)
    per_row = 3 if total <= 28 else 4  # 우등 2+1, 일반 2+2
    lines = ["[앞]"]
    for start in range(1, total + 1, per_row):
        cells = [
            f"{n:02d}" if n in free else "■■"
            for n in range(start, min(start + per_row, total + 1))
        ]
        line = " ".join(cells[:2])
        if cells[2:]:
            line += "  |  " + " ".join(cells[2:])
        lines.append(line)
    lines.append("")
    lines.append("숫자 = 빈 자리 / ■■ = 찬 자리")
    return "\n".join(lines)
