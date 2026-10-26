"""tools/design_colour.py, against reference values."""

import unittest

import design_colour


class ChannelsTest(unittest.TestCase):
    def test_reads_both_lengths_in_either_case(self):
        self.assertEqual(design_colour.channels("#12abEF"), (0x12, 0xAB, 0xEF))
        self.assertEqual(design_colour.channels("#fA0"), (0xFF, 0xAA, 0x00))

    def test_refuses_anything_but_an_opaque_hex(self):
        for value in ("#12345678", "#1234", "123456", "#12345G", "#12345", "", "red"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "not an opaque"),
            ):
                design_colour.channels(value)


class ContrastTest(unittest.TestCase):
    def test_linear_on_both_sides_of_the_knee(self):
        self.assertAlmostEqual(design_colour.linear(10), 10 / 255 / 12.92)
        self.assertAlmostEqual(design_colour.linear(255), 1.0)

    def test_luminance_of_the_extremes(self):
        self.assertEqual(design_colour.luminance("#000"), 0)
        self.assertAlmostEqual(design_colour.luminance("#FFFFFF"), 1)

    def test_black_on_white_is_21_and_a_colour_on_itself_is_1(self):
        self.assertAlmostEqual(design_colour.contrast("#000000", "#FFFFFF"), 21)
        self.assertAlmostEqual(design_colour.contrast("#6B6557", "#6B6557"), 1)

    def test_contrast_is_symmetric(self):
        self.assertEqual(
            design_colour.contrast("#6B6557", "#F5F1E8"),
            design_colour.contrast("#F5F1E8", "#6B6557"),
        )

    def test_a_known_pair(self):
        # #767676 on white is the conventional greyest text passing AA, at 4.54:1.
        self.assertAlmostEqual(
            design_colour.contrast("#767676", "#FFFFFF"), 4.54, places=2
        )


class CompositeTest(unittest.TestCase):
    def test_mixes_in_srgb(self):
        self.assertEqual(design_colour.composite("#FFFFFF", 50, "#000000"), "#808080")
        self.assertEqual(design_colour.composite("#123456", 100, "#000000"), "#123456")
        self.assertEqual(design_colour.composite("#123456", 0, "#ABCDEF"), "#ABCDEF")
        self.assertEqual(design_colour.composite("#fff", 25, "#000"), "#404040")

    def test_refuses_an_opacity_out_of_range(self):
        for percent in (-1, 100.5):
            with (
                self.subTest(percent=percent),
                self.assertRaisesRegex(ValueError, "not an opacity"),
            ):
                design_colour.composite("#FFFFFF", percent, "#000000")


class LabTest(unittest.TestCase):
    def assertLab(self, hex_colour, expected):
        for got, want in zip(design_colour.lab(hex_colour), expected, strict=True):
            self.assertAlmostEqual(got, want, places=2)

    def test_reference_values(self):
        self.assertLab("#FFFFFF", (100, 0, 0))
        self.assertLab("#000000", (0, 0, 0))
        self.assertLab("#FF0000", (53.24, 80.09, 67.20))
        self.assertLab("#0000FF", (32.30, 79.19, -107.86))

    def test_lightness_chroma_and_hue(self):
        self.assertAlmostEqual(design_colour.lightness("#FF0000"), 53.24, places=2)
        self.assertAlmostEqual(design_colour.chroma("#FF0000"), 104.55, places=2)
        self.assertAlmostEqual(design_colour.hue("#FF0000"), 40.00, places=2)
        # A negative b* wraps to the far side of the circle.
        self.assertAlmostEqual(design_colour.hue("#0000FF"), 306.28, places=2)

    def test_delta_e(self):
        self.assertAlmostEqual(
            design_colour.delta_e("#000000", "#FFFFFF"), 100, places=4
        )
        self.assertEqual(design_colour.delta_e("#ABC", "#AABBCC"), 0)
        self.assertEqual(
            design_colour.delta_e("#FF0000", "#0000FF"),
            design_colour.delta_e("#0000FF", "#FF0000"),
        )


class HueTest(unittest.TestCase):
    def test_hue_gap_takes_the_shorter_way(self):
        self.assertEqual(design_colour.hue_gap(350, 10), 20)
        self.assertEqual(design_colour.hue_gap(10, 200), 170)
        self.assertEqual(design_colour.hue_gap(0, 720), 0)

    def test_hue_spread_is_the_widest_gap(self):
        red, yellow, blue = "#FF0000", "#FFFF00", "#0000FF"
        self.assertAlmostEqual(
            design_colour.hue_spread([red, yellow, blue]),
            max(
                design_colour.hue_gap(design_colour.hue(a), design_colour.hue(b))
                for a, b in ((red, yellow), (red, blue), (yellow, blue))
            ),
        )
        with self.assertRaises(ValueError):
            design_colour.hue_spread([red])

    def test_hue_between_follows_the_shorter_arc(self):
        self.assertTrue(design_colour.hue_between(0, 350, 10))
        self.assertFalse(design_colour.hue_between(180, 350, 10))
        self.assertTrue(design_colour.hue_between(20, 10, 30))
