#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build with Fjelltoppen's packaging script and verify the resulting app/DMG."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

import public_audit
import public_manifest

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = "dbbf9d9a2463078bfe3eb8d94de68edd949b7f6f"
SCRIPT = ROOT / "dists/fjelltoppen/macos/build.sh"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record(path):
    path = Path(path).absolute()
    return {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha(path)}


def capture(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command}\n{result.stdout}{result.stderr}")
    return {"command": command, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def output_directory(path):
    path = Path(path).absolute()
    base = ROOT / "artifacts/build"
    if path == base or not path.is_relative_to(base):
        raise ValueError("Build output must be a named directory below artifacts/build")
    for ancestor in [path, *path.parents]:
        if ancestor == ROOT:
            break
        if ancestor.is_symlink():
            raise ValueError("Build output must not traverse symlinks")
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Build output escapes artifacts/build")
    return path


def source_inventory():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    roots = ("base/", "backends/", "engines/", "graphics/", "video/", "image/", "audio/", "math/", "common/", "gui/", "LICENSES/", "dists/fjelltoppen/")
    names = {raw.decode() for raw in paths.split(b"\0") if raw}
    files = []
    for name in sorted(names):
        path = ROOT / name
        if path.is_file() and (name.startswith(roots) or name in
                ("configure", "Makefile", "Makefile.common", "rules.mk", "COPYING", "AUTHORS", "COPYRIGHT", "NEWS.md", "README.md",
                 "config.guess", "config.sub", "engines.awk", "plugin.exp",
                 "tools/macos_port/build.py", "tools/macos_port/public_manifest.py", "tools/macos_port/public_audit.py", "tools/macos_port/sanitize_resources.py",
                 "dists/engine-data/fonts.dat", "dists/engine-data/helpdialog.zip") or name.endswith(".mk")):
            files.append(record(path))
    return files


def checkpoint_paths(directory):
    paths = [directory / name for name in ("build-source-inventory.json", "build-checkpoint-source.patch", "build-source-checkpoint.json")]
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths):
        raise ValueError("Build checkpoint outputs must be regular files")
    return paths


def manifest_output(directory, path=None):
    path = Path(path).absolute() if path else directory / "build-manifest.json"
    if path.parent.resolve() != directory.resolve() or path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Manifest must be a regular file in the selected build directory")
    if path.name in {p.name for p in checkpoint_paths(directory)}:
        raise ValueError("Manifest must not overwrite a build checkpoint")
    if path.name == "public-manifest.json":
        raise ValueError("Private manifest must not overwrite the public manifest")
    return path


def freeze_source(directory, mode, log=None):
    inventory, patch, path = checkpoint_paths(directory)
    log_record = record(log) if log else None
    inventory.write_text(json.dumps(source_inventory(), indent=2) + "\n")
    patch.write_bytes(subprocess.check_output(["git", "diff", "--binary", UPSTREAM], cwd=ROOT))
    checkpoint = {
        "schema": 1, "capture_mode": mode, "upstream_commit": UPSTREAM,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "source_inventory": record(inventory), "source_patch": record(patch),
        "compiler": capture(["/usr/bin/clang++", "--version"])["stdout"],
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if log_record:
        checkpoint["build_log"] = log_record
    path.write_text(json.dumps(checkpoint, indent=2) + "\n")
    return checkpoint


def inspect_disk_image(dmg, app, files, forbidden_roots):
    with tempfile.TemporaryDirectory(prefix="fjelltoppen-dmg-audit-") as temporary:
        mount = Path(temporary) / "volume"
        mount.mkdir()
        capture(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist", "-mountpoint", str(mount), str(dmg)])
        try:
            if {p.name for p in mount.iterdir()} != {app.name, "Les meg.txt", "Programmer"}:
                raise ValueError("Unexpected disk image contents")
            if not (mount / "Programmer").is_symlink() or (mount / "Programmer").readlink() != Path("/Applications"):
                raise ValueError("Unexpected disk image Applications link")
            bundled = mount / app.name
            public_audit.assert_public_artifact(bundled, forbidden_roots=forbidden_roots)
            expected = {p.relative_to(app): sha(p) for p in files}
            actual = {p.relative_to(bundled): sha(p) for p in bundled.rglob("*") if p.is_file()}
            if actual != expected:
                raise ValueError("Disk image application does not match the inspected application")
            instructions = mount / "Les meg.txt"
            public_audit.assert_public_artifact(instructions, forbidden_roots=forbidden_roots)
            if sha(instructions) != sha(ROOT / "dists/fjelltoppen/LESMEG.txt"):
                raise ValueError("Disk image instructions do not match the public source")
        finally:
            capture(["hdiutil", "detach", str(mount)])
    return {"exit_code": 0, "application_files_match": True, "privacy_verified": True}


def inspect(directory, manifest_path=None):
    path = manifest_output(directory, manifest_path)
    app = directory / "Fjelltoppen.app"
    if app.is_symlink() or not app.is_dir():
        raise ValueError("Expected an independent application directory")
    executable = app / "Contents/MacOS/fjelltoppen"
    plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    version = plist["CFBundleShortVersionString"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", version):
        raise ValueError("Invalid application version")
    dmg = directory / f"Fjelltoppen-{version}-macos.dmg"
    if dmg.is_symlink():
        raise ValueError("Expected an independent disk image")
    if not executable.is_file() or not dmg.is_file():
        raise ValueError("Expected the completed Fjelltoppen app and versioned disk image")
    files = sorted(p for p in app.rglob("*") if p.is_file())
    if any(p.is_symlink() for p in app.rglob("*")):
        raise ValueError("Unexpected symlink in standalone app")
    forbidden_roots = (ROOT, directory, Path.home())
    public_audit.assert_public_artifact(app, forbidden_roots=forbidden_roots)
    checks = {
        "architectures": capture(["lipo", "-archs", str(executable)]),
        "dynamic_dependencies": capture(["otool", "-L", str(executable)]),
        "deployment_targets": capture(["vtool", "-show-build", str(executable)]),
        "code_signature": capture(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]),
        "signature_details": capture(["codesign", "--display", "--verbose=4", str(app)]),
        "disk_image": capture(["hdiutil", "verify", str(dmg)]),
    }
    checks["disk_image_contents"] = inspect_disk_image(dmg, app, files, forbidden_roots)
    if set(checks["architectures"]["stdout"].split()) != {"arm64", "x86_64"}:
        raise ValueError("Expected both ARM64 and x86_64 executable slices")
    dependencies = re.findall(r"^\s+(.+?) \(compatibility version", checks["dynamic_dependencies"]["stdout"], re.MULTILINE)
    if not dependencies or any(not path.startswith(("/System/Library/", "/usr/lib/")) for path in dependencies):
        raise ValueError("App depends on non-system dynamic libraries")
    minimums = re.findall(r"^\s+minos (\S+)", checks["deployment_targets"]["stdout"], re.MULTILINE)
    if minimums != ["11.0", "11.0"]:
        raise ValueError("Expected macOS 11.0 deployment target for both slices")
    if plist["LSMinimumSystemVersion"] != "11.0" or plist["CFBundleExecutable"] != "fjelltoppen":
        raise ValueError("Unexpected application metadata")
    resources = app / "Contents/Resources"
    required = ("scummremastered.zip", "scummmodern.zip", "scummclassic.zip", "gui-icons.dat",
                "shaders.dat", "translations.dat", "fonts.dat", "helpdialog.zip")
    if not all((resources / name).is_file() for name in required) or not (resources / "licenses/COPYING").is_file():
        raise ValueError("Packaged support data or GPL license missing")
    checkpoint_path = directory / "build-source-checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("No frozen build source checkpoint; rebuild or explicitly adopt a known operator build")
    checkpoint = json.loads(checkpoint_path.read_text())
    for key in ("source_inventory", "source_patch", "build_log"):
        if key in checkpoint and sha(ROOT / checkpoint[key]["path"]) != checkpoint[key]["sha256"]:
            raise ValueError("Frozen build checkpoint changed: " + key)
    frozen_inventory = json.loads((ROOT / checkpoint["source_inventory"]["path"]).read_text())
    current_matches = source_inventory() == frozen_inventory
    build_files = [p for arch in ("arm64", "x86_64")
                   for p in (directory / arch / "scummvm/config.log", directory / arch / "scummvm/config.mk",
                             directory / arch / "scummvm/config.h", directory / arch / "scummvm/scummvm-static")]
    archives = sorted((directory / "download").glob("*"))
    report = {
        "schema": 1, "verified_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Verified local universal app build; independent byte-reproducibility and older-macOS runtime support unproven",
        "upstream_repository": "https://github.com/alehel/Fjelltoppen", "upstream_commit": UPSTREAM,
        "build_source_checkpoint": record(checkpoint_path),
        "source_commit": checkpoint["source_commit"], "source_branch": checkpoint["source_branch"],
        "source_patch": checkpoint["source_patch"], "source_inventory": checkpoint["source_inventory"],
        "current_source_inputs_match_build": current_matches, "compiler": checkpoint["compiler"],
        "build_command": [str(SCRIPT.relative_to(ROOT)), str(directory.relative_to(ROOT))],
        "build_configuration": [record(p) for p in build_files],
        "dependency_archives": [record(p) for p in archives if p.is_file() and not p.name.endswith(".part")],
        "system_zlib": "macOS system libz, not the unused zlib archive declared for other platforms",
        "application": str(app.relative_to(ROOT)), "executable": record(executable),
        "application_files": [record(p) for p in files], "disk_image": record(dmg),
        "checks": checks, "support_files": list(required),
        "signature": "ad-hoc" if "Signature=adhoc" in checks["signature_details"]["stderr"] else "See signature_details",
        "distribution": "No release publication performed by this wrapper; signing evidence is in checks",
        "game_data": "No original game data inside the application or Git source tree",
        "artifact_privacy_verified": True,
    }
    path.write_text(json.dumps(report, indent=2) + "\n")
    public_path = directory / "public-manifest.json"
    public_manifest.write(report, public_path)
    public_audit.assert_public_artifact(public_path, forbidden_roots=(ROOT, directory, Path.home()))
    print(json.dumps({"app": str(app), "dmg": str(dmg), "sha256": report["executable"]["sha256"],
                      "manifest": str(path), "public_manifest": str(public_path)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "inspect", "adopt"))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/build/macos")
    parser.add_argument("--manifest", type=Path, help="Optional separate inspection manifest in the build directory")
    parser.add_argument("--build-log", type=Path, help="Required for explicit adoption of a known successful operator build")
    args = parser.parse_args()
    directory = output_directory(args.output)
    manifest_output(directory, args.manifest)
    if args.action == "adopt":
        if not args.build_log or not args.build_log.is_file():
            parser.error("adopt requires the known successful operator build's --build-log")
        freeze_source(directory, "Operator adoption of an already completed, source-reviewed build; not a pre-build capture", args.build_log)
    if args.action == "build":
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / ("build-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".log")
        environment = dict(os.environ, CODESIGN_IDENTITY="", NOTARY_PROFILE="")
        checkpoint = freeze_source(directory, "Source inputs frozen before invoking the packaging script")
        frozen_inventory = json.loads((ROOT / checkpoint["source_inventory"]["path"]).read_text())
        with log.open("xb") as stream:
            result = subprocess.run([str(SCRIPT), str(directory)], cwd=ROOT, env=environment,
                                    stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"Build exited {result.returncode}; inspect {log}")
        if source_inventory() != frozen_inventory:
            raise RuntimeError("Build source inputs changed while compiling; inspect the log and rebuild from fixed inputs")
        checkpoint["build_log"] = record(log)
        (directory / "build-source-checkpoint.json").write_text(json.dumps(checkpoint, indent=2) + "\n")
    inspect(directory, args.manifest)


if __name__ == "__main__":
    main()
