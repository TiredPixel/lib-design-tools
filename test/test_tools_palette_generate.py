"""tools/palette-generate, on fabricated input in temporary directories."""

import json
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

import tools_loader


def run(*args, cwd):
    return subprocess.run(
        [str(tools_loader.TOOLS / "palette-generate"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class Workspace(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path


class PaletteGenerateTest(Workspace):
    YAML = """
        meta:
          note: prose stays data
        text_ink:
          dark: "#101010"
        steps:
          10: "#C08020"
          5: "#E0C040"
        pairs:
          warm: ["#E0B050", "#B08020"]
        nested:
          - ["#111111", "#222"]
        Mixed_Case:
          A_B: "#AABBCCDD"
        not_colour: red
    """

    def generate(self, *extra, prefix="x"):
        return run(
            "p.yml",
            "--prefix",
            prefix,
            "--json",
            "p.json",
            "--css",
            "p.css",
            *extra,
            cwd=self.root,
        )

    def test_writes_css_by_the_naming_rule_in_yaml_order(self):
        self.write("p.yml", self.YAML)
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        css = (self.root / "p.css").read_text(encoding="utf-8").splitlines()
        self.assertEqual(css[0], '@charset "UTF-8";')
        properties = [line.strip() for line in css if line.startswith("  --")]
        self.assertEqual(
            properties,
            [
                "--x-text-ink-dark: #101010;",
                "--x-steps-10: #C08020;",
                "--x-steps-5: #E0C040;",
                "--x-pairs-warm-1: #E0B050;",
                "--x-pairs-warm-2: #B08020;",
                "--x-nested-1-1: #111111;",
                "--x-nested-1-2: #222;",
                "--x-mixed-case-a-b: #AABBCCDD;",
            ],
        )

    def test_writes_json_in_yaml_order_with_comment_first(self):
        self.write("p.yml", self.YAML)
        self.assertEqual(self.generate().returncode, 0)
        document = json.loads((self.root / "p.json").read_text(encoding="utf-8"))
        self.assertEqual(
            list(document),
            [
                "$comment",
                "meta",
                "text_ink",
                "steps",
                "pairs",
                "nested",
                "Mixed_Case",
                "not_colour",
            ],
        )
        self.assertIn("p.yml", document["$comment"])
        self.assertEqual(list(document["steps"]), ["10", "5"])

    def test_rerun_changes_nothing(self):
        self.write("p.yml", self.YAML)
        self.generate()
        first = [(self.root / f).read_bytes() for f in ("p.json", "p.css")]
        self.generate()
        second = [(self.root / f).read_bytes() for f in ("p.json", "p.css")]
        self.assertEqual(first, second)

    def test_check_passes_on_current_output(self):
        self.write("p.yml", self.YAML)
        self.generate()
        result = self.generate("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_fails_on_stale_output_and_writes_nothing(self):
        self.write("p.yml", self.YAML)
        self.generate()
        self.write("p.yml", self.YAML.replace("#101010", "#000000"))
        before = (self.root / "p.css").read_text(encoding="utf-8")
        result = self.generate("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale", result.stderr)
        self.assertIn("#000000", result.stdout)
        self.assertEqual((self.root / "p.css").read_text(encoding="utf-8"), before)

    def test_fails_on_a_name_collision(self):
        self.write("p.yml", 'a_b: "#000000"\na-b: "#FFFFFF"\n')
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("--x-a-b", result.stderr)

    def test_fails_on_a_key_css_cannot_name(self):
        for key in ("light blue", "a.b", "x/y", "a:b", "a+b"):
            with self.subTest(key=key):
                self.write("p.yml", f'ok: "#000000"\n"{key}": "#FFFFFF"\n')
                result = self.generate()
                self.assertEqual(result.returncode, 1)
                self.assertIn("names CSS cannot read", result.stderr)
                self.assertIn(f"--x-{key}", result.stderr)
                self.assertNotIn("--x-ok", result.stderr)
                self.assertFalse((self.root / "p.css").exists())

    def test_keeps_a_non_ascii_key(self):
        self.write("p.yml", 'grün: "#00FF00"\n')
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "  --x-grün: #00FF00;", (self.root / "p.css").read_text(encoding="utf-8")
        )

    def test_fails_on_a_yaml_without_colour(self):
        self.write("p.yml", "a: plain\n")
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds no colour", result.stderr)

    def test_expands_aliases_and_merge_keys_in_both_outputs(self):
        self.write(
            "p.yml",
            """
            base: &b
              ink: "#101010"
              paper: "#FFFFFF"
            merged:
              <<: *b
              paper: "#EEEEEE"
            link: *b
            """,
        )
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads((self.root / "p.json").read_text(encoding="utf-8"))
        self.assertEqual(document["merged"], {"ink": "#101010", "paper": "#EEEEEE"})
        self.assertEqual(document["link"], {"ink": "#101010", "paper": "#FFFFFF"})
        css = (self.root / "p.css").read_text(encoding="utf-8")
        for line in (
            "  --x-merged-ink: #101010;",
            "  --x-merged-paper: #EEEEEE;",
            "  --x-link-ink: #101010;",
            "  --x-link-paper: #FFFFFF;",
        ):
            with self.subTest(line=line):
                self.assertIn(line, css)
        self.assertNotIn("--x-merged-paper: #FFFFFF;", css)

    def test_fails_on_a_string_starting_with_hash_that_is_no_colour(self):
        for value in ("#12345", "#abc ", "#XYZ", "#1234567"):
            with self.subTest(value=value):
                self.write("p.yml", f'ok: "#000000"\ngroup:\n  - "{value}"\n')
                result = self.generate()
                self.assertEqual(result.returncode, 1)
                self.assertIn("not a #RGB", result.stderr)
                self.assertIn(f'  group.1: "{value}"', result.stderr)
                self.assertNotIn("  ok", result.stderr)
                self.assertFalse((self.root / "p.css").exists())

    def test_fails_on_a_leaf_with_no_value(self):
        for line in ("accent: #abc", "accent: ~", "accent:"):
            with self.subTest(line=line):
                self.write("p.yml", f'ok: "#000000"\nset:\n  {line}\n')
                result = self.generate()
                self.assertEqual(result.returncode, 1)
                self.assertIn("leaves with no value; quote a colour", result.stderr)
                self.assertIn("  set.accent", result.stderr)
                self.assertFalse((self.root / "p.css").exists())

    def test_fails_on_a_missing_yaml(self):
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_refuses_a_prefix_that_is_not_a_css_name(self):
        self.write("p.yml", self.YAML)
        result = self.generate(prefix="X y")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a CSS name", result.stderr)

    def test_usage_errors(self):
        for args in (
            [],
            ["p.yml", "--prefix", "x", "--json", "p.json"],
            ["p.yml", "--prefix"],
            ["p.yml", "q.yml", "--prefix", "x", "--json", "j", "--css", "c"],
            ["p.yml", "--bogus"],
        ):
            with self.subTest(args=args):
                result = run(*args, cwd=self.root)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)

    def test_help_prints_usage_and_succeeds(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = run("p.yml", flag, cwd=self.root)
                self.assertEqual(result.returncode, 0)
                self.assertTrue(result.stdout.startswith("usage: palette-generate"))
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
