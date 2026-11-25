"""Export the measured camera columns and the original evidence."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .dispersion import png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px", "wavelength_nm", "luma", "r_mean", "g_mean", "b_mean", "rows_used"))
    for p in result["profile"]:
        wavelength = "" if p["wavelength_nm"] is None else f'{p["wavelength_nm"]:.4f}'
        if p["brightness"] is None:
            # 跟线方式下没有任何有效行能对应该标定列：留空，不补造数值。
            writer.writerow((p["x"], wavelength, "", "", "", "", p["rows_used"]))
        else:
            writer.writerow((p["x"], wavelength, f'{p["brightness"]:.3f}',
                             *(f'{channel:.3f}' for channel in p["rgb"]), p["rows_used"]))
    return buffer.getvalue().encode()


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(414,width),height+65), "#161e2d")
    output.paste(Image.fromarray(image),(0,0))
    draw = ImageDraw.Draw(output)
    cfg = card["settings"]
    draw.rectangle((cfg["left"],cfg["top"],cfg["right"],cfg["bottom"]),outline="#8bcccc",width=2)
    if r := result["reading"]:
        draw.rectangle((r["left"],cfg["top"],r["right"],cfg["bottom"]),outline="#e6a56f",width=2)
        detail = f'Window x {r["left"]} to {r["right"]} / signal {r["signal"]:.2f}'
    else:
        detail = "The reading window misses the sampled strip"
    if result.get("track"):
        track = result["track"]
        # 绿点是各行在小窗内实际找到并采用的参考峰；无点的采样行即无证行。
        for item in track["evidence"]:
            x, y = item["peak_x"], item["y"]
            draw.ellipse((x-2, y-2, x+2, y+2), outline="#7ef0b1", width=1)
        detail += f' / tracked peaks {track["rows_used"]} of {track["rows_total"]} rows'
    center = result["center_nm"]
    value = f'{center:.2f} nm' if center is not None else "outside calibration"
    mode = "tracked per-row peaks" if result.get("method") == "track" else "column average"
    draw.text((14,height+14), f'Source x {result["focus"]} / {value} / {mode}', fill="#e7f0fa")
    draw.text((14,height+35), detail, fill="#edc096")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("spectrum.png", original)
        payload = {"card": card, "method": result.get("method", "average"),
                   "reading": result["reading"], "center_nm": result["center_nm"]}
        if result.get("track"):
            payload["track"] = result["track"]
        output.writestr("reading.json", json.dumps(payload, ensure_ascii=False, indent=2))
        output.writestr("strip-profile.csv", trace_table(result))
        output.writestr("strip-proof.png", proof(image, card, result))
    return buffer.getvalue()
