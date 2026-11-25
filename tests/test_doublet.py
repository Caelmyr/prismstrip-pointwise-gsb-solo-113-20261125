import unittest
from io import BytesIO
from zipfile import ZipFile

from PIL import Image

from prismstrip import dispersion, doublet, exports
from prismstrip.server import create_app


def make_samples(brightness, first_x=100, floor=None):
    return [{"x": first_x + i, "brightness": float(level),
             "wavelength_nm": 500.0 + (first_x + i) * 0.5,
             "rgb": [level, level, level]} for i, level in enumerate(brightness)]


def decide(brightness, floor):
    selected = make_samples(brightness)
    return selected, doublet.resolve(selected, floor)


class DoubletDetectionTests(unittest.TestCase):
    def test_clean_double_line_is_resolved_at_the_valley(self):
        levels = [10, 40, 70, 90, 60, 30, 18, 25, 45, 70, 80, 50, 20, 10]
        selected, decision = decide(levels, floor=15)
        self.assertTrue(decision["resolved"])
        self.assertEqual(decision["split_x"], selected[6]["x"])
        self.assertEqual([p["x"] for p in decision["peaks"]],
                         [selected[3]["x"], selected[10]["x"]])
        reading = doublet.split_reading(selected, 15, decision)
        self.assertEqual(reading["status"], "resolved")
        self.assertEqual(reading["left_band"]["right"], reading["split_x"])
        self.assertEqual(reading["right_band"]["left"], reading["split_x"] + 1)
        self.assertEqual(reading["left_band"]["columns"] + reading["right_band"]["columns"],
                         len(selected))

    def test_signals_conserve_without_inserting_or_repeating_columns(self):
        levels = [10, 40, 70, 90, 60, 30, 18, 25, 45, 70, 80, 50, 20, 10]
        floor = 15
        selected, decision = decide(levels, floor)
        reading = doublet.split_reading(selected, floor, decision)
        corrected = [max(0.0, v - floor) for v in levels]
        self.assertAlmostEqual(reading["left_band"]["signal"] + reading["right_band"]["signal"],
                               reading["total_signal"], delta=1e-9)
        self.assertAlmostEqual(reading["total_signal"], sum(corrected), delta=1e-9)
        left_xs = [s["x"] for s in selected[:decision["split_index"] + 1]]
        right_xs = [s["x"] for s in selected[decision["split_index"] + 1:]]
        self.assertEqual(left_xs + right_xs, [s["x"] for s in selected])
        self.assertEqual(len(set(left_xs) & set(right_xs)), 0)
        self.assertEqual(reading["peak_wavelength_nm"],
                         [selected[3]["wavelength_nm"], selected[10]["wavelength_nm"]])

    def test_peaks_closer_than_four_columns_are_not_split(self):
        # Peaks at index 4 and 7: only three columns apart.
        levels = [10, 20, 45, 80, 90, 55, 30, 85, 60, 30, 12]
        _, decision = decide(levels, floor=15)
        self.assertFalse(decision["resolved"])
        self.assertIn("四列", decision["reason"])

    def test_peak_without_three_above_floor_columns_is_not_a_line(self):
        # Second "peak" at index 9 has only one above-floor column beside it.
        levels = [10, 30, 60, 85, 70, 40, 20, 16, 14, 55, 13, 12, 10]
        _, decision = decide(levels, floor=15)
        self.assertFalse(decision["resolved"])
        self.assertIn("连续三列", decision["reason"])
        self.assertEqual(len(decision["supported_peaks"]), 1)

    def test_isolated_single_column_dip_is_rejected_as_boundary(self):
        # Peaks at 3 (95, weak) and 13 (88). The notch at index 8 is one isolated
        # downward spike inside an otherwise strictly rising flank: itself a local
        # minimum, but its left neighbour (90) is above the weaker peak, so the
        # three-column rule rejects it. The real valley at index 5 has all three
        # columns dimmer than 88 and keeps three support columns on each side.
        levels = [10, 40, 70, 95, 70, 40, 50, 60, 35, 90, 80, 85, 88, 60, 25, 10]
        selected, decision = decide(levels, floor=15)
        self.assertTrue(decision["resolved"])
        self.assertEqual(decision["split_x"], selected[5]["x"])
        rejected_xs = [item["x"] for item in decision["rejected_valleys"]]
        self.assertIn(selected[8]["x"], rejected_xs)

    def test_valley_must_keep_three_support_columns_on_both_sides(self):
        # Valley at index 6 (and both neighbours) is below the weaker peak (70),
        # but cutting there leaves the left peak (index 3) only two above-floor
        # columns on its side (4,5), so the split is rejected on support length.
        levels = [10, 20, 14, 90, 60, 50, 30, 55, 60, 70, 60, 40, 25, 10]
        assert max(levels[5], levels[7]) < 70
        _, decision = decide(levels, floor=15)
        self.assertFalse(decision["resolved"])
        self.assertIn("实拍低谷", decision["reason"])
        self.assertTrue(any("连续三列" in item["reason"]
                            for item in decision.get("rejected_valleys", [])))

    def test_three_distinct_supported_lines_have_no_unique_boundary(self):
        # Three separate above-floor runs, each with its own peak.
        levels = [10, 40, 80, 60, 14, 10, 40, 55, 35, 14, 10,
                  45, 75, 40, 12]
        _, decision = decide(levels, floor=15)
        self.assertFalse(decision["resolved"])
        self.assertIn("三个或更多", decision["reason"])

    def test_wiggles_inside_one_merged_run_do_not_block_the_split(self):
        # Extra local maxima inside the same above-floor envelope are ignored.
        levels = [10, 20, 45, 60, 70, 55, 40, 30, 50, 45, 30, 50, 70, 95, 60, 30, 10]
        selected, decision = decide(levels, floor=15)
        self.assertTrue(decision["resolved"])
        self.assertEqual(decision["peak_indices"], [4, 13])

    def test_deepest_eligible_valley_wins_and_weak_side_is_kept(self):
        # Peaks at 3 (80, weak) and 13 (95). Two eligible valleys at 6 and 9;
        # the valley at 9 is deeper, so it is chosen.
        levels = [10, 40, 60, 80, 60, 45, 40, 50, 45, 25, 45, 60, 80, 95, 50, 20, 10]
        selected, decision = decide(levels, floor=15)
        self.assertTrue(decision["resolved"])
        self.assertEqual(decision["split_x"], selected[9]["x"])
        self.assertEqual(decision["weak_side"], "left")
        self.assertEqual(len(decision["candidate_valleys"]), 2)

    def test_equal_valley_depth_prefers_the_cut_keeping_more_weak_columns(self):
        # Peaks at 4 (70, weak) and 13 (95). Two equal-depth valleys at 7 and 10,
        # joined by a strictly rising step so no third peak appears. Cutting at 7
        # keeps only three weak-side above-floor columns (4,5,6); cutting at 10
        # keeps six, so index 10 must be chosen.
        levels = [10, 20, 45, 60, 70, 55, 40, 30, 50, 45, 30, 50, 70, 95, 60, 30, 10]
        selected, decision = decide(levels, floor=15)
        self.assertTrue(decision["resolved"])
        self.assertEqual(decision["split_x"], selected[10]["x"])
        depths = {item["x"]: item["valley"] for item in decision["candidate_valleys"]}
        self.assertEqual(depths[selected[7]["x"]], depths[selected[10]["x"]])

    def test_columns_at_or_below_floor_count_as_zero_signal(self):
        levels = [10, 40, 70, 90, 60, 30, 15, 25, 45, 70, 80, 50, 20, 10]
        selected, decision = decide(levels, floor=15)
        reading = doublet.split_reading(selected, 15, decision)
        # Valley column equals the floor: corrected value is zero, still assigned left.
        self.assertIn(selected[6]["x"],
                      range(reading["left_band"]["left"], reading["left_band"]["right"] + 1))
        self.assertAlmostEqual(reading["left_band"]["signal"],
                               sum(max(0.0, v - 15) for v in levels[:7]), delta=1e-9)

    def test_window_membership(self):
        levels = [10, 40, 70, 90, 60, 30, 18, 25, 45, 70, 80, 50, 20, 10]
        selected, decision = decide(levels, floor=15)
        split = doublet.split_reading(selected, 15, decision)
        reading = {"left": selected[0]["x"], "right": selected[-1]["x"]}
        split_x = split["split_x"]
        self.assertEqual(doublet.window_membership(split_x, reading, split), "left")
        self.assertEqual(doublet.window_membership(split_x + 1, reading, split), "right")
        self.assertIsNone(doublet.window_membership(reading["left"] - 1, reading, split))
        self.assertEqual(doublet.window_membership(split_x, reading, None), "window")


class ReportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.size, self.png_bytes, self.cfg = dispersion.practice()
        self.image = Image.open(BytesIO(self.png_bytes)).convert("RGB")
        import numpy as np
        self.array = np.asarray(self.image)

    def test_practice_double_line_split(self):
        result = dispersion.report(self.array, self.cfg)
        split = result["split"]
        self.assertEqual(split["status"], "resolved")
        self.assertEqual(split["peak_x"], [364, 374])
        self.assertEqual(split["split_x"], 370)
        self.assertEqual(split["left_band"]["peak"]["wavelength_nm"], 546.0)
        self.assertAlmostEqual(split["right_band"]["peak"]["wavelength_nm"], 552.6667, places=3)
        self.assertAlmostEqual(
            split["left_band"]["signal"] + split["right_band"]["signal"],
            result["reading"]["signal"], delta=1e-9)
        self.assertEqual(split["left_band"]["columns"] + split["right_band"]["columns"],
                         result["reading"]["columns"])

    def test_narrow_window_falls_back_to_combined_read(self):
        result = dispersion.report(self.array, {**self.cfg, "half_width": 3})
        self.assertEqual(result["split"]["status"], "unresolved")
        self.assertIsNotNone(result["reading"])
        self.assertTrue(result["split"]["reason"])

    def test_single_line_window_is_unresolved(self):
        result = dispersion.report(self.array, {**self.cfg, "focus": 233, "half_width": 6})
        self.assertEqual(result["split"]["status"], "unresolved")

    def test_proof_csv_and_package(self):
        result = dispersion.report(self.array, self.cfg)
        card = {"id": "a" * 32, "revision": 1, "name": "练习", "settings": self.cfg}
        proof_bytes = exports.proof(self.array, card, result)
        Image.open(BytesIO(proof_bytes)).verify()
        csv_text = exports.trace_table(result).decode()
        rows = list(line for line in csv_text.splitlines()[1:] if line.rsplit(",", 1)[-1] in ("left", "right"))
        self.assertTrue(rows)
        window_rows = [line for line in csv_text.splitlines()[1:]
                       if line.rsplit(",", 1)[-1] in ("left", "right", "window")]
        self.assertEqual(len(window_rows), result["reading"]["columns"])
        left_count = sum(1 for line in window_rows if line.endswith("left"))
        right_count = sum(1 for line in window_rows if line.endswith("right"))
        self.assertEqual(left_count, result["split"]["left_band"]["columns"])
        self.assertEqual(right_count, result["split"]["right_band"]["columns"])
        package = exports.package(card, self.png_bytes, self.array, result)
        with ZipFile(BytesIO(package)) as archive:
            self.assertEqual(set(archive.namelist()),
                             {"spectrum.png", "reading.json", "strip-profile.csv", "strip-proof.png"})
            import json
            payload = json.loads(archive.read("reading.json"))
            self.assertEqual(payload["split"]["status"], "resolved")


class ServerEndpointTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        created = self.client.post("/prismstrip/practice")
        self.assertEqual(created.status_code, 201)
        self.card = created.get_json()
        self.key = self.card["id"]

    def test_measurement_exposes_split(self):
        response = self.client.get(f"/prismstrip/cards/{self.key}/measurement")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("split", body)
        self.assertEqual(body["split"]["status"], "resolved")
        self.assertIn("reading", body)

    def test_probe_reports_band_membership(self):
        split_x = self.client.get(f"/prismstrip/cards/{self.key}/measurement").get_json()["split"]["split_x"]
        left = self.client.get(f"/prismstrip/cards/{self.key}/probe?x={split_x}&y=215").get_json()
        right = self.client.get(f"/prismstrip/cards/{self.key}/probe?x={split_x + 1}&y=215").get_json()
        outside = self.client.get(f"/prismstrip/cards/{self.key}/probe?x=10&y=215").get_json()
        self.assertEqual(left["window_band"], "left")
        self.assertEqual(right["window_band"], "right")
        self.assertIsNone(outside["window_band"])

    def test_exports_download(self):
        for tail, mime in (("export/proof.png", "image/png"),
                           ("export/profile.csv", "text/csv"),
                           ("export/observation.zip", "application/zip")):
            response = self.client.get(f"/prismstrip/cards/{self.key}/{tail}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, mime)
            self.assertGreater(len(response.data), 200)


if __name__ == "__main__":
    unittest.main()
