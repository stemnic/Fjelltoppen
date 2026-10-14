#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project private build evidence into an allowlisted public package manifest."""

import json
from pathlib import Path, PurePosixPath
import re

UPSTREAM = "https://github.com/alehel/Fjelltoppen"
SUPPORT_FILES = (
    "scummremastered.zip", "scummmodern.zip", "scummclassic.zip", "gui-icons.dat",
    "shaders.dat", "translations.dat", "fonts.dat", "helpdialog.zip",
)
CHECKS = ("architectures", "dynamic_dependencies", "deployment_targets",
          "code_signature", "signature_details", "disk_image", "disk_image_contents")


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Expected a repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Public package paths must be relative and cannot escape their root")
    return path


def fingerprint(item, name):
    value = item.get("sha256")
    size = item.get("bytes")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid package fingerprint")
    if type(size) is not int or size < 0:
        raise ValueError("Invalid package file size")
    return {"path": str(name), "bytes": size, "sha256": value}


def commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Invalid source commit")
    return value


def compiler(value):
    lines = value.splitlines()
    version = re.fullmatch(r"(Apple clang version [0-9.]+)(?: \(clang-[0-9.]+\))?", lines[0]) if lines else None
    targets = [line.removeprefix("Target: ") for line in lines if line.startswith("Target: ")]
    if not version or len(targets) != 1 or not re.fullmatch(r"(?:arm64|aarch64|x86_64)-apple-darwin[0-9.]+", targets[0]):
        raise ValueError("Unexpected compiler version or target")
    return {"version": version.group(1), "target": targets[0]}


def project(report):
    """Copy only structured package facts; never copy commands or tool output."""
    if report.get("upstream_repository") != UPSTREAM:
        raise ValueError("Unexpected upstream repository")
    if report.get("artifact_privacy_verified") is not True:
        raise ValueError("Application artifacts have not passed the privacy scan")
    application = relative_path(report["application"])
    if application.name != "Fjelltoppen.app":
        raise ValueError("Unexpected application name")
    executable = relative_path(report["executable"]["path"])
    if executable != application / "Contents/MacOS/fjelltoppen":
        raise ValueError("Unexpected executable path")
    image = relative_path(report["disk_image"]["path"])
    if image.parent != application.parent or not re.fullmatch(r"Fjelltoppen-[A-Za-z0-9][A-Za-z0-9.+_-]*-macos\.dmg", image.name):
        raise ValueError("Unexpected disk image name")
    files = []
    for item in report["application_files"]:
        path = relative_path(item["path"])
        try:
            name = path.relative_to(application)
        except ValueError as error:
            raise ValueError("Package file lies outside the application") from error
        if not name.parts or name.parts[0] != "Contents":
            raise ValueError("Unexpected application file")
        files.append(fingerprint(item, name))
    names = {item["path"] for item in files}
    if len(names) != len(files) or not all("Contents/Resources/" + name in names for name in SUPPORT_FILES):
        raise ValueError("Duplicate package entries or missing support files")
    if "Contents/Resources/licenses/COPYING" not in names:
        raise ValueError("Missing packaged GPL license")
    checks = report["checks"]
    if any(type(checks[name]["exit_code"]) is not int or checks[name]["exit_code"] != 0 for name in CHECKS):
        raise ValueError("Package inspection has unsuccessful checks")
    architectures = checks["architectures"]["stdout"].split()
    if set(architectures) != {"arm64", "x86_64"} or len(architectures) != 2:
        raise ValueError("Expected a universal ARM64/x86_64 package")
    minimums = re.findall(r"^\s+minos ([0-9.]+)$", checks["deployment_targets"]["stdout"], re.MULTILINE)
    sdks = re.findall(r"^\s+sdk ([0-9.]+)$", checks["deployment_targets"]["stdout"], re.MULTILINE)
    if minimums != ["11.0", "11.0"] or len(sdks) != 2:
        raise ValueError("Unexpected deployment targets")
    dependencies = re.findall(r"^\s+(.+?) \(compatibility version", checks["dynamic_dependencies"]["stdout"], re.MULTILINE)
    if not dependencies or any(not re.fullmatch(r"/(?:System/Library|usr/lib)/[A-Za-z0-9_./+-]+", path)
                               or ".." in PurePosixPath(path).parts for path in dependencies):
        raise ValueError("Non-system or malformed dynamic dependency")
    archives = []
    for item in report["dependency_archives"]:
        name = relative_path(item["path"]).name
        if not re.fullmatch(r"(?:SDL2|freetype|libpng|bzip2)-[0-9.]+\.tar\.(?:gz|xz)", name):
            raise ValueError("Unexpected dependency archive")
        archives.append(fingerprint(item, name))
    details = checks["signature_details"]["stderr"]
    signature = "ad-hoc" if "Signature=adhoc" in details else "Developer ID" if "Authority=Developer ID Application:" in details else "other"
    result = {
        "schema": 1,
        "scope": "Sanitized package facts; full build logs and local provenance are excluded.",
        "source": {"upstream_repository": UPSTREAM, "upstream_commit": commit(report["upstream_commit"]),
                   "commit": commit(report["source_commit"])},
        "compiler": compiler(report["compiler"]),
        "application": application.name, "executable": fingerprint(report["executable"], "Contents/MacOS/fjelltoppen"),
        "application_files": files, "disk_image": fingerprint(report["disk_image"], image.name),
        "architectures": sorted(architectures), "macos_minimum": "11.0", "sdk_versions": sorted(set(sdks)),
        "dynamic_dependencies": sorted(set(dependencies)), "support_files": list(SUPPORT_FILES),
        "dependency_archives": archives, "zlib": "macOS system library",
        "license": "GPL-3.0-or-later", "signature": signature,
        "checks": {name: True for name in (*CHECKS, "support_files", "license", "artifact_privacy")},
        "limitations": ["macOS minimum is declared; older macOS runtime support is not established.",
                        "Signature verification does not establish notarization or Gatekeeper acceptance.",
                        "Independent byte reproducibility is not established."],
    }
    if type(report.get("current_source_inputs_match_build")) is bool:
        result["source"]["current_inputs_match_build"] = report["current_source_inputs_match_build"]
    return result


def write(report, path):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Public manifest output must be a regular file")
    path.write_text(json.dumps(project(report), ensure_ascii=False, indent=2) + "\n")
