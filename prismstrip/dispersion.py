"""Measure a photographed dispersed light strip with manual wavelength references."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, integer, number

from . import doublet


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def decode(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("单张原片不得超过18 MiB")
    try:
        with Image.open(BytesIO(data)) as photo:
            if photo.format not in {"PNG", "JPEG", "TIFF", "BMP", "WEBP"} or getattr(photo, "n_frames", 1) != 1:
                raise Invalid("请选择单帧 PNG、JPEG、TIFF、BMP 或 WebP")
            if min(photo.size) < 32 or max(photo.size) > 2600 or math.prod(photo.size) > 4_000_000:
                raise Invalid("原片边长须在32–2600 px内，总像素不超过400万")
            normal = ImageOps.exif_transpose(photo).convert("RGB")
            return normal.size, png(normal)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise Invalid("原片无法解码") from error


def pixels(path):
    with Image.open(path) as photo:
        return np.asarray(photo.convert("RGB"))


def settings(value, size):
    width, height = size
    left = integer(value.get("left"), "左标记 X", 0, width - 9)
    top = integer(value.get("top"), "采样上行", 0, height - 1)
    left_nm = number(value.get("left_nm"), "左标记波长", 100, 1900)
    right_nm = number(value.get("right_nm"), "右标记波长", 100, 1900)
    if left_nm == right_nm:
        raise Invalid("两个波长参考须不同")
    return {"left": left, "right": integer(value.get("right"), "右标记 X", left + 8, width - 1),
            "top": top, "bottom": integer(value.get("bottom"), "采样下行", top, height - 1),
            "left_nm": left_nm, "right_nm": right_nm,
            "focus": integer(value.get("focus"), "取样 X", 0, width - 1),
            "half_width": integer(value.get("half_width"), "窗口半宽", 1, min(width, 300)),
            "floor": number(value.get("floor"), "背景亮度", 0, 254)}


def defaults(size):
    w, h = size
    return settings({"left": w//5, "right": w*4//5, "top": h*2//5, "bottom": h*3//5,
                     "left_nm": 420, "right_nm": 690, "focus": w//2, "half_width": 12, "floor": 18}, size)


def wavelength(x, config):
    if not config["left"] <= x <= config["right"]:
        return None
    frac = (x-config["left"]) / (config["right"]-config["left"])
    return config["left_nm"] + frac * (config["right_nm"]-config["left_nm"])


def point(image, x, y, config, reading=None, split=None):
    rgb = image[y, x]
    return {"x": x, "y": y, "rgb": [int(value) for value in rgb],
            "brightness": float(rgb @ LUMA), "wavelength_nm": wavelength(x, config),
            "window_band": doublet.window_membership(x, reading, split)}


def profile(image, config):
    left, right = config["left"], config["right"]
    top, bottom = config["top"], config["bottom"]
    strip = image[top:bottom+1, left:right+1]
    average = np.mean(strip, axis=0)
    levels = average @ LUMA
    return [{"x": x, "wavelength_nm": wavelength(x, config),
             "brightness": float(level), "rgb": values.tolist()}
            for x, level, values in zip(range(left,right+1), levels, average)]


def band(values, config):
    low, high = config["focus"] - config["half_width"], config["focus"] + config["half_width"]
    selected = [sample for sample in values if low <= sample["x"] <= high]
    if not selected:
        return None
    corrected = [max(0, sample["brightness"] - config["floor"]) for sample in selected]
    return {"left": selected[0]["x"], "right": selected[-1]["x"], "columns": len(selected),
            "signal": float(math.fsum(corrected)),
            "peak": max(selected, key=lambda sample: sample["brightness"]),
            "_selected": selected}


def split_band(reading, config):
    """Resolve two lines inside the current window; fall back to the combined read."""
    selected = reading["_selected"]
    decision = doublet.resolve(selected, config["floor"])
    if not decision["resolved"]:
        return {"status": "unresolved", "reason": decision["reason"],
                "supported_peaks": decision.get("supported_peaks", []),
                "rejected_valleys": decision.get("rejected_valleys", [])}
    return doublet.split_reading(selected, config["floor"], decision)


def report(image, config):
    values = profile(image, config)
    reading = band(values, config)
    split = split_band(reading, config) if reading else None
    if reading:
        reading = {key: value for key, value in reading.items() if not key.startswith("_")}
    return {"profile": values, "reading": reading, "split": split,
            "focus": config["focus"], "center_nm": wavelength(config["focus"], config)}


def practice():
    w, h = 744, 436
    cfg = settings({"left": 220, "right": 520, "top": 181, "bottom": 246,
                    "left_nm": 450, "right_nm": 650, "focus": 370, "half_width": 13, "floor": 50}, (w,h))
    yy, xx = np.indices((h,w))
    control = np.array([[144,91,220], [72,125,251], [69,221,219], [92,235,140],
                        [221,237,83], [255,174,67], [239,91,84]], dtype=float)
    anchors = np.array([176,218,269,336,409,488,560])
    colors = np.stack([np.interp(xx, anchors, control[:, k]) for k in range(3)], axis=-1)
    cross = np.exp(-((yy - 215 - 2*np.sin(xx/62))/35)**6)
    broad = 0.38 * np.exp(-((xx-375)/197)**6)
    lines = np.zeros_like(broad)
    for x, width, strength in [(233,3.0,.35),(291,4.7,.32),(364,3.9,.55),(374,4.6,.41),(414,4,.51),(494,3.4,.48)]:
        lines += strength*np.exp(-((xx-x - 1.7*((yy-215)/36)**2)/width)**2)
    signal = (cross*(broad+lines))[..., None]
    noise = (1.2*np.sin(xx*.23+yy*.61)+1.5*np.cos(xx*.61-yy*.16))[..., None]
    background = np.array([15,19,29]) + noise
    image = Image.fromarray((background+colors*signal).clip(0,255).astype("uint8"), "RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=15)
    draw.text((26,57), "DISPERSION STUDY / A", fill="#9bb0c8", font=font)
    for tx, caption in [(220,"450"),(520,"650")]:
        draw.line((tx,285,tx,296),fill="#899aaf",width=1)
        draw.text((tx-10,304),caption,fill="#b4c6d9",font=ImageFont.load_default(size=12))
    draw.text((26,372),"SIMULATED CAMERA STRIP",fill="#9bb0c8",font=ImageFont.load_default(size=11))
    return image.size, png(image), cfg
