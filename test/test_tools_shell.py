"""tools/palette-generate, tools/charset-check and tools/lfs-check, on fabricated input
in temporary directories."""

import json
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

import tools_loader


def run(tool, *args, cwd):
    return subprocess.run(
        [str(tools_loader.TOOLS / tool), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class Workspace(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = pathlib.Path(directory.name)

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())
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
        not_colour: "#XYZ"
    """

    def generate(self, *extra, prefix="x"):
        return run(
            "palette-generate",
            "p.yml",
            "--prefix",
            prefix,
            "--json",
            "p.json",
            "--css",
            "p.css",
            *extra,
            cwd=self.dir,
        )

    def test_writes_css_by_the_naming_rule_in_yaml_order(self):
        self.write("p.yml", self.YAML)
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        css = (self.dir / "p.css").read_text().splitlines()
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
        document = json.loads((self.dir / "p.json").read_text())
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
        first = [(self.dir / f).read_bytes() for f in ("p.json", "p.css")]
        self.generate()
        second = [(self.dir / f).read_bytes() for f in ("p.json", "p.css")]
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
        before = (self.dir / "p.css").read_text()
        result = self.generate("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale", result.stderr)
        self.assertIn("#000000", result.stdout)
        self.assertEqual((self.dir / "p.css").read_text(), before)

    def test_fails_on_a_name_collision(self):
        self.write("p.yml", 'a_b: "#000000"\na-b: "#FFFFFF"\n')
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("--x-a-b", result.stderr)

    def test_fails_on_a_yaml_without_colour(self):
        self.write("p.yml", "a: plain\n")
        result = self.generate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds no colour", result.stderr)

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
                result = run("palette-generate", *args, cwd=self.dir)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)


class CharsetCheckTest(Workspace):
    def test_passes_a_declared_stylesheet(self):
        self.write("a.css", '@charset "UTF-8";\nbody {}\n')
        result = run("charset-check", "a.css", cwd=self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked 1", result.stderr)

    def test_fails_each_undeclared_stylesheet(self):
        self.write("a.css", '@charset "UTF-8";\n')
        self.write("b.css", "body {}\n")
        self.write("c.css", "@charset 'UTF-8';\n")
        result = run("charset-check", "a.css", "b.css", "c.css", cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("a.css", result.stderr.split("checked")[0])
        self.assertIn("b.css:1", result.stderr)
        self.assertIn("c.css:1", result.stderr)

    def test_usage_without_files(self):
        result = run("charset-check", cwd=self.dir)
        self.assertEqual(result.returncode, 2)


class LfsCheckTest(Workspace):
    ATTRIBUTES = """
        *.png  filter=lfs diff=lfs merge=lfs -text
        *.ttf  filter=lfs diff=lfs merge=lfs -text
        *.css  linguist-generated
    """
    POINTER = "version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n"

    def test_passes_real_files(self):
        self.write(".gitattributes", self.ATTRIBUTES)
        (self.dir / "a").mkdir()
        (self.dir / "a" / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.write("a/tiny.ttf", "v")
        self.write("a/y.css", self.POINTER)
        result = run("lfs-check", "--attributes", ".gitattributes", "a", cwd=self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked 2 LFS file(s)", result.stderr)

    def test_fails_each_pointer(self):
        self.write(".gitattributes", self.ATTRIBUTES)
        self.write("a/x.png", self.POINTER)
        self.write("b/deep/y.ttf", self.POINTER)
        result = run(
            "lfs-check", "--attributes", ".gitattributes", "a", "b", cwd=self.dir
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("a/x.png", result.stderr)
        self.assertIn("b/deep/y.ttf", result.stderr)

    def test_skips_git_directories(self):
        self.write(".gitattributes", self.ATTRIBUTES)
        self.write("a/.git/lfs/x.png", self.POINTER)
        result = run("lfs-check", "--attributes", ".gitattributes", "a", cwd=self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fails_without_lfs_patterns(self):
        self.write(".gitattributes", "*.css linguist-generated\n")
        result = run("lfs-check", "--attributes", ".gitattributes", ".", cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("names no filter=lfs pattern", result.stderr)

    def test_refuses_a_pattern_with_a_slash(self):
        self.write(".gitattributes", "img/*.png filter=lfs -text\n")
        result = run("lfs-check", "--attributes", ".gitattributes", ".", cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds a slash", result.stderr)

    def test_fails_on_a_missing_attributes_file(self):
        result = run("lfs-check", "--attributes", "nope", ".", cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such file", result.stderr)

    def test_usage_errors(self):
        for args in ([], ["--attributes", "x"], ["x", "y", "z"]):
            with self.subTest(args=args):
                result = run("lfs-check", *args, cwd=self.dir)
                self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
