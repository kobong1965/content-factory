"""Build exact, reusable logo assets from the user-supplied raster mark."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps


def build_assets(source_path: Path, output_root: Path) -> tuple[Path, Path]:
    source = Image.open(source_path).convert("RGB")
    grayscale = ImageOps.grayscale(source)
    alpha = grayscale.point(lambda value: 255 - value)
    visible = alpha.point(lambda value: 255 if value >= 5 else 0)
    bounds = visible.getbbox()
    if bounds is None:
        raise ValueError("源图片中没有识别到深色 LOGO")

    mark_alpha = alpha.crop(bounds)
    max_width, max_height = 760, 660
    scale = min(max_width / mark_alpha.width, max_height / mark_alpha.height)
    size = (max(1, round(mark_alpha.width * scale)), max(1, round(mark_alpha.height * scale)))
    mark_alpha = mark_alpha.resize(size, Image.Resampling.LANCZOS)
    mark = Image.new("RGBA", size, (0, 0, 0, 0))
    mark.putalpha(mark_alpha)

    transparent = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    position = ((1024 - size[0]) // 2, (1024 - size[1]) // 2)
    transparent.alpha_composite(mark, position)
    icon = Image.new("RGBA", transparent.size, "white")
    icon.alpha_composite(transparent)

    branding = output_root / "apps" / "desktop" / "branding"
    public = output_root / "apps" / "desktop" / "public"
    branding.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    transparent_path = branding / "brand-logo-master.png"
    icon_path = branding / "app-icon-master.png"
    transparent.save(transparent_path, optimize=True)
    icon.convert("RGB").save(icon_path, optimize=True)
    transparent.save(public / "brand-logo.png", optimize=True)
    icon.convert("RGB").save(public / "app-icon.png", optimize=True)
    return transparent_path, icon_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    transparent, icon = build_assets(arguments.source.resolve(), arguments.project_root.resolve())
    print(transparent)
    print(icon)


if __name__ == "__main__":
    main()
