"""Validate and re-encode public uploads independently from private generation files."""
from io import BytesIO
from pathlib import Path
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_POST_BYTES = 30 * 1024 * 1024
MAX_POST_IMAGES = 9


def store_post_images(images: list[bytes], directory: Path, post_id: str) -> list[dict]:
    if len(images) > MAX_POST_IMAGES or sum(map(len, images)) > MAX_POST_BYTES:
        raise HTTPException(400, "最多 9 张图片，合计不超过 30MB")
    output = []
    directory.mkdir(parents=True, exist_ok=False)
    for index, data in enumerate(images):
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "单张图片不能超过 10MB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as source:
                    if source.format not in {"PNG", "JPEG", "WEBP"} or source.width * source.height > 24_000_000:
                        raise ValueError("unsupported image")
                    image = ImageOps.exif_transpose(source).convert("RGBA" if "A" in source.getbands() else "RGB")
                    image.thumbnail((2048, 2048))
                    width, height = image.size
                    image.save(directory / f"{index}.webp", "WEBP", quality=90, method=4)
                    image.thumbnail((720, 720))
                    image.save(directory / f"{index}-preview.webp", "WEBP", quality=82, method=4)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(400, "图片无法读取，请使用有效的 PNG、JPG 或 WebP，像素不超过 2400 万") from exc
        prefix = f"/community-media/{post_id}/{index}"
        output.append({"image": prefix + ".webp", "thumbnail": prefix + "-preview.webp", "width": width, "height": height})
    return output
