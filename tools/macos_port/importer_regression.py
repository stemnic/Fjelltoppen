#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the native importer using generated files, without game assets."""

import argparse
import configparser
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
CHUNK_SIZE = 256 * 1024
FIXTURE_SIZES = {
    "data/sceneindex.bin": 3 * CHUNK_SIZE + 19,
    "data/boundary.bin": CHUNK_SIZE,
    "data/empty.bin": 0,
    "lang/common.bin": 2 * CHUNK_SIZE,
    "data1/synthetic.bin": 257,
    "data2/synthetic.bin": 65537,
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected symlink in regression files")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": digest(path),
            }
    return result


def protected_state():
    home = Path.home()
    paths = (home / "Library/Application Support/Fjelltoppen",
             home / "Library/Application Support/ScummVM",
             home / "Library/Preferences/Fjelltoppen Preferences",
             home / "Library/Logs/fjelltoppen.log",
             home / "Documents/ScummVM Savegames")
    result = []
    for root in paths:
        if root.is_symlink():
            raise ValueError("Unexpected symlink in a default application location")
        if not root.exists():
            result.append(None)
        elif root.is_file():
            result.append({"bytes": root.stat().st_size, "sha256": digest(root)})
        else:
            result.append({"files": inventory(root), "directories": sorted(
                path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir())})
    return result


def create_fixture(directory):
    # These marker names identify CD 1; the bytes are deliberately artificial.
    pattern = bytes((index * 37 + 11) % 251 for index in range(4096))
    for name, size in FIXTURE_SIZES.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            remaining = size
            while remaining:
                block = pattern[:min(remaining, len(pattern))]
                stream.write(block)
                remaining -= len(block)


def run_case(app, directory, app_data, fixture, timeout):
    directory.mkdir()
    dialogs = directory / "dialogs"
    dialogs.mkdir()
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["scummvm"] = {
        "app_data_path": str(app_data),
        "app_test_answers": f"0;{fixture};2",
        "app_test_shots": str(dialogs),
        "logfile": str(directory / "backend.log"),
        "macos_savepath_migrated": "true",
        "fullscreen": "false", "confirm_exit": "false",
        "gfx_mode": "surfacesdl", "filtering": "false",
        "themepath": str(app / "Contents/Resources"),
        "extrapath": str(app / "Contents/Resources"),
    }
    config = directory / "config.ini"
    with config.open("x") as stream:
        cfg.write(stream)
    environment = os.environ.copy()
    environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                       SDL_RENDER_DRIVER="software")
    command = [str(app / "Contents/MacOS/fjelltoppen"), "--config=" + str(config),
               "--gfx-mode=surfacesdl", "--debuglevel=2"]
    started = time.monotonic()
    log = directory / "runtime.log"
    with log.open("xb") as stream:
        process = subprocess.Popen(command, cwd=directory, env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                code = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
    text = log.read_text(errors="replace")
    shots = sorted(path.name for path in dialogs.glob("*.png"))
    result = {"exit_code": code, "timed_out": timed_out,
              "seconds": round(time.monotonic() - started, 3),
              "dialogs": shots, "runtime_log_sha256": digest(log),
              "no_game_started": not re.search(r"Entering scene [a-z0-9_]+", text),
              "home_unchanged": environment.get("HOME") == os.environ.get("HOME")}
    result["reached_cd_two_prompt"] = any(name.endswith("-next.png") for name in shots)
    write_json(directory / "result.json", result)
    print(json.dumps({"case": directory.name, **result}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--label", default="importer-regression-001")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.label):
        raise ValueError("Use a simple validation label")
    if args.timeout <= 0:
        raise ValueError("Timeout must be positive")
    app = args.app.absolute()
    executable = app / "Contents/MacOS/fjelltoppen"
    if not executable.is_file() or executable.is_symlink():
        raise ValueError("Expected a packaged Fjelltoppen application")
    out = ROOT / "artifacts/validation" / args.label
    if any(path.is_symlink() for path in (ROOT / "artifacts", out.parent, out)) or out.exists():
        raise ValueError("Output must be a new independent directory")
    out.mkdir(parents=True)
    fixture = out / "synthetic-cd-one"
    create_fixture(fixture)
    expected = inventory(fixture)
    app_data = out / "app-data"
    copied = app_data / "Spilldata"
    executable_before = digest(executable)
    defaults_before = protected_state()
    cases, checks = {}, {}

    result = run_case(app, out / "first-import", app_data, fixture, args.timeout)
    cases["first_import"] = result
    checks["first_import_matches_synthetic_source"] = inventory(copied) == expected
    if not checks["first_import_matches_synthetic_source"]:
        raise ValueError("The first import did not produce the synthetic fixture")

    # A deliberately old timestamp makes an unnecessary rewrite observable.
    unchanged_time = 946684800000000000
    for name in expected:
        os.utime(copied / name, ns=(unchanged_time, unchanged_time))
    before = {name: (copied / name).stat().st_mtime_ns for name in expected}
    result = run_case(app, out / "unchanged-import", app_data, fixture, args.timeout)
    cases["unchanged_import"] = result
    checks["identical_files_preserve_mtime"] = all(
        (copied / name).stat().st_mtime_ns == timestamp for name, timestamp in before.items())
    checks["identical_files_remain_exact"] = inventory(copied) == expected

    damaged = copied / "data/sceneindex.bin"
    with damaged.open("r+b") as stream:
        stream.seek(2 * CHUNK_SIZE + 7)
        value = stream.read(1)
        stream.seek(-1, os.SEEK_CUR)
        stream.write(bytes((value[0] ^ 0x80,)))
    checks["same_size_damage_precondition"] = (
        damaged.stat().st_size == expected["data/sceneindex.bin"]["bytes"]
        and digest(damaged) != expected["data/sceneindex.bin"]["sha256"])
    result = run_case(app, out / "same-size-repair", app_data, fixture, args.timeout)
    cases["same_size_repair"] = result
    checks["same_size_damage_repaired"] = inventory(copied) == expected
    checks["repair_preserves_other_files"] = all(
        (copied / name).stat().st_mtime_ns == timestamp for name, timestamp in before.items()
        if name != "data/sceneindex.bin")

    with damaged.open("r+b") as stream:
        stream.truncate(128)
    checks["shortened_file_precondition"] = damaged.stat().st_size == 128
    result = run_case(app, out / "short-file-repair", app_data, fixture, args.timeout)
    cases["short_file_repair"] = result
    checks["shortened_file_repaired"] = inventory(copied) == expected
    checks["all_runs_exit_at_cd_two_prompt"] = all(
        case["exit_code"] == 0 and not case["timed_out"] and case["reached_cd_two_prompt"]
        for case in cases.values())
    checks["no_game_assets_or_engine_run"] = all(case["no_game_started"] for case in cases.values())
    checks["source_fixture_unchanged"] = inventory(fixture) == expected
    checks["executable_unchanged"] = digest(executable) == executable_before
    checks["default_locations_unchanged"] = protected_state() == defaults_before
    checks["home_never_overridden"] = all(case["home_unchanged"] for case in cases.values())
    summary = {"schema": 1, "completed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "executable_sha256": executable_before, "harness_sha256": digest(__file__),
               "current_source_sha256": digest(ROOT / "base/fjelltoppen.cpp"),
               "fixture_files": expected, "checks": checks, "cases": cases,
               "passed": all(checks.values()),
               "scope": "Generated CD1 directory import, unchanged-file preservation, same-size damage repair, shortened-file repair and CD2 prompt. No game assets, gameplay, copy cancellation or injected source-read failures."}
    write_json(out / "summary.json", summary)
    print(json.dumps({"summary": str(out / "summary.json"),
                      "passed": summary["passed"], "checks": checks}), flush=True)
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
