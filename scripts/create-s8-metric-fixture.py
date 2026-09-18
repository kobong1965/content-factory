from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "output" / "s8" / "fixture" / "metric-screenshot.png"
FONT = Path("C:/Windows/Fonts/msyh.ttc")


def main() -> None:
    if not FONT.is_file():
        raise SystemExit("找不到微软雅黑字体，无法生成 S8 OCR 样例")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1400, 900), "white")
    draw = ImageDraw.Draw(image)
    title = ImageFont.truetype(str(FONT), 76)
    value = ImageFont.truetype(str(FONT), 70)
    draw.rectangle((55, 55, 1345, 845), outline="#222838", width=6)
    draw.text((110, 105), "抖音作品数据", fill="#111827", font=title)
    draw.text((110, 275), "播放量 12800", fill="#111827", font=value)
    draw.text((110, 425), "点赞 920", fill="#111827", font=value)
    draw.text((110, 575), "完播率 31%", fill="#111827", font=value)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
