#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep resource content and SVG rendering bytes intact during sanitization."""

import io
from pathlib import Path
import tempfile
import unittest
import xml.parsers.expat
import zipfile

import sanitize_resources

SVG = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<svg xmlns="http://www.w3.org/2000/svg" '
       b'xmlns:editor="http://www.inkscape.org/namespaces/inkscape" '
       b'xmlns:legacy="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
       b'viewBox="0 0 24 24" editor:export-filename="/private-fixture/export.png" '
       b'legacy:docbase="/private-fixture" legacy:docname="fixture.svg">\n'
       b'<!-- License: Synthetic fixture; no game artwork. -->\n'
       b'<metadata>License text and creator credit stay verbatim.</metadata>\n'
       b'<path d="M 1,2 L 3,4 Z" fill="#123456" stroke-width="0.5"/>\n'
       b'</svg>\n')


def archive(svg=SVG):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        output.comment = b"Public synthetic archive comment"
        for name, data in (("fixture.svg", svg), ("LICENSE.txt", b"Synthetic fixture license\n"), ("unchanged.txt", b"Unrelated bytes\x00\xff\n")):
            info = zipfile.ZipInfo(name, date_time=(2020, 2, 3, 4, 5, 6))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.comment = b"Public member comment"
            output.writestr(info, data)
    return stream.getvalue()


class ResourceSanitizerTests(unittest.TestCase):
    def test_svg_removes_only_editor_filesystem_attributes_and_keeps_other_bytes(self):
        expected = SVG
        for attribute in (b' editor:export-filename="/private-fixture/export.png"', b' legacy:docbase="/private-fixture"', b' legacy:docname="fixture.svg"'):
            expected = expected.replace(attribute, b"")
        actual = sanitize_resources.sanitize_svg(SVG)
        self.assertEqual(actual, expected)
        self.assertIn(b'xmlns:editor=', actual)
        self.assertIn(b'viewBox="0 0 24 24"', actual)
        self.assertIn(b'License text and creator credit stay verbatim.', actual)
        self.assertNotIn(b"private-fixture", actual)

    def test_namespace_aliases_scope_and_comment_text_are_handled_precisely(self):
        svg = (b'<svg xmlns:editor="http://www.inkscape.org/namespaces/inkscape">'
               b'<!-- editor:export-filename="comment text" -->'
               b'<g editor:export-filename=\'fixture.png\'/>'
               b'<g xmlns:editor="urn:unrelated" editor:export-filename="semantic attribute"/>'
               b'</svg>')
        expected = svg.replace(b" editor:export-filename='fixture.png'", b"")
        self.assertEqual(sanitize_resources.sanitize_svg(svg), expected)
        with self.assertRaises(xml.parsers.expat.ExpatError):
            sanitize_resources.sanitize_svg(b'<svg><path broken="value></svg>')

    def test_zip_preserves_member_bytes_metadata_names_comments_and_license(self):
        original = archive()
        output, changed = sanitize_resources.sanitize_zip(original)
        self.assertEqual(changed, ["fixture.svg"])
        with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(output)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(before.comment, after.comment)
            self.assertIsNone(after.testzip())
            for name in before.namelist():
                old, new = before.getinfo(name), after.getinfo(name)
                for field in ("date_time", "compress_type", "external_attr", "extra", "comment", "create_system"):
                    self.assertEqual(getattr(old, field), getattr(new, field))
                expected = sanitize_resources.sanitize_svg(before.read(name)) if name.endswith(".svg") else before.read(name)
                self.assertEqual(after.read(name), expected)

    def test_archive_without_editor_metadata_is_byte_identical(self):
        original = archive(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0,0 L1,1"/></svg>')
        output, changed = sanitize_resources.sanitize_zip(original)
        self.assertEqual(output, original)
        self.assertEqual(changed, [])

    def test_only_copied_zip_and_dat_resources_are_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "upstream.zip"
            source.write_bytes(archive())
            resources = root / "Resources"
            resources.mkdir()
            for name in ("theme.zip", "gui-icons.dat"):
                (resources / name).write_bytes(source.read_bytes())
            (resources / "unchanged.txt").write_bytes(b"Unrelated application resource")
            (resources / "nonarchive.dat").write_bytes(b"Not a ZIP archive")
            changed = sanitize_resources.sanitize_directory(resources)
            self.assertEqual(sorted(changed), [("gui-icons.dat", "fixture.svg"), ("theme.zip", "fixture.svg")])
            self.assertEqual(source.read_bytes(), archive())
            self.assertEqual((resources / "unchanged.txt").read_bytes(), b"Unrelated application resource")
            self.assertEqual((resources / "nonarchive.dat").read_bytes(), b"Not a ZIP archive")


if __name__ == "__main__":
    unittest.main()
