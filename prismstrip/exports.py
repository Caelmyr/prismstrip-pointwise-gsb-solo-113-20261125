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
    writer.writerow(("x_px", "wavelength_nm", "luma", "r_mean", "g_mean", "b_mean"))
    for p in result["profile"]:
        writer.writerow((p["x"], f'{p["wavelength_nm"]:.4f}', f'{p["brightness"]:.3f}',
                         *(f'{channel:.3f}' for channel in p["rgb"])))
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
    center = result["center_nm"]
    value = f'{center:.2f} nm' if center is not None else "outside calibration"
    draw.text((14,height+14), f'Source x {result["focus"]} / {value}', fill="#e7f0fa")
    draw.text((14,height+35), detail, fill="#edc096")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("spectrum.png", original)
        output.writestr("reading.json", json.dumps({"card":card,"reading":result["reading"],
                                                    "center_nm":result["center_nm"]},ensure_ascii=False,indent=2))
        output.writestr("strip-profile.csv",trace_table(result))
        output.writestr("strip-proof.png",proof(image,card,result))
    return buffer.getvalue()
