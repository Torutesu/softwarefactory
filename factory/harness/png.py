"""UI ベンチマーク比較: PNG の読み書きと類似スコア (標準ライブラリのみ)。

- デコード対応: bit depth 8, color type 0 (grey) / 2 (RGB) / 3 (palette) / 6 (RGBA)、非インターレース
- 類似度: 両画像を GRID×GRID にプーリング縮小し、RGB 平均絶対誤差を 0..1 の類似値に変換
    1.0 = 完全一致, 0.0 = 完全不一致
"""
from __future__ import annotations

import struct
import zlib
from typing import List, Tuple

GRID = 64

MAX_PIXELS = 40_000_000  # 展開後のメモリ上限の目安 (RGBA で ~160MB)
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# encode (主にテスト・デモ用)
# ---------------------------------------------------------------------------
def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """RGBA bytes (4*w*h) から PNG を作る。"""
    raw = b"".join(
        b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height)
    )
    idat = zlib.compress(raw, 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return _PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------
class PngImage:
    def __init__(self, width: int, height: int, rgba: List[Tuple[int, int, int, int]]):
        self.width = width
        self.height = height
        self.data = rgba


def _read_chunk(buf: memoryview, pos: int) -> Tuple[bytes, memoryview, int]:
    (length,) = struct.unpack_from(">I", buf, pos)
    tag = bytes(buf[pos + 4:pos + 8])
    data = buf[pos + 8:pos + 8 + length]
    pos += 8 + length + 4  # + CRC
    return tag, data, pos


def decode_png(data: bytes) -> PngImage:
    if not data.startswith(_PNG_SIG):
        raise ValueError("not a PNG file")
    buf = memoryview(data)
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    plte = None
    idat = bytearray()
    while pos < len(buf):
        try:
            tag, chunk, pos = _read_chunk(buf, pos)
            if tag == b"IHDR":
                width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", bytes(chunk))
        except struct.error:
            raise ValueError("broken PNG: truncated chunk") from None
        if tag == b"IHDR":
            pass
        elif tag == b"PLTE":
            plte = bytes(chunk)
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break
    if width is None or not idat:
        raise ValueError("broken PNG: IHDR/IDAT missing")
    if bit_depth != 8:
        raise ValueError("unsupported bit depth: %s (8 のみ)" % bit_depth)
    if interlace != 0:
        raise ValueError("interlaced PNG is unsupported")
    if color_type not in (0, 2, 3, 6):
        raise ValueError("unsupported color type: %s" % color_type)
    if width <= 0 or height <= 0:
        raise ValueError("broken PNG: width/height is 0")
    if width * height > MAX_PIXELS:
        raise ValueError("PNG too large: %dx%d (max %d px)" % (width, height, MAX_PIXELS))
    if color_type == 3 and (not plte or len(plte) % 3):
        raise ValueError("broken PNG: palette image without valid PLTE")
    channels = {0: 1, 2: 3, 3: 1, 6: 4}[color_type]
    stride = width * channels
    expected = (stride + 1) * height
    try:
        # 展開後サイズの上限付き (圧縮爆弾で メモリを食い潰さない)
        raw = zlib.decompressobj().decompress(bytes(idat), expected + 1)
    except zlib.error as e:
        raise ValueError("broken PNG: %s" % e) from None
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _y in range(height):
        if i >= len(raw):
            raise ValueError("broken PNG: not enough scanlines")
        filt = raw[i]
        i += 1
        row = bytearray(raw[i:i + stride])
        if len(row) < stride:
            raise ValueError("broken PNG: truncated scanline")
        i += stride
        _unfilter(filt, row, prev, channels)
        _convert_row(row, channels, color_type, plte, out)
        prev = row
    rgba = [(out[j], out[j + 1], out[j + 2], out[j + 3]) for j in range(0, len(out), 4)]
    return PngImage(width, height, rgba)


def _unfilter(filt: int, row: bytearray, prev: bytearray, bpp: int) -> None:
    if filt == 0:
        return
    if filt == 1:  # Sub
        for i in range(bpp, len(row)):
            row[i] = (row[i] + row[i - bpp]) & 0xFF
    elif filt == 2:  # Up
        for i in range(len(row)):
            row[i] = (row[i] + prev[i]) & 0xFF
    elif filt == 3:  # Average
        for i in range(len(row)):
            left = row[i - bpp] if i >= bpp else 0
            row[i] = (row[i] + (left + prev[i]) // 2) & 0xFF
    elif filt == 4:  # Paeth
        for i in range(len(row)):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            if pa <= pb and pa <= pc:
                pred = a
            elif pb <= pc:
                pred = b
            else:
                pred = c
            row[i] = (row[i] + pred) & 0xFF
    else:
        raise ValueError("bad filter type: %s" % filt)


def _convert_row(row: bytearray, channels: int, color_type: int, plte, out: bytearray) -> None:
    n = len(row) // channels
    if color_type == 6:
        for i in range(n):
            j = i * channels
            out.extend(row[j:j + 4])
    elif color_type == 2:
        for i in range(n):
            j = i * channels
            out.extend(row[j:j + 3] + b"\xff")
    elif color_type == 0:
        for i in range(n):
            g = row[i]
            out.extend(bytes((g, g, g, 255)))
    else:  # palette
        entries = len(plte) // 3
        for i in range(n):
            idx = row[i]
            if idx >= entries:
                raise ValueError("broken PNG: palette index %d out of range" % idx)
            r, g, b = plte[idx * 3:idx * 3 + 3]
            out.extend(bytes((r, g, b, 255)))


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------
def _pool(img: PngImage, grid: int = GRID):
    """画像を grid×grid (RGB) に nearest サンプリングでリサンプルする。

    サイズ違いの比較に耐え、大きな画像でも grid² 回の参照で済む。
    """
    W, H = img.width, img.height
    out = []
    for gy in range(grid):
        sy = min(H - 1, int(gy * H / grid))
        row = []
        for gx in range(grid):
            sx = min(W - 1, int(gx * W / grid))
            px = img.data[sy * W + sx]
            row.append((px[0], px[1], px[2]))
        out.append(row)
    return out


def similarity(a_path, b_path, grid: int = GRID) -> float:
    """2枚の PNG の類似度 (0..1)。1.0 = 完全一致。"""
    with open(a_path, "rb") as f:
        aimg = decode_png(f.read())
    with open(b_path, "rb") as f:
        bimg = decode_png(f.read())
    return similarity_images(aimg, bimg, grid)


def similarity_images(aimg: PngImage, bimg: PngImage, grid: int = GRID) -> float:
    grid = max(1, int(grid))
    ga = _pool(aimg, grid)
    gb = _pool(bimg, grid)
    diff = sum(abs(pa[k] - pb[k]) for ra, rb in zip(ga, gb) for pa, pb in zip(ra, rb) for k in range(3))
    denom = 255 * 3 * grid * grid
    return round(1.0 - diff / denom, 4)
