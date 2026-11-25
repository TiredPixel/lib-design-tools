"""tools/fonts-upgrade, run against a fabricated GitHub on a fonts directory.

fetch itself runs against a server on the loopback.
"""

import contextlib
import hashlib
import http.server
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
import zipfile

import tools_loader

SCRIPT = tools_loader.TOOLS / "fonts-upgrade"
fonts_upgrade = tools_loader.load("fonts-upgrade")
API = fonts_upgrade.API

SERIF = "example-type/serif-one"
SANS = "example-type/sans-fonts"
MONO = "example-type/sans-mono-fonts"
# The releases the fonts directory holds, and the latest the fabricated GitHub gives.
HELD = {SERIF: "4.004R", SANS: "v1.006", MONO: "v1.006"}
NEW = {SERIF: "4.005R", SANS: "v1.006", MONO: "v1.100"}
FAMILIES = f"""[[family]]
prefix = "SerifOne"
repository = "{SERIF}"
licences-from-repository = true

[family.directories]
".ttf" = "TTF"
".woff2" = "TTF"

[[family]]
prefix = "ExampleSans"
repository = "{SANS}"

[family.directories]
".ttf" = "ttf"
".woff2" = "webfont"

[[family]]
prefix = "ExampleSansMono"
repository = "{MONO}"

[family.directories]
".ttf" = "ttf"
".woff2" = "webfont"
"""


def archive(members):
    """A .zip holding MEMBERS, a path to content each, one ending in / a directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        for path, content in members.items():
            written.writestr(path, content)
    return buffer.getvalue()


def sums(files):
    return "".join(
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files)
    )


class GitHub:
    """Serves each URL in BODIES, refusing any other as fetch does a 404.

    Every URL asked for is recorded with the media type asked.
    """

    def __init__(self):
        self.bodies = {}
        self.asked = []

    def fetch(self, url, accept="application/vnd.github+json"):
        self.asked.append((url, accept))
        if url not in self.bodies:
            raise fonts_upgrade.Refusal(f"{url}: HTTP Error 404: Not Found")
        return self.bodies[url]

    def release(self, repository, tag, assets):
        """ASSETS: a name to (content, digest) each, None where GitHub gives none."""
        listed = []
        for name, (content, digest) in assets.items():
            url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
            self.bodies[url] = content
            listed.append({"browser_download_url": url, "digest": digest, "name": name})
        self.bodies[f"{API}/repos/{repository}/releases/latest"] = json.dumps(
            {"assets": listed, "tag_name": tag}
        ).encode()

    def file(self, repository, tag, name, content):
        """NAME at the repository's root at TAG, TAG as it appears in the URL."""
        self.bodies[file_url(repository, tag, name)] = content

    def commits(self, repository, path, *shas):
        """SHAS as the newest commits touching PATH, PATH as it appears in the URL."""
        self.bodies[commits_url(repository, path)] = json.dumps(
            [{"sha": sha} for sha in shas]
        ).encode()

    def folder_file(self, repository, sha, path, name, content):
        """NAME in PATH at commit SHA, PATH and NAME as they appear in the URL."""
        self.bodies[folder_url(repository, sha, path, name)] = content


def commits_url(repository, path):
    return f"{API}/repos/{repository}/commits?path={path}&per_page=1"


def folder_url(repository, sha, path, name):
    return f"{fonts_upgrade.RAW}/{repository}/{sha}/{path}/{name}"


def file_url(repository, tag, name):
    return f"{fonts_upgrade.RAW}/{repository}/refs/tags/{tag}/{name}"


class Fonts(unittest.TestCase):
    """A fonts directory holding one or two files of each family at the HELD releases.

    The fabricated GitHub's latest releases are NEW's.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.directory = self.root / "fonts"
        self.directory.mkdir()
        self.held = {
            "SerifOne-LICENSE.md": b"OFL, 2021\r\n",
            "SerifOne-Regular.ttf.woff2": b"wOF2serif old",
            "ExampleSans-LICENCE.txt": b"Licence\n",
            "ExampleSans-Regular.ttf": b"\x00\x01\x00\x00sans",
            "ExampleSansMono-Bold.woff2": b"wOF2mono old",
        }
        for name, content in self.held.items():
            (self.directory / name).write_bytes(content)
        (self.directory / "README.md").write_text("# Fonts\n")
        (self.directory / "SHA256SUMS").write_text(sums(self.held))
        (self.directory / "RELEASES").write_text(self.releases(HELD))
        (self.directory / "families.toml").write_text(FAMILIES)

        self.new = {
            "SerifOne-LICENSE.md": b"OFL, 2023\r\n",
            "SerifOne-Regular.ttf.woff2": b"wOF2serif new",
            "ExampleSans-LICENCE.txt": b"Licence\n",
            "ExampleSans-Regular.ttf": b"\x00\x01\x00\x00sans",
            "ExampleSansMono-Bold.woff2": b"wOF2mono new",
        }
        self.github = GitHub()
        mono = archive(
            {
                "example-sans-mono-v1.100/fonts/webfont/": b"",
                "example-sans-mono-v1.100/fonts/webfont/ExampleSansMono-Bold"
                ".woff2": self.new["ExampleSansMono-Bold.woff2"],
                "example-sans-mono-v1.100/fonts/unhinted/ExampleSansMono-Bold"
                ".woff2": b"wOF2mono unhinted",
                "example-sans-mono-v1.100/fonts/LICENCE.txt": b"not at the top",
                "example-sans-mono-v1.100/fonts/ttf/ExampleSansMono-Bold.ttf": (
                    b"\x00\x01\x00\x00mono"
                ),
            }
        )
        self.github.release(
            MONO,
            "v1.100",
            {
                "example-sans-mono-v1.100.zip": (
                    mono,
                    "sha256:" + hashlib.sha256(mono).hexdigest(),
                ),
                "checksums.txt": (b"not an archive", None),
            },
        )
        self.github.release(
            SANS,
            "v1.006",
            {
                "ExampleSans-fonts-1.006.zip": (
                    archive(
                        {
                            "ExampleSans-fonts-1.006/LICENCE.txt": self.new[
                                "ExampleSans-LICENCE.txt"
                            ],
                            "ExampleSans-fonts-1.006/ttf/ExampleSans-Regular.ttf": (
                                self.new["ExampleSans-Regular.ttf"]
                            ),
                        }
                    ),
                    None,
                )
            },
        )
        self.github.release(
            SERIF,
            "4.005R",
            {
                "serif-one-4.005_Desktop.zip": (
                    archive(
                        {
                            "serif-one-4.005_Desktop/LICENSE.md": b"not this one",
                            "serif-one-4.005_Desktop/TTF/SerifOne-Regular.ttf": (
                                b"\x00\x01\x00\x00serif"
                            ),
                        }
                    ),
                    None,
                ),
                "serif-one-4.005_WOFF2.zip": (
                    archive(
                        {
                            "serif-one-4.005_WOFF2/TTF/SerifOne-Regular.ttf"
                            ".woff2": self.new["SerifOne-Regular.ttf.woff2"],
                            "__MACOSX/serif-one-4.005_WOFF2/TTF/SerifOne-Regular"
                            ".ttf.woff2": b"a resource fork",
                        }
                    ),
                    None,
                ),
            },
        )
        self.github.file(SERIF, "4.005R", "LICENSE.md", self.new["SerifOne-LICENSE.md"])

    @staticmethod
    def releases(tags):
        return "".join(
            f"{repository} {tags[repository]}\n" for repository in sorted(tags)
        )

    def run_main(self, *arguments):
        """main's exit status, stdout and stderr, given ARGUMENTS or the directory."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = list(arguments) or [str(self.directory)]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = fonts_upgrade.main(argv, self.github.fetch)
        return status, stdout.getvalue(), stderr.getvalue()

    def contents(self):
        return {
            path.name: path.read_bytes() for path in sorted(self.directory.iterdir())
        }

    def assert_refused(self, message):
        """main fails with MESSAGE in its last line, writing nothing."""
        before = self.contents()
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        last = stderr.splitlines()[-1]
        self.assertTrue(last.startswith("fonts-upgrade: "), last)
        self.assertIn(message, last)
        self.assertEqual(self.contents(), before)


class UpgradeTest(Fonts):
    def test_moves_each_file_to_its_latest_release(self):
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            stdout.splitlines(),
            [f"{MONO} v1.006 -> v1.100", f"{SERIF} 4.004R -> 4.005R"],
        )
        expected = {
            **self.new,
            "README.md": b"# Fonts\n",
            "RELEASES": self.releases(NEW).encode(),
            "SHA256SUMS": sums(self.new).encode(),
            "families.toml": FAMILIES.encode(),
        }
        self.assertEqual(self.contents(), expected)

    def test_names_each_latest_release(self):
        _, _, stderr = self.run_main()
        for repository, tag in NEW.items():
            self.assertIn(
                f"fonts-upgrade: {repository} {tag}: the latest release", stderr
            )

    def test_takes_a_licence_from_the_repository_where_the_table_says_so(self):
        self.run_main()
        self.assertIn(
            (
                f"https://raw.githubusercontent.com/{SERIF}/refs/tags/4.005R/LICENSE.md",
                "application/octet-stream",
            ),
            self.github.asked,
        )

    def test_quotes_the_tag(self):
        self.github.release(
            SERIF,
            "4.005R+1",
            {"a.zip": (archive({"TTF/SerifOne-Regular.ttf.woff2": b"x"}), None)},
        )
        self.github.file(SERIF, "4.005R%2B1", "LICENSE.md", b"OFL\n")
        status, _, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)

    def test_takes_a_licence_from_the_archives_root(self):
        self.github.release(
            SANS,
            "v1.006",
            {
                "a.zip": (
                    archive(
                        {
                            "LICENCE.txt": b"Licence at the root\n",
                            "ttf/ExampleSans-Regular.ttf": b"\x00\x01\x00\x00sans",
                        }
                    ),
                    None,
                )
            },
        )
        self.github.file(SANS, "v1.006", "LICENCE.txt", b"Licence in the repository\n")
        status, _, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            (self.directory / "ExampleSans-LICENCE.txt").read_bytes(),
            b"Licence at the root\n",
        )
        self.assertNotIn(
            file_url(SANS, "v1.006", "LICENCE.txt"),
            [url for url, _ in self.github.asked],
        )

    def test_takes_a_licence_the_release_lacks_from_the_repository(self):
        self.github.release(
            SANS,
            "v1.007",
            {"a.zip": (archive({"ttf/ExampleSans-Regular.ttf": b"sans"}), None)},
        )
        self.github.file(SANS, "v1.007", "LICENCE.txt", b"Licence in the repository\n")
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertIn(f"{SANS} v1.006 -> v1.007", stdout.splitlines())
        self.assertEqual(
            (self.directory / "ExampleSans-LICENCE.txt").read_bytes(),
            b"Licence in the repository\n",
        )
        self.assertIn(
            (file_url(SANS, "v1.007", "LICENCE.txt"), "application/octet-stream"),
            self.github.asked,
        )

    def test_reproduces_the_files_where_no_release_moved(self):
        (self.directory / "RELEASES").write_text(self.releases(NEW))
        for name, content in self.new.items():
            (self.directory / name).write_bytes(content)
        (self.directory / "SHA256SUMS").write_text(sums(self.new))
        before = self.contents()
        status, stdout, stderr = self.run_main()
        self.assertEqual((status, stdout), (0, ""), stderr)
        self.assertEqual(self.contents(), before)

    def test_skips_assets_that_are_not_archives(self):
        self.run_main()
        self.assertNotIn(
            (
                f"https://github.com/{MONO}/releases/download/v1.100/checksums.txt",
                "application/octet-stream",
            ),
            self.github.asked,
        )


class UsageTest(Fonts):
    def test_arguments_other_than_one_directory(self):
        for argv in ([], ["a", "b"], ["--help"]):
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    status = fonts_upgrade.main(argv, self.github.fetch)
                self.assertEqual(status, 2)
                self.assertEqual(stderr.getvalue(), "Usage: fonts-upgrade DIRECTORY\n")
        self.assertEqual(self.github.asked, [])

    def test_run_as_a_program(self):
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            (run.returncode, run.stdout, run.stderr),
            (2, "", "Usage: fonts-upgrade DIRECTORY\n"),
        )


class RefusalTest(Fonts):
    def commit(self, name, text):
        (self.directory / name).write_text(text)

    def test_name_outside_the_directory(self):
        sums = (self.directory / "SHA256SUMS").read_text()
        for name in ("../SerifOne-LICENSE.md", "/tmp/SerifOne-x", "SHA256SUMS"):
            with self.subTest(name=name):
                self.commit("SHA256SUMS", sums + f"{'0' * 64}  {name}\n")
                self.assert_refused(f"SHA256SUMS: {name!r} is not a file of")

    def test_name_that_is_a_symbolic_link(self):
        outside = self.directory.parent / "outside"
        outside.write_text("kept")
        licence = self.directory / "SerifOne-LICENSE.md"
        licence.unlink()
        licence.symlink_to(outside)
        self.assert_refused("SerifOne-LICENSE.md: is a symbolic link")
        self.assertEqual(outside.read_text(), "kept")

    def test_directory_that_is_a_symbolic_link(self):
        link = self.directory.parent / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        before = self.contents()
        status, stdout, stderr = self.run_main(str(link))
        self.assertEqual((status, stdout), (1, ""))
        self.assertEqual(stderr, f"fonts-upgrade: {link}: is a symbolic link\n")
        self.assertEqual(self.contents(), before)

    def test_name_of_no_family(self):
        sums = (self.directory / "SHA256SUMS").read_text()
        self.commit("SHA256SUMS", sums + f"{'0' * 64}  Other-Regular.ttf\n")
        self.assert_refused("Other-Regular.ttf is of no family in families.toml")

    def test_releases_missing_a_publisher(self):
        self.commit("RELEASES", f"{SANS} v1.006\n{SERIF} 4.004R\n")
        self.assert_refused("RELEASES: lists")

    def test_font_missing_from_the_release(self):
        self.github.release(MONO, "v1.100", {"a.zip": (archive({"x": b"x"}), None)})
        self.assert_refused(
            f"{MONO} v1.100: no ExampleSansMono-Bold.woff2 in a webfont/ of its assets"
        )

    def test_font_only_in_another_directory(self):
        self.github.release(
            MONO,
            "v1.100",
            {"a.zip": (archive({"unhinted/ExampleSansMono-Bold.woff2": b"x"}), None)},
        )
        self.assert_refused(
            f"{MONO} v1.100: no ExampleSansMono-Bold.woff2 in a webfont/ of its assets"
        )

    def test_font_at_the_archives_root(self):
        self.github.release(
            MONO,
            "v1.100",
            {"a.zip": (archive({"ExampleSansMono-Bold.woff2": b"x"}), None)},
        )
        self.assert_refused(
            f"{MONO} v1.100: no ExampleSansMono-Bold.woff2 in a webfont/ of its assets"
        )

    def test_licence_missing_from_the_release_and_the_repository(self):
        self.github.release(
            SANS,
            "v1.007",
            {"a.zip": (archive({"ttf/ExampleSans-Regular.ttf": b"x"}), None)},
        )
        self.assert_refused(
            f"{SANS} v1.007: no LICENCE.txt at the top of its assets, and "
            f"{file_url(SANS, 'v1.007', 'LICENCE.txt')}: HTTP Error 404"
        )

    def test_licence_only_below_the_top(self):
        self.github.release(
            SANS,
            "v1.007",
            {
                "a.zip": (
                    archive(
                        {
                            "ExampleSans-fonts-1.007/docs/LICENCE.txt": b"Licence\n",
                            "ttf/ExampleSans-Regular.ttf": b"x",
                        }
                    ),
                    None,
                )
            },
        )
        self.assert_refused(f"{SANS} v1.007: no LICENCE.txt at the top of its assets")

    def test_licence_missing_from_the_repository(self):
        del self.github.bodies[file_url(SERIF, "4.005R", "LICENSE.md")]
        self.assert_refused("HTTP Error 404")

    def test_font_twice_in_the_release(self):
        self.github.release(
            MONO,
            "v1.100",
            {
                "a.zip": (archive({"webfont/ExampleSansMono-Bold.woff2": b"a"}), None),
                "b.zip": (
                    archive({"b/webfont/ExampleSansMono-Bold.woff2": b"b"}),
                    None,
                ),
            },
        )
        self.assert_refused(
            f"{MONO} v1.100: ExampleSansMono-Bold.woff2 more than once in a webfont/"
            " of its assets: a.zip:webfont/ExampleSansMono-Bold.woff2, "
            "b.zip:b/webfont/ExampleSansMono-Bold.woff2"
        )

    def test_licence_twice_in_the_release(self):
        self.github.release(
            SANS,
            "v1.007",
            {
                "a.zip": (
                    archive(
                        {
                            "LICENCE.txt": b"a",
                            "s/LICENCE.txt": b"b",
                            "ttf/ExampleSans-Regular.ttf": b"x",
                        }
                    ),
                    None,
                )
            },
        )
        # Only a licence missing from the release falls back to the repository, so a
        # copy served there must not rescue one the release holds twice.
        self.github.file(SANS, "v1.007", "LICENCE.txt", b"Licence in the repository\n")
        self.assert_refused(
            f"{SANS} v1.007: LICENCE.txt more than once at the top of its assets: "
            "a.zip:LICENCE.txt, a.zip:s/LICENCE.txt"
        )
        asked = [url for url, _ in self.github.asked]
        self.assertNotIn(file_url(SANS, "v1.007", "LICENCE.txt"), asked)

    def test_archive_not_matching_its_digest(self):
        self.github.release(
            MONO, "v1.100", {"a.zip": (archive({}), "sha256:" + "0" * 64)}
        )
        self.assert_refused("a.zip: does not match its digest")

    def test_asset_not_an_archive(self):
        self.github.release(MONO, "v1.100", {"a.zip": (b"not a zip", None)})
        self.assert_refused("a.zip: File is not a zip file")

    def test_release_lookup_failing(self):
        del self.github.bodies[f"{API}/repos/{SANS}/releases/latest"]
        self.assert_refused(f"{API}/repos/{SANS}/releases/latest: HTTP Error 404")


FOLDERS = "example-type/fonts"
SERIF_OLD = "1" * 40
SERIF_NEW = "2" * 40
MONO_OLD = "3" * 40
MONO_NEW = "4" * 40
SERIF_FOLDER = {"prefix": "Serif", "repository": FOLDERS, "path": "ofl/serif"}
MONO_FOLDER = {"prefix": "Mono", "repository": FOLDERS, "path": "ofl/mono"}


def families_toml(*tables):
    """A families.toml holding TABLES, each a [[family]] table's keys in order."""
    written = []
    for table in tables:
        lines = ["[[family]]"]
        lines += [
            f"{key} = {json.dumps(value)}"
            for key, value in table.items()
            if key != "directories"
        ]
        if "directories" in table:
            lines += ["", "[family.directories]"]
            lines += [f'"{s}" = "{d}"' for s, d in table["directories"].items()]
        written.append("\n".join(lines) + "\n")
    return "\n".join(written)


class Folders(Fonts):
    """A fonts directory of two families following folders of one repository.

    In the fabricated GitHub, each folder's newest commit has moved.
    """

    def setUp(self):
        super().setUp()
        for path in self.directory.iterdir():
            path.unlink()
        self.families = families_toml(SERIF_FOLDER, MONO_FOLDER)
        self.held = {
            "Mono-Bold.ttf": b"\x00\x01\x00\x00mono old",
            "Mono-OFL.txt": b"OFL, Mono\n",
            "Serif-Italic.woff2": b"wOF2serif old",
            "Serif-OFL.txt": b"OFL, 2021\n",
            "Serif-Regular.ttf": b"\x00\x01\x00\x00serif old",
        }
        self.pins = {f"{FOLDERS}/ofl/mono": MONO_OLD, f"{FOLDERS}/ofl/serif": SERIF_OLD}
        self.write_directory()

        self.new = {
            "Mono-Bold.ttf": b"\x00\x01\x00\x00mono new",
            "Mono-OFL.txt": b"OFL, Mono\n",
            "Serif-Italic.woff2": b"wOF2serif new",
            "Serif-OFL.txt": b"OFL, 2023\n",
            "Serif-Regular.ttf": b"\x00\x01\x00\x00serif new",
        }
        self.github.commits(FOLDERS, "ofl/serif", SERIF_NEW, SERIF_OLD)
        self.github.commits(FOLDERS, "ofl/mono", MONO_NEW)
        self.serve("ofl/serif", SERIF_NEW, "Serif", self.new)
        self.serve("ofl/mono", MONO_NEW, "Mono", self.new)

    def write_directory(self):
        for name, content in self.held.items():
            (self.directory / name).write_bytes(content)
        (self.directory / "SHA256SUMS").write_text(sums(self.held))
        (self.directory / "RELEASES").write_text(self.releases(self.pins))
        (self.directory / "families.toml").write_text(self.families)

    def serve(self, path, sha, prefix, files):
        """PREFIX's FILES in PATH at SHA, each licence under the publisher's name."""
        for name, content in files.items():
            if not name.startswith(f"{prefix}-"):
                continue
            if name.endswith("-OFL.txt"):
                name = "OFL.txt"
            self.github.folder_file(FOLDERS, sha, path, name, content)

    def expected(self, files, pins):
        return {
            **files,
            "RELEASES": self.releases(pins).encode(),
            "SHA256SUMS": sums(files).encode(),
            "families.toml": self.families.encode(),
        }


class FolderUpgradeTest(Folders):
    def test_moves_each_file_to_its_folders_newest_commit(self):
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            stdout.splitlines(),
            [
                f"{FOLDERS}/ofl/mono {MONO_OLD} -> {MONO_NEW}",
                f"{FOLDERS}/ofl/serif {SERIF_OLD} -> {SERIF_NEW}",
            ],
        )
        pins = {f"{FOLDERS}/ofl/mono": MONO_NEW, f"{FOLDERS}/ofl/serif": SERIF_NEW}
        self.assertEqual(self.contents(), self.expected(self.new, pins))

    def test_names_each_newest_commit(self):
        _, _, stderr = self.run_main()
        for path, sha in (("ofl/mono", MONO_NEW), ("ofl/serif", SERIF_NEW)):
            self.assertIn(
                f"fonts-upgrade: {FOLDERS}/{path} {sha}: the newest commit touching it",
                stderr,
            )

    def test_moves_one_family_of_a_repository_alone(self):
        self.github.commits(FOLDERS, "ofl/mono", MONO_OLD)
        self.serve("ofl/mono", MONO_OLD, "Mono", self.held)
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            stdout.splitlines(), [f"{FOLDERS}/ofl/serif {SERIF_OLD} -> {SERIF_NEW}"]
        )
        files = {
            **self.new,
            "Mono-Bold.ttf": self.held["Mono-Bold.ttf"],
            "Mono-OFL.txt": self.held["Mono-OFL.txt"],
        }
        pins = {f"{FOLDERS}/ofl/mono": MONO_OLD, f"{FOLDERS}/ofl/serif": SERIF_NEW}
        self.assertEqual(self.contents(), self.expected(files, pins))

    def test_reproduces_the_files_where_no_commit_moved(self):
        self.held = self.new
        self.pins = {f"{FOLDERS}/ofl/mono": MONO_NEW, f"{FOLDERS}/ofl/serif": SERIF_NEW}
        self.write_directory()
        before = self.contents()
        status, stdout, stderr = self.run_main()
        self.assertEqual((status, stdout), (0, ""), stderr)
        self.assertEqual(self.contents(), before)

    def test_takes_one_commit_for_families_sharing_a_folder(self):
        self.held = {k: v for k, v in self.held.items() if k != "Mono-OFL.txt"}
        self.families = families_toml(
            SERIF_FOLDER, {**MONO_FOLDER, "path": "ofl/serif"}
        )
        self.pins = {f"{FOLDERS}/ofl/serif": SERIF_OLD}
        for path in self.directory.iterdir():
            path.unlink()
        self.write_directory()
        self.github.folder_file(
            FOLDERS, SERIF_NEW, "ofl/serif", "Mono-Bold.ttf", self.new["Mono-Bold.ttf"]
        )
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            stdout.splitlines(), [f"{FOLDERS}/ofl/serif {SERIF_OLD} -> {SERIF_NEW}"]
        )
        asked = [url for url, _ in self.github.asked]
        self.assertEqual(asked.count(commits_url(FOLDERS, "ofl/serif")), 1)

    def test_prints_a_move_where_no_file_changed(self):
        self.serve("ofl/mono", MONO_NEW, "Mono", self.held)
        _, stdout, _ = self.run_main()
        self.assertIn(
            f"{FOLDERS}/ofl/mono {MONO_OLD} -> {MONO_NEW}", stdout.splitlines()
        )

    def test_asks_for_one_commit_and_each_file_at_it(self):
        self.run_main()
        api = "application/vnd.github+json"
        raw = "application/octet-stream"
        self.assertEqual(
            sorted(self.github.asked),
            [
                (f"{API}/repos/{FOLDERS}/commits?path=ofl/mono&per_page=1", api),
                (f"{API}/repos/{FOLDERS}/commits?path=ofl/serif&per_page=1", api),
                *(
                    (f"https://raw.githubusercontent.com/{FOLDERS}/{at}", raw)
                    for at in (
                        f"{SERIF_NEW}/ofl/serif/OFL.txt",
                        f"{SERIF_NEW}/ofl/serif/Serif-Italic.woff2",
                        f"{SERIF_NEW}/ofl/serif/Serif-Regular.ttf",
                        f"{MONO_NEW}/ofl/mono/Mono-Bold.ttf",
                        f"{MONO_NEW}/ofl/mono/OFL.txt",
                    )
                ),
            ],
        )

    def test_takes_a_licence_by_the_publishers_name(self):
        self.github.folder_file(
            FOLDERS, SERIF_NEW, "ofl/serif", "Serif-OFL.txt", b"not this one"
        )
        status, _, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            (self.directory / "Serif-OFL.txt").read_bytes(), self.new["Serif-OFL.txt"]
        )

    def test_quotes_the_path_and_each_name(self):
        self.families = families_toml(
            {**SERIF_FOLDER, "path": "ofl/serif one"}, MONO_FOLDER
        )
        self.pins = {
            f"{FOLDERS}/ofl/mono": MONO_OLD,
            f"{FOLDERS}/ofl/serif one": SERIF_OLD,
        }
        self.held = {"Mono-Bold.ttf": b"old", "Serif-Book Italic.ttf": b"old"}
        for path in self.directory.iterdir():
            path.unlink()
        self.write_directory()
        self.github.commits(FOLDERS, "ofl/serif%20one", SERIF_NEW)
        self.github.folder_file(
            FOLDERS, SERIF_NEW, "ofl/serif%20one", "Serif-Book%20Italic.ttf", b"new"
        )
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertIn(
            f"{FOLDERS}/ofl/serif one {SERIF_OLD} -> {SERIF_NEW}", stdout.splitlines()
        )
        self.assertEqual(
            (self.directory / "Serif-Book Italic.ttf").read_bytes(), b"new"
        )

    def test_path_with_an_arrow_inside_a_word(self):
        self.families = families_toml({**SERIF_FOLDER, "path": "ofl/a->b"}, MONO_FOLDER)
        self.pins = {f"{FOLDERS}/ofl/mono": MONO_OLD, f"{FOLDERS}/ofl/a->b": SERIF_OLD}
        self.write_directory()
        self.github.commits(FOLDERS, "ofl/a-%3Eb", SERIF_NEW)
        self.serve("ofl/a-%3Eb", SERIF_NEW, "Serif", self.new)
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertIn(
            f"{FOLDERS}/ofl/a->b {SERIF_OLD} -> {SERIF_NEW}", stdout.splitlines()
        )


class MixedTest(Fonts):
    """The release families' fonts directory, with a family following a folder."""

    def setUp(self):
        super().setUp()
        self.families = FAMILIES + "\n" + families_toml(SERIF_FOLDER)
        (self.directory / "families.toml").write_text(self.families)
        (self.directory / "Serif-Regular.ttf").write_bytes(b"serif old")
        held = {**self.held, "Serif-Regular.ttf": b"serif old"}
        (self.directory / "SHA256SUMS").write_text(sums(held))
        pins = {**HELD, f"{FOLDERS}/ofl/serif": SERIF_OLD}
        (self.directory / "RELEASES").write_text(self.releases(pins))
        self.github.commits(FOLDERS, "ofl/serif", SERIF_NEW)
        self.github.folder_file(
            FOLDERS, SERIF_NEW, "ofl/serif", "Serif-Regular.ttf", b"serif new"
        )

    def test_moves_release_and_folder_families_together(self):
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            stdout.splitlines(),
            [
                f"{FOLDERS}/ofl/serif {SERIF_OLD} -> {SERIF_NEW}",
                f"{MONO} v1.006 -> v1.100",
                f"{SERIF} 4.004R -> 4.005R",
            ],
        )
        files = {**self.new, "Serif-Regular.ttf": b"serif new"}
        pins = {**NEW, f"{FOLDERS}/ofl/serif": SERIF_NEW}
        expected = {
            **files,
            "README.md": b"# Fonts\n",
            "RELEASES": self.releases(pins).encode(),
            "SHA256SUMS": sums(files).encode(),
            "families.toml": self.families.encode(),
        }
        self.assertEqual(self.contents(), expected)

    def test_release_refused(self):
        self.github.release(MONO, "v1.100", {"a.zip": (archive({"x": b"x"}), None)})
        self.assert_refused(f"{MONO} v1.100: no ExampleSansMono-Bold.woff2")

    def test_folder_refused(self):
        self.github.commits(FOLDERS, "ofl/serif")
        self.assert_refused(f"{FOLDERS}/ofl/serif: no commit")


class FolderRefusalTest(Folders):
    def test_font_missing_at_the_commit(self):
        url = folder_url(FOLDERS, SERIF_NEW, "ofl/serif", "Serif-Regular.ttf")
        del self.github.bodies[url]
        self.assert_refused(
            f"{FOLDERS}/ofl/serif {SERIF_NEW}: Serif-Regular.ttf: {url}: HTTP Error 404"
        )

    def test_font_only_in_a_folder_below(self):
        url = folder_url(FOLDERS, MONO_NEW, "ofl/mono", "Mono-Bold.ttf")
        del self.github.bodies[url]
        self.github.folder_file(
            FOLDERS, MONO_NEW, "ofl/mono/static", "Mono-Bold.ttf", b"static"
        )
        self.assert_refused(
            f"{FOLDERS}/ofl/mono {MONO_NEW}: Mono-Bold.ttf: {url}: HTTP Error 404"
        )

    def test_licence_missing_at_the_commit(self):
        url = folder_url(FOLDERS, MONO_NEW, "ofl/mono", "OFL.txt")
        del self.github.bodies[url]
        self.assert_refused(
            f"{FOLDERS}/ofl/mono {MONO_NEW}: OFL.txt: {url}: HTTP Error 404"
        )

    def test_folder_no_commit_touches(self):
        self.github.commits(FOLDERS, "ofl/mono")
        self.assert_refused(
            f"{FOLDERS}/ofl/mono: no commit on the default branch touches it"
        )

    def test_commit_lookup_failing(self):
        url = commits_url(FOLDERS, "ofl/mono")
        del self.github.bodies[url]
        self.assert_refused(f"{url}: HTTP Error 404")

    def assert_table_refused(self, table, message):
        """main refuses families.toml holding TABLE, asking GitHub nothing."""
        self.families = families_toml(table, MONO_FOLDER)
        self.write_directory()
        self.assert_refused(message)
        self.assertEqual(self.github.asked, [])

    def test_table_with_path_and_directories(self):
        self.assert_table_refused(
            {**SERIF_FOLDER, "directories": {".ttf": "ttf"}},
            "families.toml: Serif: both path and directories",
        )

    def test_table_with_neither_path_nor_directories(self):
        self.assert_table_refused(
            {"prefix": "Serif", "repository": FOLDERS},
            "families.toml: Serif: neither path nor directories",
        )

    def test_table_with_path_and_licences_from_repository(self):
        for value in (True, False):
            with self.subTest(value=value):
                self.assert_table_refused(
                    {**SERIF_FOLDER, "licences-from-repository": value},
                    "families.toml: Serif: both path and licences-from-repository",
                )

    def test_path_not_a_folders_from_the_root(self):
        paths = (
            "",
            "/ofl/serif",
            "ofl/serif/",
            "ofl//serif",
            "./ofl/serif",
            "ofl/./serif",
            "ofl/../ofl/serif",
            "..",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assert_table_refused(
                    {**SERIF_FOLDER, "path": path},
                    f"families.toml: Serif: path {path!r} is not a folder's from the"
                    " repository's root",
                )

    def test_path_not_a_move_records_take(self):
        paths = (
            "ofl/serif  one",
            "ofl/serif ",
            " ofl/serif",
            "ofl/serif\tone",
            "ofl/serif\none",
            "ofl/a -> b",
            "ofl/a ->",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assert_table_refused(
                    {**SERIF_FOLDER, "path": path},
                    f"families.toml: Serif: path {path!r} is not words joined by"
                    " single spaces, none of them ->",
                )

    def test_releases_holding_no_tag_or_commit(self):
        key = f"{FOLDERS}/ofl/serif"
        for held in (f"{SERIF_OLD}\t", "", "->"):
            with self.subTest(held=held):
                self.pins = {f"{FOLDERS}/ofl/mono": MONO_OLD, key: held}
                self.write_directory()
                self.assert_refused(f"RELEASES: {key}: {held!r} is not a tag or commit")
                self.assertEqual(self.github.asked, [])

    def test_releases_keying_a_folder_by_its_repository(self):
        self.pins = {FOLDERS: SERIF_OLD, f"{FOLDERS}/ofl/mono": MONO_OLD}
        self.write_directory()
        self.assert_refused(
            f"RELEASES: lists {sorted(self.pins)}, not "
            f"{[f'{FOLDERS}/ofl/mono', f'{FOLDERS}/ofl/serif']}"
        )

    def test_releases_missing_a_family(self):
        self.pins = {f"{FOLDERS}/ofl/mono": MONO_OLD}
        self.write_directory()
        self.assert_refused(
            f"RELEASES: lists {[f'{FOLDERS}/ofl/mono']}, not "
            f"{[f'{FOLDERS}/ofl/mono', f'{FOLDERS}/ofl/serif']}"
        )


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/body")
            self.end_headers()
        elif self.path in {"/body", "/api/body"}:
            body = json.dumps(
                {
                    "accept": self.headers["Accept"],
                    "authorization": self.headers["Authorization"],
                    "version": self.headers["X-GitHub-Api-Version"],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_follows_a_redirect_asking_for_the_media_type(self):
        body = json.loads(fonts_upgrade.fetch(f"{self.base}/redirect", "a/b"))
        self.assertEqual(
            body, {"accept": "a/b", "authorization": None, "version": "2022-11-28"}
        )

    def test_asks_for_githubs_json_by_default(self):
        body = json.loads(fonts_upgrade.fetch(f"{self.base}/body"))
        self.assertEqual(body["accept"], "application/vnd.github+json")

    def authorization(self, path, environment):
        """The Authorization header fetch sends for PATH.

        The API is served under /api, and ENVIRONMENT's tokens alone are set.
        """
        tokens = {"GH_TOKEN": "", "GITHUB_TOKEN": "", **environment}
        with (
            unittest.mock.patch.object(fonts_upgrade, "API", f"{self.base}/api"),
            unittest.mock.patch.dict(os.environ, tokens),
        ):
            body = json.loads(fonts_upgrade.fetch(f"{self.base}{path}"))
        return body["authorization"]

    def test_sends_gh_token_to_the_api(self):
        self.assertEqual(
            self.authorization("/api/body", {"GH_TOKEN": "a", "GITHUB_TOKEN": "b"}),
            "Bearer a",
        )

    def test_sends_github_token_to_the_api_where_gh_token_is_empty(self):
        self.assertEqual(
            self.authorization("/api/body", {"GITHUB_TOKEN": "b"}), "Bearer b"
        )

    def test_sends_no_token_where_neither_is_set(self):
        self.assertIsNone(self.authorization("/api/body", {}))

    def test_sends_no_token_outside_the_api(self):
        self.assertIsNone(self.authorization("/body", {"GH_TOKEN": "a"}))

    def test_http_error(self):
        url = f"{self.base}/missing"
        with self.assertRaisesRegex(
            fonts_upgrade.Refusal, f"^{url}: HTTP Error 404: Not Found$"
        ):
            fonts_upgrade.fetch(url)

    def test_no_server(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{unused.getsockname()[1]}/"
        with self.assertRaisesRegex(fonts_upgrade.Refusal, f"^{url}: "):
            fonts_upgrade.fetch(url)

    def test_server_never_answering(self):
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen()
            url = f"http://127.0.0.1:{silent.getsockname()[1]}/"
            with (
                unittest.mock.patch.object(fonts_upgrade, "TIMEOUT_S", 0.1),
                self.assertRaisesRegex(fonts_upgrade.Refusal, f"^{url}: "),
            ):
                fonts_upgrade.fetch(url)


if __name__ == "__main__":
    unittest.main()
