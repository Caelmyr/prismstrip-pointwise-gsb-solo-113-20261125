"""Split two close emission lines inside one reading window.

The window columns are measured from the real camera strip.  A split is only
accepted when both lines have direct photographic evidence: their peak
columns are at least four source columns apart, each line owns at least three
consecutive columns measured above the background floor, and the boundary is
a genuine valley between them (the valley and both adjacent columns are
dimmer than the weaker peak), never an isolated single-column dip.

Small wiggles inside the same merged above-floor run are part of the two-line
envelope, not extra spectral lines: only a peak sitting in its own separate
above-floor run counts as a third line and blocks the split.
"""

import math

MIN_PEAK_GAP = 4
MIN_LINE_COLUMNS = 3


def _support_runs(mask):
    """Return half-open (start, stop) index ranges of consecutive True flags."""
    runs = []
    start = None
    for index, above in enumerate(mask):
        if above and start is None:
            start = index
        elif not above and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _peak_indices(values):
    """Local maxima on the raw profile; plateaus resolve to their right end."""
    peaks = []
    for index in range(len(values)):
        left = values[index - 1] if index else -math.inf
        right = values[index + 1] if index + 1 < len(values) else -math.inf
        if values[index] >= left and values[index] > right:
            peaks.append(index)
    return peaks


def _valley_indices(values, first, last):
    """Local minima strictly between two peak indices."""
    valleys = []
    for index in range(first + 1, last):
        left = values[index - 1]
        right = values[index + 1]
        if values[index] <= left and values[index] < right:
            valleys.append(index)
    return valleys


def _run_length(mask, peak_index):
    for start, stop in _support_runs(mask):
        if start <= peak_index < stop:
            return stop - start
    return 0


def _support_length(mask, peak_index, split_index, side):
    """Above-floor columns kept on one side, counting the run through its peak."""
    if side == "left":
        count = 0
        for index in range(peak_index, -1, -1):
            if index > split_index or not mask[index]:
                break
            count += 1
    else:
        count = 0
        for index in range(peak_index, len(mask)):
            if index <= split_index or not mask[index]:
                break
            count += 1
    return count


def resolve(selected, floor):
    """Decide whether the window holds two separable lines.

    ``selected`` keeps the existing profile samples in source-X order.
    Returns an audit dict with ``resolved`` plus the evidence used.
    """
    values = [sample["brightness"] for sample in selected]
    mask = [value > floor for value in values]
    runs = _support_runs(mask)

    def run_number(peak_index):
        for number, (start, stop) in enumerate(runs):
            if start <= peak_index < stop:
                return number
        return None

    supported = [index for index in _peak_indices(values)
                 if _run_length(mask, index) >= MIN_LINE_COLUMNS]
    line_runs = {run_number(index) for index in supported}

    def describe(index):
        return {"index": index, "x": selected[index]["x"],
                "brightness": values[index],
                "support_columns": _run_length(mask, index),
                "run_index": run_number(index)}

    if len(line_runs) >= 3:
        return {"resolved": False,
                "reason": "窗口里有三个或更多各自带连续背景以上亮度的独立亮线，没有唯一的双线分界，仍按合读处理。",
                "supported_peaks": [describe(index) for index in supported]}
    if len(supported) < 2:
        return {"resolved": False,
                "reason": "窗口里不足两个各自有连续三列背景以上亮度的峰，仍按合读处理。",
                "supported_peaks": [describe(index) for index in supported]}

    # The two brightest supported maxima are the two lines; any other local
    # maximum shares their merged above-floor run and is just envelope noise.
    first, last = sorted(sorted(supported, key=lambda index: values[index],
                                reverse=True)[:2])
    if last - first < MIN_PEAK_GAP:
        return {"resolved": False,
                "reason": "两个峰中心只隔 %d 列，不足四列，还分不开。" % (last - first),
                "supported_peaks": [describe(index) for index in supported]}

    weak_level = min(values[first], values[last])
    weak_side = "left" if values[first] <= values[last] else "right"
    candidates = []
    rejected = []
    for valley in _valley_indices(values, first, last):
        record = {"index": valley, "x": selected[valley]["x"],
                  "valley": values[valley], "left_neighbor": values[valley - 1],
                  "right_neighbor": values[valley + 1]}
        if not all(values[offset] < weak_level for offset in (valley - 1, valley, valley + 1)):
            record["reason"] = "低谷或其相邻列没有全部低于较弱峰。"
            rejected.append(record)
            continue
        left_length = _support_length(mask, first, valley, "left")
        right_length = _support_length(mask, last, valley, "right")
        record.update(left_columns=left_length, right_columns=right_length)
        if left_length < MIN_LINE_COLUMNS or right_length < MIN_LINE_COLUMNS:
            record["reason"] = "在此切分会使某条线失去连续三列背景以上实测亮度。"
            rejected.append(record)
            continue
        # Ranking counts every above-floor column the weak line keeps visible.
        weak_kept = (sum(mask[valley + 1:]) if weak_side == "right"
                     else sum(mask[:valley + 1]))
        candidates.append((record, weak_kept))

    if not candidates:
        return {"resolved": False,
                "reason": "两峰之间找不到实拍低谷（低谷与左右相邻列都低于较弱峰，且两线各留连续三列），仍按合读处理。",
                "supported_peaks": [describe(index) for index in supported],
                "rejected_valleys": rejected}

    # Prefer the deepest valley; ties keep more of the weak line's visible side.
    best_record, _ = min(candidates, key=lambda item: (item[0]["valley"], -item[1], item[0]["index"]))
    return {"resolved": True, "split_index": best_record["index"], "split_x": best_record["x"],
            "peak_indices": [first, last], "weak_side": weak_side,
            "peaks": [describe(first), describe(last)],
            "chosen_valley": best_record, "candidate_valleys": [record for record, _ in candidates],
            "rejected_valleys": rejected}


def split_reading(selected, floor, decision):
    """Build the conserved two-band reading from the chosen valley."""
    corrected = [max(0.0, sample["brightness"] - floor) for sample in selected]
    split_index = decision["split_index"]
    left_corrected = corrected[:split_index + 1]
    right_corrected = corrected[split_index + 1:]
    total = math.fsum(corrected)
    left_signal = math.fsum(left_corrected)
    right_signal = math.fsum(right_corrected)
    if not math.isclose(left_signal + right_signal, total, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError("双线信号合计与原窗口信号不一致")
    first, last = decision["peak_indices"]

    def band(samples, pieces, peak_sample, side_label):
        return {"side": side_label, "left": samples[0]["x"], "right": samples[-1]["x"],
                "columns": len(samples), "signal": float(math.fsum(pieces)),
                "peak": dict(peak_sample)}

    left_samples = selected[:split_index + 1]
    right_samples = selected[split_index + 1:]
    return {
        "status": "resolved", "reason": None,
        "split_x": decision["split_x"], "split_index": split_index,
        "left_band": band(left_samples, left_corrected, selected[first], "left"),
        "right_band": band(right_samples, right_corrected, selected[last], "right"),
        "total_signal": float(total),
        "peak_x": [selected[first]["x"], selected[last]["x"]],
        "peak_wavelength_nm": [selected[first]["wavelength_nm"], selected[last]["wavelength_nm"]],
        "candidate_valleys": decision["candidate_valleys"],
        "rejected_valleys": decision["rejected_valleys"],
    }


def window_membership(x, reading, split):
    """Which side of a resolved split a point-read column belongs to."""
    if reading is None or not reading["left"] <= x <= reading["right"]:
        return None
    if not split or split.get("status") != "resolved":
        return "window"
    return "left" if x <= split["split_x"] else "right"
