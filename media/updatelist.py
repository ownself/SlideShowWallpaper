#!/usr/bin/env python3
"""Generate list.json containing all media files (images & videos) under this directory.

Usage:
  python updatelist.py                     # scan current script directory
  python updatelist.py <path>              # scan specified root directory
  python updatelist.py -L [path]           # follow directory symlinks
  python updatelist.py --follow-symlinks [path]

Options:
  -L, --follow-symlinks  Follow directory symbolic links (cycle-safe).

The output list.json is written into the root directory being scanned.
Paths inside JSON are POSIX style (forward slashes) relative to the root.
Existing list.json will be overwritten.
"""
from __future__ import annotations

import json
import sys
import os
import argparse
from pathlib import Path
from typing import Iterable

# Supported media extensions (lower-case, without dot)
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "avif", "heic", "heif", "ico"
}
VIDEO_EXTS = {
    "mp4", "webm", "mov", "mkv", "avi", "wmv", "m4v", "mpg", "mpeg", "3gp", "flv", "ogg"
}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Filenames to always ignore
IGNORE_NAMES = {"list.json", Path(__file__).name}


def is_media_file(path: Path) -> bool:
    """Return True if path has a media extension we support."""
    if not path.is_file():
        return False
    if path.name in IGNORE_NAMES:
        return False
    suffix = path.suffix.lower().lstrip('.')
    return suffix in MEDIA_EXTS


def iter_media_files(root: Path, follow_symlinks: bool = False) -> Iterable[Path]:
    """Yield all media file paths under root (recursively).

    If follow_symlinks is True, directory symbolic links are traversed with a
    simple cycle guard (device+inode) to avoid infinite recursion.
    """
    if follow_symlinks:
        # Manual walk to allow followlinks with cycle protection
        seen: set[tuple[int, int]] = set()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            try:
                st = os.stat(dirpath)
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    # Prevent descending further from this directory
                    dirnames[:] = []
                    continue
                seen.add(key)
            except OSError:
                # Skip unreadable directories
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
    """Return path relative to root using forward slashes."""
    rel = path.relative_to(root)
    return rel.as_posix()


def build_list(root: Path, follow_symlinks: bool = False) -> list[str]:
    """Build sorted list of relative media file paths.

    Parameters:
      root: Root directory to scan.
      follow_symlinks: Whether to traverse directory symbolic links.
    """
    items = [make_relative_posix(root, p) for p in iter_media_files(root, follow_symlinks=follow_symlinks)]
    # Sort with simple lexicographical order (case-insensitive for stability)
    items.sort(key=lambda s: s.lower())
    return items


def write_json(root: Path, items: list[str]) -> None:
    """Write items to list.json in root with indent=2 and trailing newline."""
    out_file = root / 'list.json'
    # Ensure deterministic JSON formatting similar to existing style
    with out_file.open('w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write('\n')  # newline at end of file


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="updatelist.py",
        description="Scan directory recursively and generate list.json of media files (images & videos)."
    )
    parser.add_argument(
        "root_dir",
        nargs="?",
        default=str(Path(__file__).resolve().parent),
        help="Root directory to scan (default: script directory)"
    )
    parser.add_argument(
        "-L", "--follow-symlinks",
        action="store_true",
        help="Follow directory symbolic links (cycle-safe)."
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

