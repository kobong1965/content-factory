from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "output" / "s3" / "fixture" / "ocr-fixture.png"
FONT = Path("C:/Windows/Fonts/msyh.ttc")


def main() -> None:
    if not FONT.is_file():
        raise SystemExit("找不到微软雅黑字体，无法生成可重复 OCR 样例")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1200, 600), "white")
    draw = ImageDraw.Draw(image)
    title = ImageFont.truetype(str(FONT), 92)
    subtitle = ImageFont.truetype(str(FONT), 58)
    draw.rectangle((48, 48, 1152, 552), outline="#1c2230", width=6)
    draw.text((96, 112), "直筒西裤 XZ-2308", fill="#111827", font=title)
    draw.text((96, 300), "先看垂感  再看版型", fill="#3b4a67", font=subtitle)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
