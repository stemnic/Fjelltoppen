#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test public manifest projection and compiler/artifact privacy boundaries."""

import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import build
import public_manifest


def package_report():
    application = "artifacts/build/fixture/Fjelltoppen.app"
    checksum = hashlib.sha256(b"synthetic package data").hexdigest()

    def item(path):
        return {"path": path, "bytes": 22, "sha256": checksum}

    private = "/private-fixture/customer/source"
    checks = {name: {"command": ["fixture-tool", private], "exit_code": 0,
                     "stdout": private, "stderr": private} for name in public_manifest.CHECKS}
    checks["architectures"]["stdout"] = "x86_64 arm64\n"
    checks["deployment_targets"]["stdout"] = private + "\n    minos 11.0\n      sdk 27.0\n" * 2
    checks["dynamic_dependencies"]["stdout"] = private + ":\n\t/usr/lib/libz.1.dylib (compatibility version 1.0.0, current version 1.2.12)\n"
    checks["signature_details"]["stderr"] = "Executable=" + private + "\nSignature=adhoc\n"
    return {
        "upstream_repository": public_manifest.UPSTREAM, "upstream_commit": "1" * 40,
        "source_commit": "2" * 40, "source_branch": "private-local-branch",
        "compiler": "Apple clang version 21.0.0 (clang-2100.3.34.2)\nTarget: arm64-apple-darwin27.0.0\nInstalledDir: " + private + "\n",
        "application": application, "executable": item(application + "/Contents/MacOS/fjelltoppen"),
        "application_files": [item(application + "/Contents/Resources/" + name) for name in public_manifest.SUPPORT_FILES]
                             + [item(application + "/Contents/Resources/licenses/COPYING"), item(application + "/Contents/MacOS/fjelltoppen")],
        "disk_image": item("artifacts/build/fixture/Fjelltoppen-0.1.0-macos.dmg"),
        "dependency_archives": [item("artifacts/build/fixture/download/SDL2-2.32.10.tar.gz")],
        "current_source_inputs_match_build": True, "artifact_privacy_verified": True, "checks": checks,
        "unknown_private_payload": {"logs": private, "player": "Private Fixture Person"},
    }


class PublicManifestTests(unittest.TestCase):
    def test_projection_discards_commands_logs_local_paths_and_unknown_fields(self):
        report = public_manifest.project(package_report())
        encoded = json.dumps(report)
        for marker in ("private-fixture", "customer", "InstalledDir", "private-local-branch", "Private Fixture Person", "artifacts/build", "command", "stderr"):
            self.assertNotIn(marker, encoded)
        self.assertEqual(report["compiler"], {"version": "Apple clang version 21.0.0", "target": "arm64-apple-darwin27.0.0"})
        self.assertEqual(report["disk_image"]["path"], "Fjelltoppen-0.1.0-macos.dmg")
        self.assertEqual(report["architectures"], ["arm64", "x86_64"])
        self.assertTrue(all(report["checks"].values()))

    def test_missing_privacy_proof_or_unsuccessful_native_check_is_refused(self):
        for field in ("privacy", "signature", "disk-contents"):
            report = package_report()
            if field == "privacy":
                report["artifact_privacy_verified"] = False
            else:
                report["checks"]["code_signature" if field == "signature" else "disk_image_contents"]["exit_code"] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                public_manifest.project(report)

    def test_escaping_package_paths_and_non_system_dependencies_are_refused(self):
        report = package_report()
        bad_values = ["/private-fixture/file", "artifacts/build/fixture/Fjelltoppen.app/../private", "artifacts\\build\\file"]
        for value in bad_values:
            changed = copy.deepcopy(report)
            changed["application_files"][0]["path"] = value
            with self.subTest(path=value), self.assertRaises(ValueError):
                public_manifest.project(changed)
        changed = copy.deepcopy(report)
        changed["checks"]["dynamic_dependencies"]["stdout"] = "\t/private-fixture/libsecret.dylib (compatibility version 1.0.0)\n"
        with self.assertRaises(ValueError):
            public_manifest.project(changed)

    def test_unstructured_compiler_and_fingerprint_payloads_are_refused(self):
        for field in ("compiler", "sha256", "size"):
            report = package_report()
            if field == "compiler":
                report["compiler"] = "Apple clang version 21.0.0 /private-fixture/customer\nTarget: arm64-apple-darwin27.0.0\n"
            else:
                report["executable"]["sha256" if field == "sha256" else "bytes"] = "/private-fixture/customer"
            with self.subTest(field=field), self.assertRaises(ValueError):
                public_manifest.project(report)

    def test_build_inspection_rejects_embedded_user_path_before_publishing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = directory / "Fjelltoppen.app"
            executable = app / "Contents/MacOS/fjelltoppen"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"synthetic executable\0/" + b"Users" + b"/fixture-account/source.cpp\0")
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.1.0"}))
            (directory / "Fjelltoppen-0.1.0-macos.dmg").write_bytes(b"synthetic image")
            with mock.patch.object(build, "capture") as native:
                with self.assertRaisesRegex(ValueError, "private path"):
                    build.inspect(directory)
                native.assert_not_called()
            self.assertFalse((directory / "public-manifest.json").exists())

    @unittest.skipUnless(sys.platform == "darwin", "Requires Apple's compiler")
    def test_compiler_wrappers_map_c_and_cpp_paths_containing_spaces(self):
        script = build.SCRIPT.read_text()
        wrapper = script.split('cat > "$compiler_wrappers/clang" <<\'EOF\'\n', 1)[1].split("\nEOF\n", 1)[0]
        with tempfile.TemporaryDirectory(prefix="fjelltoppen spaces ") as temporary:
            root = Path(temporary)
            source = root / "source files"
            source.mkdir()
            tools = root / "compiler wrappers"
            tools.mkdir()
            environment = dict(os.environ, FJELLTOPPEN_SOURCE_ROOT=str(source), FJELLTOPPEN_BUILD_ROOT=str(root / "build files"),
                               FJELLTOPPEN_DEVELOPER_ROOT=subprocess.check_output(["xcode-select", "-p"], text=True).strip())
            for compiler, suffix in (("clang", "c"), ("clang++", "cpp")):
                path = tools / compiler
                path.write_text(wrapper + "\n")
                path.chmod(0o700)
                code = source / ("example." + suffix)
                code.write_text('#include <stdio.h>\nint main(void) { puts(__FILE__); return 0; }\n')
                executable = root / ("example-" + suffix)
                subprocess.run([str(path), str(code), "-o", str(executable)], env=environment, check=True, capture_output=True)
                self.assertEqual(Path(subprocess.check_output([str(executable)], text=True).strip()).as_posix(), "src/example." + suffix)
                self.assertNotIn(str(source).encode(), executable.read_bytes())


if __name__ == "__main__":
    unittest.main()
