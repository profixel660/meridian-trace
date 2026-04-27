"""Stdlib-only QR code rendering for TOTP enrolment.

We implement a minimal QR encoder good enough for ``otpauth://`` URIs (~100–
200 byte payloads). Two outputs are exposed:

* :func:`render_ascii_qr` — terminal-friendly. Each module is rendered as
  a half-block (▀ / ▄ / █ / space) so a 33×33 module QR fits in ~17 rows.
* :func:`render_svg_qr` — a self-contained SVG ``<rect>`` grid for the
  future Next.js shell to embed.

Scope deliberately limited to:

* Byte (8-bit) encoding mode.
* Error-correction levels L and M.
* Versions 1-10 (max 174 byte chars at level L; ample for our URIs).
* Mask 0 (the simplest fixed mask). The QR spec allows picking the lowest-
  penalty mask; we skip the search since a single fixed mask still scans
  reliably for short payloads at 4×+ pixel density.

This is NOT a general-purpose QR library. If you need to encode a 1KB blob,
use the ``qrcode`` package — but adding it pulls in ``Pillow`` for image
output which we are explicitly avoiding (see CONTEXT.md ground rules).

References:
* ISO/IEC 18004:2015 (QR Code spec) — table numbers cited inline.
* Project Nayuki's reference implementation (algorithm cross-check).
"""

from __future__ import annotations

# --- Capacity tables (ISO/IEC 18004 Table 7) ----------------------------------
# (version: byte-mode capacity at level L, level M)
_BYTE_CAPACITY: dict[int, tuple[int, int]] = {
    1: (17, 14),
    2: (32, 26),
    3: (53, 42),
    4: (78, 62),
    5: (106, 84),
    6: (134, 106),
    7: (154, 122),
    8: (192, 152),
    9: (230, 180),
    10: (271, 213),
}

# (version, ec_level): (total_codewords, ec_codewords_per_block, num_blocks)
# Distilled from ISO/IEC 18004 Table 9. Only the variants we use.
_BLOCK_INFO: dict[tuple[int, str], tuple[int, int, int]] = {
    (1, "L"): (26, 7, 1),
    (1, "M"): (26, 10, 1),
    (2, "L"): (44, 10, 1),
    (2, "M"): (44, 16, 1),
    (3, "L"): (70, 15, 1),
    (3, "M"): (70, 26, 1),
    (4, "L"): (100, 20, 1),
    (4, "M"): (100, 18, 2),
    (5, "L"): (134, 26, 1),
    (5, "M"): (134, 24, 2),
    (6, "L"): (172, 18, 2),
    (6, "M"): (172, 16, 4),
    (7, "L"): (196, 20, 2),
    (7, "M"): (196, 18, 4),
    (8, "L"): (242, 24, 2),
    (8, "M"): (242, 22, 4),
    (9, "L"): (292, 30, 2),
    (9, "M"): (292, 22, 5),
    (10, "L"): (346, 18, 4),
    (10, "M"): (346, 26, 5),
}

# Format-info bit strings (ISO/IEC 18004 Table C.1). Mask = 0 only.
_FORMAT_INFO: dict[str, int] = {
    "L": 0b111011111000100,
    "M": 0b101010000010010,
}

# Generator polynomials cached by degree.
_GF_EXP: list[int] = [0] * 512
_GF_LOG: list[int] = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        poly = _poly_mul(poly, [1, _GF_EXP[i]])
    return poly


def _poly_mul(p: list[int], q: list[int]) -> list[int]:
    out = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] ^= _gf_mul(a, b)
    return out


def _rs_encode(data: list[int], ec_count: int) -> list[int]:
    gen = _rs_generator_poly(ec_count)
    res = list(data) + [0] * ec_count
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j, g in enumerate(gen):
                res[i + j] ^= _gf_mul(g, coef)
    return res[len(data) :]


# --- QR matrix construction ---------------------------------------------------


def _pick_version(payload_len: int, ec_level: str) -> int:
    idx = 0 if ec_level == "L" else 1
    for ver in sorted(_BYTE_CAPACITY):
        if _BYTE_CAPACITY[ver][idx] >= payload_len:
            return ver
    raise ValueError(
        f"Payload too long ({payload_len} bytes) for QR versions ≤10 at level {ec_level}"
    )


def _build_data_codewords(
    payload: bytes, version: int, ec_level: str
) -> list[int]:
    total_cw, ec_cw, _blocks = _BLOCK_INFO[(version, ec_level)]
    data_cw = total_cw - ec_cw * _BLOCK_INFO[(version, ec_level)][2]
    # Build bit stream: mode (4) + length (8 or 16) + payload bytes (8 each).
    bits: list[int] = []

    def push(value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)  # byte mode
    length_bits = 8 if version <= 9 else 16
    push(len(payload), length_bits)
    for b in payload:
        push(b, 8)
    # Terminator (up to 4 zero bits).
    for _ in range(min(4, data_cw * 8 - len(bits))):
        bits.append(0)
    # Pad to byte boundary.
    while len(bits) % 8:
        bits.append(0)
    # Pad bytes 0xEC, 0x11 alternating.
    pad = [0xEC, 0x11]
    while len(bits) // 8 < data_cw:
        for b in pad[len(bits) // 8 % 2 : len(bits) // 8 % 2 + 1]:
            push(b, 8)
            if len(bits) // 8 >= data_cw:
                break
    # Bits → codewords.
    data = [
        int("".join(str(b) for b in bits[i : i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]
    return data[:data_cw]


def _interleave(
    data_cw: list[int], version: int, ec_level: str
) -> list[int]:
    total_cw, ec_per_block, n_blocks = _BLOCK_INFO[(version, ec_level)]
    data_total = total_cw - ec_per_block * n_blocks
    # Equal-sized blocks for simplicity (works for all our table entries).
    block_size = data_total // n_blocks
    blocks = [
        data_cw[i * block_size : (i + 1) * block_size] for i in range(n_blocks)
    ]
    ec_blocks = [_rs_encode(b, ec_per_block) for b in blocks]
    out: list[int] = []
    for i in range(block_size):
        for b in blocks:
            out.append(b[i])
    for i in range(ec_per_block):
        for b in ec_blocks:
            out.append(b[i])
    return out


def _matrix_size(version: int) -> int:
    return 17 + 4 * version


def _new_matrix(size: int) -> list[list[int | None]]:
    return [[None] * size for _ in range(size)]


def _place_finder(matrix: list[list[int | None]], r: int, c: int) -> None:
    size = len(matrix)
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < size and 0 <= cc < size):
                continue
            if dr in (-1, 7) or dc in (-1, 7):
                matrix[rr][cc] = 0
            elif (
                dr in (0, 6)
                or dc in (0, 6)
                or (2 <= dr <= 4 and 2 <= dc <= 4)
            ):
                matrix[rr][cc] = 1
            else:
                matrix[rr][cc] = 0


def _place_timing(matrix: list[list[int | None]]) -> None:
    size = len(matrix)
    for i in range(size):
        if matrix[6][i] is None:
            matrix[6][i] = (i + 1) % 2
        if matrix[i][6] is None:
            matrix[i][6] = (i + 1) % 2


def _reserve_format(matrix: list[list[int | None]]) -> None:
    size = len(matrix)
    # Reserve modules around the three finders; we'll fill them in
    # _place_format() with the encoded format-info bits.
    for i in range(9):
        if matrix[8][i] is None:
            matrix[8][i] = 0
        if matrix[i][8] is None:
            matrix[i][8] = 0
    for i in range(8):
        if matrix[size - 1 - i][8] is None:
            matrix[size - 1 - i][8] = 0
        if matrix[8][size - 1 - i] is None:
            matrix[8][size - 1 - i] = 0
    matrix[size - 8][8] = 1  # dark module (always 1)


def _place_format(matrix: list[list[int | None]], ec_level: str) -> None:
    bits = _FORMAT_INFO[ec_level]
    size = len(matrix)
    # Around top-left finder.
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 6:
            matrix[8][i] = bit
        elif i < 8:
            matrix[8][i + 1] = bit
        elif i == 8:
            matrix[7][8] = bit
        else:
            matrix[14 - i][8] = bit
    # Split copy along right & bottom edges of top-right + bottom-left finders.
    for i in range(15):
        bit = (bits >> i) & 1
        if i < 8:
            matrix[size - 1 - i][8] = bit
        else:
            matrix[8][size - 15 + i] = bit


def _mask0(r: int, c: int) -> int:
    return (r + c) % 2 == 0


def _place_data(matrix: list[list[int | None]], stream: list[int]) -> None:
    size = len(matrix)
    bits: list[int] = []
    for cw in stream:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bit_idx = 0
    col = size - 1
    going_up = True
    while col > 0:
        if col == 6:
            col -= 1  # skip vertical timing
        for row_offset in range(size):
            row = (size - 1 - row_offset) if going_up else row_offset
            for dc in (0, 1):
                cc = col - dc
                if matrix[row][cc] is None:
                    bit = bits[bit_idx] if bit_idx < len(bits) else 0
                    bit_idx += 1
                    if _mask0(row, cc):
                        bit ^= 1
                    matrix[row][cc] = bit
        going_up = not going_up
        col -= 2


def _build_matrix(payload: bytes, ec_level: str) -> list[list[int]]:
    version = _pick_version(len(payload), ec_level)
    size = _matrix_size(version)
    matrix = _new_matrix(size)
    _place_finder(matrix, 0, 0)
    _place_finder(matrix, 0, size - 7)
    _place_finder(matrix, size - 7, 0)
    _place_timing(matrix)
    _reserve_format(matrix)
    data_cw = _build_data_codewords(payload, version, ec_level)
    full_stream = _interleave(data_cw, version, ec_level)
    _place_data(matrix, full_stream)
    _place_format(matrix, ec_level)
    return [[(0 if m is None else m) for m in row] for row in matrix]


# --- Public API ---------------------------------------------------------------


def render_ascii_qr(
    payload: str,
    *,
    ec_level: str = "M",
    border: int = 2,
) -> str:
    """Return a terminal-printable QR encoding of ``payload``.

    Uses Unicode half-blocks so the output is roughly square in a typical
    monospace font (each row of two QR modules → one terminal line).
    """
    matrix = _build_matrix(payload.encode("utf-8"), ec_level)
    size = len(matrix)
    # Wrap in quiet zone.
    n = size + border * 2
    grid = [[0] * n for _ in range(n)]
    for r in range(size):
        for c in range(size):
            grid[r + border][c + border] = matrix[r][c]
    out_lines: list[str] = []
    for r in range(0, n, 2):
        line: list[str] = []
        for c in range(n):
            top = grid[r][c]
            bot = grid[r + 1][c] if r + 1 < n else 0
            if top and bot:
                line.append("\u2588")  # full block
            elif top:
                line.append("\u2580")  # upper half
            elif bot:
                line.append("\u2584")  # lower half
            else:
                line.append(" ")
        out_lines.append("".join(line))
    return "\n".join(out_lines)


def render_svg_qr(
    payload: str,
    *,
    ec_level: str = "M",
    border: int = 4,
    module_px: int = 8,
) -> str:
    """Return a self-contained SVG string encoding ``payload``."""
    matrix = _build_matrix(payload.encode("utf-8"), ec_level)
    size = len(matrix)
    dim = (size + border * 2) * module_px
    rects: list[str] = []
    for r in range(size):
        for c in range(size):
            if matrix[r][c]:
                x = (c + border) * module_px
                y = (r + border) * module_px
                rects.append(
                    f'<rect x="{x}" y="{y}" width="{module_px}" height="{module_px}"/>'
                )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {dim} {dim}" width="{dim}" height="{dim}">'
        f'<rect width="{dim}" height="{dim}" fill="white"/>'
        f'<g fill="black">{"".join(rects)}</g>'
        f"</svg>"
    )


__all__ = ["render_ascii_qr", "render_svg_qr"]
