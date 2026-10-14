#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate evidence readers and isolation guards without launching the game."""

import gzip
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import validation
import solve_sliding


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png(rows):
    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class ValidationTests(unittest.TestCase):
    def test_labels_do_not_escape_output_root(self):
        for name in ("..", "../escape", "/tmp/escape", "a/b", "a\\b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validation.identifier(name)
        self.assertEqual(validation.identifier("packaged-001"), "packaged-001")

    def test_output_symlink_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (root / "artifacts").symlink_to(elsewhere, target_is_directory=True)
            with mock.patch.object(validation, "ROOT", root), mock.patch.object(validation, "VALIDATION", root / "artifacts/validation"):
                with self.assertRaises(ValueError):
                    validation.output_path("test")
            self.assertEqual(list(elsewhere.iterdir()), [])

    def test_nested_input_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "file.bin").write_bytes(b"input")
            (source / "alias").symlink_to(source, target_is_directory=True)
            with self.assertRaises(ValueError):
                validation.regular_files(source)

    def test_png_filters_decode_same_pixels(self):
        first = bytes([0, 10, 20, 30, 40, 50])
        second = bytes([60, 70, 80, 90, 100, 110])
        encoded_second = {
            0: second,
            1: bytes([60, 70, 80, 30, 30, 30]),
            2: bytes([60, 60, 60, 60, 60, 60]),
            3: bytes([60, 65, 70, 45, 45, 45]),
            4: bytes([60, 60, 60, 30, 30, 30]),
        }
        with tempfile.TemporaryDirectory() as temp:
            for mode, row in encoded_second.items():
                path = Path(temp) / (str(mode) + ".png")
                path.write_bytes(png(bytes([0]) + first + bytes([mode]) + row))
                with self.subTest(mode=mode):
                    info = validation.png_stats(path)
                    self.assertEqual(info["dimensions"], [2, 2])
                    self.assertEqual(info["nonblack_pixels"], 4)
                    self.assertEqual(info["rgb_sha256"], hashlib.sha256(first + second).hexdigest())

    def test_png_black_and_corrupt_capture_are_distinguished(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "frame.png"
            path.write_bytes(png(bytes(14)))
            self.assertFalse(validation.png_stats(path)["nonblack"])
            raw = bytearray(path.read_bytes())
            raw[16] ^= 1
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "CRC"):
                validation.png_stats(path)

    def test_profile_reader_accepts_native_gzip_and_rejects_bad_index(self):
        name = bytearray(36)
        name[:8] = b"PackTest"
        struct.pack_into("<ii", name, 28, 0, 1)
        block = struct.pack("<III", 7, 0, 4) + b"game"
        index = bytearray(44)
        index[:4] = b"menu"
        fixed_size = 12 + len(name) + 12 + len(index)
        struct.pack_into("<II", index, 36, 12 + fixed_size, len(block))
        data = (struct.pack("<III", 0xface01, 0x102, fixed_size) + struct.pack("<III", 0x100, 0, 36) + name +
                struct.pack("<III", 0x200, 0, 44) + index + block + b"SVMCR")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "flaaklypa.000"
            path.write_bytes(gzip.compress(data))
            info = validation.inspect_profile(path)
            self.assertEqual(info["name"], "PackTest")
            self.assertEqual((info["month"], info["day"]), (0, 1))
            self.assertEqual(info["sections"][0]["name"], "menu")
            bad = bytearray(data)
            struct.pack_into("<I", bad, 12 + 48 + 12 + 36, len(data) + 1)
            path.write_bytes(bad)
            with self.assertRaisesRegex(ValueError, "index"):
                validation.inspect_profile(path)

    def test_build_provenance_freezes_inventory_or_reviewed_source_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / "fjelltoppen"
            executable.write_bytes(b"compiled fixture")
            patch = root / "source.patch"
            patch.write_bytes(b"reviewed local diff")
            inventory = root / "source-inventory.json"
            inventory.write_text("[]\n")
            checkpoint = root / "source-checkpoint.json"
            checkpoint.write_text("{}\n")
            code = root / "source.cpp"
            code.write_text("source fixture\n")
            for schema in ("reviewed-files", "source-inventory"):
                proof = {"executable": validation.record(executable), "source_patch": validation.record(patch)}
                if schema == "reviewed-files":
                    proof["source_files"] = [validation.record(code)]
                else:
                    proof["source_inventory"] = validation.record(inventory)
                    proof["build_source_checkpoint"] = validation.record(checkpoint)
                # Provenance paths are repository relative.
                for key in ("source_patch", "source_inventory", "build_source_checkpoint"):
                    if key in proof:
                        proof[key]["path"] = Path(proof[key]["path"]).name
                for item in proof.get("source_files", []):
                    item["path"] = Path(item["path"]).name
                path = root / ("proof-" + schema + ".json")
                path.write_text(json.dumps(proof))
                out = root / schema
                out.mkdir()
                with self.subTest(schema=schema), mock.patch.object(validation, "ROOT", root):
                    frozen = validation.freeze_build_provenance(out, path, executable)
                    self.assertEqual(Path(frozen["build_source_patch"]["path"]).read_bytes(), patch.read_bytes())
                    self.assertEqual(len(frozen["build_source_files"]), 1 if schema == "reviewed-files" else 0)
                    self.assertEqual(len(frozen["build_checkpoint_files"]), 2 if schema == "source-inventory" else 0)

    def test_sliding_plan_uses_legal_moves_and_reaches_goal(self):
        board = (5, 2, 3, 4, 0, 6, 8, 7, 9)
        plan = solve_sliding.solve(board)
        current = list(board)
        for cell in plan:
            gap = current.index(0)
            self.assertEqual(abs(cell // 3 - gap // 3) + abs(cell % 3 - gap % 3), 1)
            current[gap], current[cell] = current[cell], current[gap]
        self.assertEqual(current, [0, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(solve_sliding.solve((2, 0, 3, 4, 5, 6, 7, 8, 9)), [0])

    def test_sliding_reader_rejects_unobserved_or_larger_boards(self):
        with self.assertRaises(ValueError):
            solve_sliding.read_board("No board logged")
        with self.assertRaises(ValueError):
            solve_sliding.read_board("Sliding: row 0: 0 2 3 4\nSliding: row 1: 5 6 7 8\nSliding: row 2: 9 10 11 12")

    def test_changed_or_added_resource_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            app = out / "Fjelltoppen.app"
            app.mkdir()
            executable = app / "fjelltoppen"
            executable.write_bytes(b"native app fixture")
            data = out / "data"
            data.mkdir()
            resource = data / "common.bin"
            resource.write_bytes(b"asset fixture")
            manifest = {"app": str(app), "app_files": [validation.record(executable)],
                        "resources": [{"destination": "common.bin", "copy": validation.record(resource)}]}
            (out / "installation.json").write_text(json.dumps(manifest))
            validation.verify_installation(out)
            resource.write_bytes(b"changed asset")
            with self.assertRaisesRegex(ValueError, "resource changed"):
                validation.verify_installation(out)
            resource.write_bytes(b"asset fixture")
            (data / "extra.bin").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "inventory"):
                validation.verify_installation(out)


if __name__ == "__main__":
    unittest.main()
