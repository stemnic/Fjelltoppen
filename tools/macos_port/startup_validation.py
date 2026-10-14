#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the standalone importer with private configuration and data."""

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
GAME_DIRS = ("data", "lang", "data1", "data2", "data3", "lang1", "lang2", "lang3")


def fingerprint(path):
    path = Path(path)
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": value.hexdigest()}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def file_inventory(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Unexpected symlink: " + str(root))
    if not root.exists():
        return {}
    if root.is_file():
        return {".": fingerprint(root)}
    result = {".": {"directory": True}}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected symlink: " + str(path))
        result[str(path.relative_to(root))] = {"directory": True} if path.is_dir() else fingerprint(path)
    return result


def disc_inventory(discs):
    expected = {}
    for disc in discs:
        if disc.is_symlink() or not disc.is_dir():
            raise ValueError("Disc must be an independent directory")
        for name in GAME_DIRS:
            directory = disc / name
            for relative, entry in file_inventory(directory).items():
                if "sha256" not in entry:
                    continue
                destination = (Path(name) / relative).as_posix()
                if destination in expected:
                    raise ValueError("Overlapping disc resource: " + destination)
                expected[destination] = entry
    game = {name: info for name, info in expected.items() if not Path(name).name.startswith(".")}
    if len(game) != 205 or sum(entry["bytes"] for entry in game.values()) != 1028863934:
        raise ValueError("Expected 205 files from the Norwegian Gold discs")
    return expected


def copied_inventory(path):
    return {name: value for name, value in file_inventory(path).items() if "sha256" in value}


def same_resources(actual, expected):
    return set(actual) == set(expected) and all(
        actual[name]["sha256"] == info["sha256"] and actual[name]["bytes"] == info["bytes"]
        for name, info in expected.items())


def run_case(app, directory, app_data_path, answers, timeout=120):
    directory.mkdir(parents=True)
    dialogs = directory / "dialogs"
    dialogs.mkdir()
    frame = directory / "menu.png"
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["scummvm"] = {
        "app_data_path": str(app_data_path), "app_test_answers": answers,
        "app_test_shots": str(dialogs), "logfile": str(directory / "backend.log"),
        "macos_savepath_migrated": "true", "fullscreen": "false", "confirm_exit": "false",
        "gfx_mode": "surfacesdl", "filtering": "false",
        "themepath": str(app / "Contents/Resources"), "extrapath": str(app / "Contents/Resources"),
        "start_scene": "menu", "autoshot": str(frame), "autoshot_delay": "1200", "autoshot_quit": "true",
    }
    config = directory / "config.ini"
    with config.open("x") as stream:
        cfg.write(stream)
    before = fingerprint(config)
    environment = os.environ.copy()
    environment.update(SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", SDL_RENDER_DRIVER="software")
    command = [str(app / "Contents/MacOS/fjelltoppen"), "-c", str(config),
               "--gfx-mode=surfacesdl", "--debuglevel=2"]
    log = directory / "runtime.log"
    started = time.monotonic()
    with log.open("xb") as stream:
        process = subprocess.Popen(command, cwd=directory, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
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
    result = {"command": command, "seconds": round(time.monotonic() - started, 3),
              "exit_code": code, "timed_out": timed_out, "configuration_before": before,
              "configuration_after": fingerprint(config), "runtime_log": fingerprint(log),
              "entered_scenes": re.findall(r"Entering scene ([a-z0-9_]+) \(arg", text),
              "dialogs": [fingerprint(p) for p in sorted(dialogs.glob("*.png"))],
              "frame": fingerprint(frame) if frame.is_file() else None,
              "app_data_path": str(app_data_path), "answers": answers,
              "home_unchanged": environment.get("HOME") == os.environ.get("HOME")}
    write_json(directory / "result.json", result)
    print(json.dumps({"case": directory.name, "exit": code, "scenes": result["entered_scenes"],
                      "dialog_names": [Path(p["path"]).name for p in result["dialogs"]]}), flush=True)
    return result, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--label", default="startup-001")
    parser.add_argument("--disc-one", type=Path, required=True)
    parser.add_argument("--disc-two", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.label):
        raise ValueError("Use a simple validation label")
    out = ROOT / "artifacts/validation" / args.label
    if any(p.is_symlink() for p in (ROOT / "artifacts", out.parent, out)) or out.exists():
        raise ValueError("Output must be a new independent directory")
    app = args.app.absolute()
    executable = app / "Contents/MacOS/fjelltoppen"
    if not executable.is_file() or executable.is_symlink():
        raise ValueError("Expected a packaged Fjelltoppen application")
    discs = [args.disc_one.absolute(), args.disc_two.absolute()]
    expected = disc_inventory(discs)
    defaults = [Path.home() / "Library/Application Support/Fjelltoppen",
                Path.home() / "Library/Application Support/ScummVM",
                Path.home() / "Library/Preferences/Fjelltoppen Preferences",
                Path.home() / "Library/Logs/fjelltoppen.log",
                Path.home() / "Documents/ScummVM Savegames"]
    before = {str(path): file_inventory(path) for path in defaults}
    out.mkdir(parents=True)
    app_before = file_inventory(app)
    checks, cases = {}, {}

    case = out / "welcome-exit"
    result, _ = run_case(app, case, case / "app-data", "2")
    cases[case.name] = result
    checks["welcome_exit"] = result["exit_code"] == 0 and not result["timed_out"] and not result["frame"]
    checks["welcome_rendered"] = any("welcome" in p["path"] for p in result["dialogs"])
    checks["welcome_no_import"] = not (case / "app-data/Spilldata").exists()

    case = out / "relative-rejected"
    result, text = run_case(app, case, "relative-test-data", "2", timeout=15)
    cases[case.name] = result
    checks["relative_rejected_before_write"] = (result["exit_code"] != 0 and not result["timed_out"]
        and "app_data_path must be an absolute path" in text and not (case / "relative-test-data").exists())

    case = out / "full-import"
    result, _ = run_case(app, case, case / "app-data", f"0;{discs[0]};0;{discs[1]}")
    cases[case.name] = result
    copied = copied_inventory(case / "app-data/Spilldata")
    checks["two_disc_import_matches_every_file"] = same_resources(copied, expected)
    checks["two_disc_import_enters_menu"] = result["exit_code"] == 0 and not result["timed_out"] and "menu" in result["entered_scenes"] and bool(result["frame"])
    checks["import_default_saves_private"] = (case / "app-data/Profiler").is_dir()
    write_json(case / "copied-files.json", copied)

    case = out / "resume"
    app_data = case / "app-data"
    stage_one, _ = run_case(app, case / "stage-one", app_data, f"0;{discs[0]};2")
    cases["resume-stage-one"] = stage_one
    first_files = copied_inventory(app_data / "Spilldata")
    disc_one_files = {name: entry for name, entry in expected.items()
                      if Path(entry["path"]).is_relative_to(discs[0])}
    checks["exit_after_disc_one_retains_data"] = (stage_one["exit_code"] == 0
        and not stage_one["timed_out"] and same_resources(first_files, disc_one_files))
    interrupted = app_data / "Spilldata/data/sceneindex.bin"
    if not interrupted.is_file():
        raise ValueError("Disc-one importer did not copy sceneindex.bin")
    truncated = interrupted.read_bytes()[:128]
    interrupted.write_bytes(truncated)
    truncation = fingerprint(interrupted)
    stage_two, _ = run_case(app, case / "stage-two", app_data, f"0;{discs[0]};0;{discs[1]}")
    cases["resume-stage-two"] = stage_two
    resumed_files = copied_inventory(app_data / "Spilldata")
    checks["resume_recopies_truncated_file_and_matches_every_file"] = same_resources(resumed_files, expected)
    checks["resume_enters_menu"] = stage_two["exit_code"] == 0 and not stage_two["timed_out"] and "menu" in stage_two["entered_scenes"] and bool(stage_two["frame"])
    write_json(case / "resume-files.json", {"before": first_files, "truncated_file": truncation, "after": resumed_files})

    checks["default_locations_unchanged"] = before == {str(path): file_inventory(path) for path in defaults}
    checks["app_bundle_unchanged"] = app_before == file_inventory(app)
    checks["disc_resources_unchanged"] = expected == disc_inventory(discs)
    checks["home_never_overridden"] = all(row["home_unchanged"] for row in cases.values())
    summary = {"schema": 1, "completed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "executable": fingerprint(executable), "harness": fingerprint(__file__),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "source_files": [fingerprint(ROOT / p) for p in ("base/fjelltoppen.cpp", "backends/platform/sdl/macosx/macosx.cpp")],
               "disc_files": expected, "copied_file_count": len(expected),
               "game_file_count": sum(not Path(name).name.startswith(".") for name in expected),
               "checks": checks, "passed": all(checks.values()), "cases": cases,
               "scope": "Real folder importer, two-disc merge and partial-file resume. Setup exit and restart are exercised; copy-dialog cancel button, optical images and same-size corruption repair are not tested. Default locations, app and input-resource fingerprints rechecked."}
    write_json(out / "summary.json", summary)
    print(json.dumps({"summary": str(out / "summary.json"), "passed": summary["passed"], "checks": checks}), flush=True)
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
