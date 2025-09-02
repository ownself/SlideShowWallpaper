#!/usr/bin/env python3
"""生成 (或更新) list.json ：扫描指定根目录下所有支持的图片与视频文件。

用法:
  python updatelist.py                     # 扫描脚本所在目录
  python updatelist.py <path>              # 扫描指定根目录
  python updatelist.py -L [path]           # 跟随目录符号链接(防循环)
  python updatelist.py --follow-symlinks [path]

选项:
  -L, --follow-symlinks  跟随目录符号链接 (具循环保护)。

输出:
  在被扫描根目录下写入 list.json (覆盖写)。
  现在的 JSON 结构为: 列表(List) 里每个元素是一个对象(Object)，字段:
    filename     相对根目录的 POSIX 风格路径 (字符串)
    orientation  "landscape" | "portrait" | "square" | "unknown" （不再输出 width/height 字段）

示例:
  [
    {
      "filename": "videos/sample.mp4",
      "orientation": "landscape"
    },
    {
      "filename": "images/picture.jpg",
      "orientation": "portrait"
    }
  ]

说明:
  * 分辨率内部仍会尝试获取(图片 Pillow 或后备解析 / 视频 ffprobe), 仅用于推断 orientation, 但最终 JSON 不再包含 width/height 字段。
  * 若无法获取尺寸则 orientation 为 "unknown"。
  * orientation 逻辑: 宽>高:landscape, 高>宽:portrait, 相等:square, 任一未知:unknown。
  * 若你仍需包含 width/height 的版本, 请回退到历史版本或自行修改 get_media_metadata()。
"""
from __future__ import annotations

import json
import sys
import os
import argparse
import subprocess
from pathlib import Path
from typing import Iterable, Optional, List, Dict, Any

# 可选依赖 Pillow
try:  # pragma: no cover - 环境可能无 Pillow
    from PIL import Image  # type: ignore
except Exception:  # noqa: BLE001
    Image = None  # type: ignore

# 支持的媒体扩展 (小写, 不含点)
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "avif", "heic", "heif", "ico"
}
VIDEO_EXTS = {
    "mp4", "webm", "mov", "mkv", "avi", "wmv", "m4v", "mpg", "mpeg", "3gp", "flv", "ogg"
}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# 忽略的文件名
IGNORE_NAMES = {"list.json", Path(__file__).name}


def is_media_file(path: Path) -> bool:
    """判断是否为支持的媒体文件。"""
    if not path.is_file():
        return False
    if path.name in IGNORE_NAMES:
        return False
    suffix = path.suffix.lower().lstrip('.')
    return suffix in MEDIA_EXTS


def iter_media_files(root: Path, follow_symlinks: bool = False) -> Iterable[Path]:
    """递归遍历 root 下所有媒体文件。

    follow_symlinks=True 时会跟随目录符号链接, 并用 (device,inode) 记录避免循环。
    """
    if follow_symlinks:
        seen: set[tuple[int, int]] = set()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            try:
                st = os.stat(dirpath)
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    dirnames[:] = []
                    continue
                seen.add(key)
            except OSError:
                continue
            for filename in filenames:
                p = Path(dirpath) / filename
                if is_media_file(p):
                    yield p
    else:
        for p in root.rglob('*'):
            if is_media_file(p):
                yield p


def make_relative_posix(root: Path, path: Path) -> str:
    """返回相对 root 的 POSIX 风格路径。"""
    rel = path.relative_to(root)
    return rel.as_posix()


# -------------------- 元数据获取工具函数 --------------------

# 基础二进制后备解析：支持常见 PNG / JPEG / GIF / BMP，当 Pillow 缺失或失败时使用。
# 说明：不是完整格式解析，仅提取宽高，足以满足大多数普通文件。复杂/特例文件可能失败返回 (None, None)。

def _fallback_get_image_size(path: Path) -> tuple[Optional[int], Optional[int]]:  # noqa: C901 (允许较多分支)
    try:
        with path.open('rb') as f:
            header = f.read(32)
            # PNG: 8-byte signature + IHDR
            if header.startswith(b'\x89PNG\r\n\x1a\n') and len(header) >= 24:
                # 宽高位于 IHDR 数据的前 8 字节 (大端)
                # 偏移: 8(sig) + 4(length) + 4('IHDR') = 16 起始
                w = int.from_bytes(header[16:20], 'big')
                h = int.from_bytes(header[20:24], 'big')
                return w, h
            # GIF: "GIF87a" 或 "GIF89a"，宽高: 6~10 (小端 16bit)
            if header[:6] in (b'GIF87a', b'GIF89a') and len(header) >= 10:
                w = int.from_bytes(header[6:8], 'little')
                h = int.from_bytes(header[8:10], 'little')
                return w, h
            # BMP: 头以 'BM' 开始，宽高在 DIB 头 (BITMAPINFOHEADER) 偏移 18/22 (4 bytes little-endian)
            if header.startswith(b'BM'):
                # 需要读取更多头部
                f.seek(0)
                dib = f.read(26)
                if len(dib) >= 26:
                    w = int.from_bytes(dib[18:22], 'little', signed=True)
                    h = int.from_bytes(dib[22:26], 'little', signed=True)
                    # 有些 BMP 高度为负表示自顶向下，取绝对值
                    return abs(w), abs(h)
            # JPEG: 扫描 SOF* 段以获取宽高。
            # 注意：以下标记没有长度字段，需要直接跳过：
            #   0xD8(SOI) 0xD9(EOI) 0xD0-0xD7(RST0-7) 0x01(TEM)
            #   遇到 0xDA(SOS) 说明后面是图像数据，若此前未找到 SOF* 则放弃。
            if header.startswith(b'\xFF\xD8'):  # JPEG SOI
                f.seek(2)
                while True:
                    # 读到第一个 0xFF
                    b = f.read(1)
                    if not b:
                        break
                    if b != b'\xFF':
                        continue
                    # 跳过可能连续的填充 0xFF
                    while True:
                        marker_byte = f.read(1)
                        if not marker_byte:
                            return None, None
                        if marker_byte != b'\xFF':
                            break
                    marker = marker_byte[0]
                    # 无长度字段的标记直接继续
                    if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
                        continue
                    if marker == 0xDA:  # SOS，后续无 SOF* 再出现，结束
                        break
                    # 其余应有长度字段
                    length_bytes = f.read(2)
                    if len(length_bytes) != 2:
                        break
                    seg_length = int.from_bytes(length_bytes, 'big')
                    if seg_length < 2:
                        break
                    # SOF0~3 / SOF5~7 / SOF9~11 / SOF13~15 包含尺寸
                    if (0xC0 <= marker <= 0xC3) or (0xC5 <= marker <= 0xC7) or (0xC9 <= marker <= 0xCB) or (0xCD <= marker <= 0xCF):
                        data = f.read(seg_length - 2)
                        if len(data) >= 5:
                            h = int.from_bytes(data[1:3], 'big')
                            w = int.from_bytes(data[3:5], 'big')
                            return w, h
                        break
                    else:
                        # 跳过本段剩余内容
                        f.seek(seg_length - 2, 1)
    except Exception:
        return None, None
    return None, None


def get_image_size(path: Path) -> tuple[Optional[int], Optional[int]]:
    """获取图片宽高 (优先 Pillow, 失败使用后备解析)。失败返回 (None, None)。"""
    # 1. 若 Pillow 可用尝试读取
    if Image is not None:
        try:
            with Image.open(path) as im:  # type: ignore[attr-defined]
                w, h = im.size  # (width, height)
                return int(w), int(h)
        except Exception:  # pragma: no cover
            pass
    # 2. Pillow 不存在或失败 -> 后备解析
    return _fallback_get_image_size(path)


def get_video_size(path: Path) -> tuple[Optional[int], Optional[int]]:
    """使用 ffprobe 获取视频宽高。失败返回 (None, None)。

    依赖外部命令 ffprobe (通常在 FFmpeg 套件中)。
    命令形式: ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x <file>
    输出示例: 1920x1080
    """
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', str(path)
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=True
        )
    except (FileNotFoundError, subprocess.SubprocessError):  # ffprobe 不存在或执行失败
        return None, None

    line = proc.stdout.strip().split('\n')[0].strip()
    if 'x' not in line:
        return None, None
    w_str, h_str = line.split('x', 1)
    try:
        w = int(w_str)
        h = int(h_str)
        return w, h
    except ValueError:
        return None, None


def infer_orientation(width: Optional[int], height: Optional[int]) -> str:
    """根据宽高推断方向。"""
    if width is None or height is None:
        return 'unknown'
    if width > height:
        return 'landscape'
    if height > width:
        return 'portrait'
    return 'portrait' # 方形图按照竖图处理
    # return 'square'


def get_media_metadata(path: Path, root: Path) -> dict[str, Any]:
    """获取单个媒体文件的元数据字典。"""
    rel = make_relative_posix(root, path)
    suffix = path.suffix.lower().lstrip('.')
    width: Optional[int]
    height: Optional[int]

    if suffix in IMAGE_EXTS:
        width, height = get_image_size(path)
    elif suffix in VIDEO_EXTS:
        width, height = get_video_size(path)
    else:  # 不应到达，兜底
        width, height = None, None

    orientation = infer_orientation(width, height)
    return {
        'filename': rel,
        'orientation': orientation,
    }


# -------------------- 主构建逻辑 --------------------

def build_list(root: Path, follow_symlinks: bool = False) -> List[dict[str, Any]]:
    """构建包含元数据的媒体列表。

    返回按 filename (不区分大小写) 排序后的列表。
    """
    items = [get_media_metadata(p, root) for p in iter_media_files(root, follow_symlinks=follow_symlinks)]
    items.sort(key=lambda d: d['filename'].lower())
    return items


def write_json(root: Path, items: List[dict[str, Any]]) -> None:
    """写入 list.json (UTF-8, indent=2, 行尾换行)。"""
    out_file = root / 'list.json'
    with out_file.open('w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write('\n')


# -------------------- CLI 入口 --------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="updatelist.py",
        description="递归扫描目录, 生成包含媒体文件元数据(分辨率/方向)的 list.json。"
    )
    parser.add_argument(
        "root_dir",
        nargs="?",
        default=str(Path(__file__).resolve().parent),
        help="根目录 (默认: 脚本所在目录)"
    )
    parser.add_argument(
        "-L", "--follow-symlinks",
        action="store_true",
        help="跟随目录符号链接 (循环安全)"
    )
    args = parser.parse_args(argv[1:])

    root = Path(args.root_dir).resolve()

    if not root.exists():
        print(f"Error: root directory does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"Error: specified path is not a directory: {root}", file=sys.stderr)
        return 3

    items = build_list(root, follow_symlinks=args.follow_symlinks)
    write_json(root, items)

    print(f"Found {len(items)} media files. Written to {root / 'list.json'}")
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main(sys.argv))

