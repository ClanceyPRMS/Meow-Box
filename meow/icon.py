"""生成喵盒的图标 —— 纯 Python 画一个猫爪印，不依赖 Pillow。

用法：
    python -m meow.icon            # 生成 assets/meowbox.ico + 预览图
    python -m meow.icon --size 256 # 只生成一张预览 PNG

原理：先在一个 4 倍大的画布上算出「覆盖没覆盖」（这样边缘就有抗锯齿），
再缩小回来得到 alpha。ICO 里的每一张图就是 BITMAPINFOHEADER + BGRA 像素
（自下而上）+ 1bpp 的 AND 掩码。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

# 猫爪：4 个脚趾 + 1 个掌垫（归一化坐标，左上角是 0,0）
SHAPES = [
    (0.170, 0.440, 0.110, 0.100),   # 左外趾
    (0.385, 0.275, 0.118, 0.108),   # 左内趾
    (0.615, 0.275, 0.118, 0.108),   # 右内趾
    (0.830, 0.440, 0.110, 0.100),   # 右外趾
    (0.500, 0.735, 0.270, 0.190),   # 掌垫
]

TOP = (255, 173, 203)        # 上方：浅粉
BOTTOM = (231, 74, 138)      # 下方：玫红
RIM = 0.80                   # 边缘压暗系数（让 16px 下也有轮廓）
SS = 4                       # 超采样倍数

ICO_SIZES = (16, 32, 48, 64)


def render(size: int):
    """返回 [(r,g,b,a), ...] 的行列表（自上而下）。"""
    big = size * SS
    # 1) 在超采样画布上算覆盖
    cover = [[False] * big for _ in range(big)]
    for y in range(big):
        ny = (y + 0.5) / big
        row = cover[y]
        for x in range(big):
            nx = (x + 0.5) / big
            for cx, cy, rx, ry in SHAPES:
                dx = (nx - cx) / rx
                dy = (ny - cy) / ry
                if dx * dx + dy * dy <= 1.0:
                    row[x] = True
                    break

    # 2) 缩小成 alpha
    alpha = [[0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    if cover[y * SS + sy][x * SS + sx]:
                        hits += 1
            alpha[y][x] = (hits * 255) // (SS * SS)

    # 3) 上色（竖直渐变）+ 边缘压暗
    rows = []
    for y in range(size):
        t = y / (size - 1) if size > 1 else 0.0
        base = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        row = []
        for x in range(size):
            a = alpha[y][x]
            if a == 0:
                row.append((0, 0, 0, 0))
                continue
            edge = False
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not (0 <= nx < size and 0 <= ny < size) or alpha[ny][nx] == 0:
                    edge = True
                    break
            color = tuple(int(c * RIM) for c in base) if edge else base
            row.append((color[0], color[1], color[2], a))
        rows.append(row)
    return rows


def _ico_bitmap(rows, size: int) -> bytes:
    """把一行行像素包成 ICO 里那张 BMP（32bpp，自下而上，外加 AND 掩码）。"""
    xor = bytearray()
    for row in reversed(rows):                      # BMP 是从下往上存的
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))              # BMP 里是 BGRA
    stride = ((size + 31) // 32) * 4                # AND 掩码每行按 4 字节对齐
    and_mask = b"\x00" * (stride * size)            # 32bpp 的图不看掩码，全 0 即可
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
        len(xor) + len(and_mask), 0, 0, 0, 0,
    )
    return header + bytes(xor) + and_mask


def write_ico(path, sizes=ICO_SIZES) -> Path:
    images = [_ico_bitmap(render(s), s) for s in sizes]
    directory = struct.pack("<HHH", 0, 1, len(images))              # ICONDIR
    offset = len(directory) + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in zip(sizes, images):
        entries += struct.pack(
            "<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(blob), offset,
        )
        blobs += blob
        offset += len(blob)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(directory + entries + blobs)
    return path


def write_png(path, size: int = 128) -> Path:
    rows = render(size)
    raw = b"".join(
        b"\x00" + bytes(v for px in row for v in px) for row in rows
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    from . import runtime
    root = runtime.app_root()
    if "--size" in argv:
        size = int(argv[argv.index("--size") + 1])
        out = write_png(root / "assets" / f"meowbox-{size}.png", size)
        print(f"预览图：{out}")
        return 0
    ico = write_ico(root / "assets" / "meowbox.ico")
    png = write_png(root / "assets" / "meowbox-preview.png", 128)
    print(f"图标：{ico}  ({ico.stat().st_size} 字节)")
    print(f"预览：{png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
