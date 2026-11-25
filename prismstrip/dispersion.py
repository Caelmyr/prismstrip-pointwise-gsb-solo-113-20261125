"""Measure a photographed dispersed light strip with manual wavelength references."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid, choice, integer, number


LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_IMAGE_BYTES = 18 * 1024 * 1024
# 行间峰位每错开一列的代价，与亮度差（0–255）同尺度，兼顾看清的实峰与上下位移连续。
TRACK_CONTINUITY = 24.0


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
    return {"method": choice(value.get("method", "average"), "跟线方式", ("average", "track")),
            "left": left, "right": integer(value.get("right"), "右标记 X", left + 8, width - 1),
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


def point(image, x, y, config):
    rgb = image[y, x]
    result = {"x": x, "y": y, "rgb": [int(value) for value in rgb],
              "brightness": float(rgb @ LUMA), "wavelength_nm": wavelength(x, config)}
    if config["method"] == "track":
        track = peak_track(image, config)
        shift = track["shifts"][y - config["top"]] if config["top"] <= y <= config["bottom"] else None
        if shift is not None:
            # 按该行判出的整数偏移反推与当前取样 X 对应的原片列，再做同一标定换算。
            result["corrected_x"] = x - shift
            result["wavelength_nm"] = wavelength(x - shift, config)
        else:
            # 采样行里找不到实峰的无证行：保留原片读数，但不报校正值；采样行外也不臆造。
            result["corrected_x"] = None
            result["wavelength_nm"] = None
    return result


def profile(image, config):
    left, right = config["left"], config["right"]
    top, bottom = config["top"], config["bottom"]
    strip = image[top:bottom+1, left:right+1]
    average = np.mean(strip, axis=0)
    levels = average @ LUMA
    return [{"x": x, "wavelength_nm": wavelength(x, config),
             "brightness": float(level), "rgb": values.tolist(), "rows_used": len(strip)}
            for x, level, values in zip(range(left,right+1), levels, average)]


def row_peaks(luma, low, high, floor):
    """只在小窗内找该行的实峰：两侧都要有实际落下的亮度，且峰位于≥3个连续超背景列中。"""
    peaks = []
    for col in range(max(1, low), min(high, len(luma) - 1) + 1):
        if not (luma[col] > luma[col - 1] and luma[col] > luma[col + 1]):
            continue
        run_left = col
        while run_left > low and luma[run_left - 1] > floor:
            run_left -= 1
        run_right = col
        while run_right < high and luma[run_right + 1] > floor:
            run_right += 1
        if run_right - run_left + 1 >= 3:
            peaks.append(col)
    return peaks


def peak_track(image, config):
    """在已有采样行、focus±half_width 小窗内逐行找真峰，并沿行兼顾峰亮度与位移连续。

    找不到实峰的行留作无证（shift 为 None），不拿相邻行补。采用后各行按整数列偏移
    把参考峰对齐到当前取样 X。
    """
    top, bottom = config["top"], config["bottom"]
    focus, half = config["focus"], config["half_width"]
    width = image.shape[1]
    low, high = max(0, focus - half), min(width - 1, focus + half)
    luma = image[top:bottom+1] @ LUMA
    floor = config["floor"]
    candidates = [row_peaks(luma[r], low, high, floor) for r in range(luma.shape[0])]

    # Viterbi 只在有实峰的证据行之间连接；发射代价取该行最亮峰与候选峰的亮度差，
    # 转移代价惩罚相邻证据行的峰位跳变（中间隔无证行也只按实际证据连接，不补行）。
    scores, links = [], []
    previous = None
    for r, cols in enumerate(candidates):
        if not cols:
            continue
        brightest = max(float(luma[r, c]) for c in cols)
        emission = {c: brightest - float(luma[r, c]) for c in cols}
        if previous is None:
            scores.append(dict(emission))
            links.append({c: None for c in cols})
        else:
            current, back = {}, {}
            for c in cols:
                source = min(previous, key=lambda p: previous[p] + TRACK_CONTINUITY * abs(c - p))
                current[c] = previous[source] + TRACK_CONTINUITY * abs(c - source) + emission[c]
                back[c] = source
            scores.append(current)
            links.append(back)
        previous = scores[-1]

    chosen = [None] * len(candidates)
    if scores:
        evidence_rows = [r for r, cols in enumerate(candidates) if cols]
        position = min(scores[-1], key=lambda c: scores[-1][c])
        for k in range(len(scores) - 1, -1, -1):
            chosen[evidence_rows[k]] = position
            position = links[k][position]

    rows, shifts, evidence = [], [], []
    for r, col in enumerate(chosen):
        y = top + r
        if col is None:
            shifts.append(None)
            continue
        shift = int(col - focus)
        rows.append(y)
        shifts.append(shift)
        evidence.append({"y": y, "peak_x": int(col), "shift": shift,
                         "brightness": float(luma[r, col])})
    return {"low": low, "high": high, "rows": rows, "shifts": shifts, "evidence": evidence,
            "rows_total": len(candidates), "rows_used": len(rows)}


def tracked_profile(image, config, track=None):
    """按同一标定编号（left..right 各列）求对齐后的条带列均值。

    各有效行只在其整数偏移后仍落在原片内的列上贡献；超范围样点不补，
    没有任何有效行对应的列不臆造，亮度与 RGB 留空。
    """
    track = track or peak_track(image, config)
    left, right = config["left"], config["right"]
    top = config["top"]
    width = image.shape[1]
    shifts = [shift for shift in track["shifts"] if shift is not None]
    row_index = [r for r, shift in enumerate(track["shifts"]) if shift is not None]
    columns = np.arange(left, right + 1)
    values = []
    if shifts:
        offsets = np.array(shifts)[:, None]
        sources = columns[None, :] + offsets
        inside = (sources >= 0) & (sources < width)
        frame = image[top + np.array(row_index)]
        summed = np.where(inside[:, :, None],
                          frame[np.arange(len(row_index))[:, None],
                                np.clip(sources, 0, width - 1)].astype(float), 0.0).sum(axis=0)
        counts = inside.sum(axis=0)
        luma_all = frame.astype(float) @ LUMA
        luma_summed = np.where(inside, luma_all[np.arange(len(row_index))[:, None],
                                                np.clip(sources, 0, width - 1)], 0.0).sum(axis=0)
        for x, count, brightness, total in zip(columns, counts, luma_summed, summed):
            if count:
                values.append({"x": int(x), "wavelength_nm": wavelength(int(x), config),
                               "brightness": float(brightness / count),
                               "rgb": (total / count).tolist(), "rows_used": int(count)})
            else:
                values.append({"x": int(x), "wavelength_nm": wavelength(int(x), config),
                               "brightness": None, "rgb": None, "rows_used": 0})
    else:
        for x in columns:
            values.append({"x": int(x), "wavelength_nm": wavelength(int(x), config),
                           "brightness": None, "rgb": None, "rows_used": 0})
    return values, track


def band(values, config):
    low, high = config["focus"] - config["half_width"], config["focus"] + config["half_width"]
    selected = [sample for sample in values if low <= sample["x"] <= high and sample["brightness"] is not None]
    if not selected:
        return None
    corrected = [max(0, sample["brightness"] - config["floor"]) for sample in selected]
    return {"left": selected[0]["x"], "right": selected[-1]["x"], "columns": len(selected),
            "signal": float(sum(corrected)), "peak": max(selected, key=lambda sample: sample["brightness"])}


def report(image, config):
    focus = config["focus"]
    if config["method"] == "track":
        values, track = tracked_profile(image, config)
        return {"method": "track", "profile": values, "reading": band(values, config),
                "focus": focus, "center_nm": wavelength(focus, config), "track": track}
    values = profile(image, config)
    return {"method": "average", "profile": values, "reading": band(values, config),
            "focus": focus, "center_nm": wavelength(focus, config), "track": None}


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
