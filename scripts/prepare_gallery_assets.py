"""Create web delivery copies of the supplied showcase images without cropping."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_directory", type=Path)
    args = parser.parse_args()
    catalog_path = ROOT / "app/gallery_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    destination = ROOT / "app/static/gallery"
    destination.mkdir(parents=True, exist_ok=True)
    for post in catalog:
        with Image.open(args.source_directory / post["source_filename"]) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            post["width"], post["height"] = image.size
            image.save(destination / f'{post["id"]}.webp', "WEBP", quality=95, method=6)
            image.thumbnail((640, 900), Image.Resampling.LANCZOS)
            image.save(destination / f'{post["id"]}-preview.webp', "WEBP", quality=87, method=6)
        print("PREPARED", post["id"])
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
