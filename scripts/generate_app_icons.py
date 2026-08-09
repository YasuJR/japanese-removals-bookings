#!/usr/bin/env python3
"""Generate favicon and Apple Touch Icon assets from the company logo."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "branding" / "japanese-removals-logo.png"
STATIC = ROOT / "static"
ICON_BACKGROUND = (255, 255, 255, 255)


def _square_icon(size: int, padding: float = 0.92) -> Image.Image:
    src = Image.open(SOURCE).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), ICON_BACKGROUND)
    scale = min(size / src.width, size / src.height) * padding
    width = max(1, int(src.width * scale))
    height = max(1, int(src.height * scale))
    resized = src.resize((width, height), Image.LANCZOS)
    offset = ((size - width) // 2, (size - height) // 2)
    canvas.paste(resized, offset, resized)
    return canvas


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit("Logo not found: {0}".format(SOURCE))

    apple = _square_icon(180)
    apple.save(STATIC / "apple-touch-icon.png", format="PNG")

    favicon_32 = _square_icon(32, padding=0.95)
    favicon_16 = _square_icon(16, padding=0.95)
    favicon_32.save(STATIC / "favicon-32x32.png", format="PNG")
    favicon_16.save(STATIC / "favicon-16x16.png", format="PNG")

    favicon_32.save(
        STATIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )

    print("Generated:")
    for path in (
        "apple-touch-icon.png",
        "favicon-32x32.png",
        "favicon-16x16.png",
        "favicon.ico",
    ):
        target = STATIC / path
        with Image.open(target) as img:
            print("  {0}: {1}x{2}".format(path, img.width, img.height))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
