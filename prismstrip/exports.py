"""Export the measured camera columns and the original evidence."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .dispersion import png


def _band_label(split, x):
    if not split or split.get("status") != "resolved":
        return ""
    if x <= split["split_x"]:
        return "left"
    return "right"


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("x_px", "wavelength_nm", "luma", "r_mean", "g_mean", "b_mean", "window_band"))
    reading = result["reading"]
    split = result.get("split")
    for p in result["profile"]:
        if reading and reading["left"] <= p["x"] <= reading["right"]:
            band = _band_label(split, p["x"]) or "window"
        else:
            band = ""
        writer.writerow((p["x"], f'{p["wavelength_nm"]:.4f}', f'{p["brightness"]:.3f}',
                         *(f'{channel:.3f}' for channel in p["rgb"]), band))
    return buffer.getvalue().encode()


def _nm_text(value):
    return "outside calibration" if value is None else f'{value:.2f} nm'


def _unresolved_note(reason):
    if "不足两个" in reason:
        return "Fewer than two peaks with three consecutive above-floor columns; combined read retained."
    if "不足四列" in reason:
        return "The two peak centres are fewer than four source columns apart; not separable."
    if "三个或更多" in reason:
        return "Three or more distinct supported lines appear; no unique two-line boundary."
    if "实拍低谷" in reason:
        return ("No genuine valley between the peaks (valley and both neighbours below the weaker "
                "peak, with three support columns kept per line); combined read retained.")
    return "Not enough double-line evidence; combined read retained."


def _wrap(draw, text, limit=96):
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate) <= limit:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def proof(image, card, result):
    split = result.get("split")
    resolved = bool(split and split.get("status") == "resolved")
    footer_extra = 0
    if result["reading"] and not resolved and split and split.get("status") == "unresolved":
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        footer_extra = 18 * (len(_wrap(probe, _unresolved_note(split["reason"]))) - 1)
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(414, width), height + 118 + footer_extra), "#161e2d")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    cfg = card["settings"]
    draw.rectangle((cfg["left"], cfg["top"], cfg["right"], cfg["bottom"]), outline="#8bcccc", width=2)
    if r := result["reading"]:
        draw.rectangle((r["left"], cfg["top"], r["right"], cfg["bottom"]), outline="#e6a56f", width=2)
        detail = f'Window x {r["left"]} to {r["right"]} / combined signal {r["signal"]:.2f}'
    else:
        detail = "The reading window misses the sampled strip"
    lines = [detail]
    if r and resolved:
        split_x = split["split_x"]
        left, right = split["left_band"], split["right_band"]
        draw.line((split_x, cfg["top"] - 6, split_x, cfg["bottom"] + 6), fill="#ff6f91", width=2)
        draw.text((max(2, split_x - 18), cfg["top"] - 20), f'split x{split_x}', fill="#ff9db4")
        for band, color, anchor in ((left, "#79b9fd", "ra"), (right, "#83e8cc", "la")):
            px = band["peak"]["x"]
            draw.line((px, cfg["top"] - 4, px, cfg["bottom"] + 4), fill=color, width=1)
            label = f'peak x{px}'
            tx = px - draw.textlength(label) - 4 if anchor == "ra" else px + 4
            draw.text((max(2, tx), cfg["bottom"] + 9), label, fill=color)
        lines = [
            detail,
            f'Split at source x {split_x} (boundary column counts as left) / '
            f'left {left["signal"]:.2f} + right {right["signal"]:.2f} = {split["total_signal"]:.2f}',
            f'Left band x {left["left"]}-{left["right"]} / peak x {left["peak"]["x"]} / '
            f'{_nm_text(left["peak"]["wavelength_nm"])} / {left["columns"]} columns',
            f'Right band x {right["left"]}-{right["right"]} / peak x {right["peak"]["x"]} / '
            f'{_nm_text(right["peak"]["wavelength_nm"])} / {right["columns"]} columns',
        ]
    elif r and split and split.get("status") == "unresolved":
        lines.append("Doublet not separable: " + _unresolved_note(split["reason"]))
    center = result["center_nm"]
    value = f'{center:.2f} nm' if center is not None else "outside calibration"
    draw.text((14, height + 12), f'Source x {result["focus"]} / {value}', fill="#e7f0fa")
    text_y = height + 33
    for text in lines:
        for wrapped in _wrap(draw, text, limit=max(380, width - 28)):
            draw.text((14, text_y), wrapped, fill="#edc096")
            text_y += 19
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("spectrum.png", original)
        output.writestr("reading.json", json.dumps({"card": card, "reading": result["reading"],
                                                    "split": result.get("split"),
                                                    "center_nm": result["center_nm"]},
                                                   ensure_ascii=False, indent=2))
        output.writestr("strip-profile.csv", trace_table(result))
        output.writestr("strip-proof.png", proof(image, card, result))
    return buffer.getvalue()
