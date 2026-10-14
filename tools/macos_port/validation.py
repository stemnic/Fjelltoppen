#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a packaged Fjelltoppen app with independent data and private saves."""

import argparse
import collections
import configparser
import datetime
import gzip
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
VALIDATION = ROOT / "artifacts" / "validation"
GAME_DIRS = ("data", "lang", "data1", "data2", "data3", "lang1", "lang2", "lang3")
HARNESS_SOURCE = Path(__file__).read_bytes()
SCENE_PATTERN = r"Entering scene ([A-Za-z0-9_]+) \(arg (-?[0-9]+)\)"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def record(path):
    path = Path(path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Use a simple ASCII label or case name")
    return value


def output_path(label):
    path = VALIDATION / identifier(label)
    for parent in [ROOT / "artifacts", VALIDATION, path]:
        if parent.is_symlink():
            raise ValueError("Validation output must not traverse a symlink")
    return path


def scene_names():
    source = (ROOT / "engines/flaaklypa/scenedata.cpp").read_text()
    table = source.split("const SceneDef sceneDefs[] = {", 1)[1].split("\n};", 1)[0]
    names = re.findall(r'^\s*\{ "([a-z0-9_]+)",', table, re.MULTILINE)
    if len(names) != 58 or len(set(names)) != 58:
        raise ValueError("Expected exactly 58 distinct registered scenes")
    return names


def regular_files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected an independent directory: " + str(root))
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in input/evidence directory: " + str(path))
        if path.is_file():
            files.append(path)
    return files


def freeze_build_provenance(out, path, executable):
    path = Path(path)
    proof = json.loads(path.read_text())
    if proof["executable"]["sha256"] != digest(executable):
        raise ValueError("Build provenance does not identify this app executable")
    target = out / "build-provenance.json"
    if target.exists():
        raise ValueError("Existing build provenance refused")
    shutil.copy2(path, target)
    patch = out / "build-source.patch"
    source_patch = ROOT / proof["source_patch"]["path"]
    if digest(source_patch) != proof["source_patch"]["sha256"]:
        raise ValueError("Build source patch changed")
    shutil.copy2(source_patch, patch)
    snapshots = []
    for item in proof.get("source_files", []):
        source = ROOT / item["path"]
        if digest(source) != item["sha256"]:
            raise ValueError("Build source changed: " + item["path"])
        copy = out / "build-sources" / item["path"]
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copy)
        snapshots.append(record(copy))
    auxiliary = []
    for key in ("source_inventory", "build_source_checkpoint"):
        if key in proof:
            source = ROOT / proof[key]["path"]
            if digest(source) != proof[key]["sha256"]:
                raise ValueError("Build checkpoint changed: " + key)
            copy = out / source.name
            shutil.copy2(source, copy)
            auxiliary.append(record(copy))
    return {"build_provenance": record(target), "build_source_patch": record(patch),
            "build_source_files": snapshots, "build_checkpoint_files": auxiliary,
            "build_provenance_attached_utc": utc()}


def setup(label, app, disc_one, disc_two, build_manifest=None):
    out = output_path(label)
    if out.exists():
        raise ValueError("Existing validation installation refused; choose a new label")
    app = Path(app).absolute()
    app_files = regular_files(app)
    executable = app / "Contents/MacOS/fjelltoppen"
    if executable not in app_files:
        raise ValueError("Expected Fjelltoppen.app/Contents/MacOS/fjelltoppen")
    discs = [Path(disc_one).absolute(), Path(disc_two).absolute()]
    inputs = []
    destinations = set()
    for disc in discs:
        if disc.is_symlink() or not disc.is_dir():
            raise ValueError("Disc inputs must be regular directories")
        for name in GAME_DIRS:
            directory = disc / name
            if not directory.exists():
                continue
            for path in regular_files(directory):
                rel = path.relative_to(disc)
                # Ignore filesystem metadata, installers and unrelated Windows files.
                if not ((len(rel.parts) == 2 and path.suffix.lower() == ".bin") or
                        (rel.parts[:3] == ("data", "fence", "tunes") and path.suffix.lower() == ".tun")):
                    continue
                if str(rel) in destinations:
                    raise ValueError("Resource collision between discs: " + str(rel))
                destinations.add(str(rel))
                inputs.append({"source": record(path), "destination": str(rel)})
    if len(inputs) != 205 or sum(r["source"]["bytes"] for r in inputs) != 1028863934:
        raise ValueError("Expected these Norwegian Gold discs: 205 resources, 1,028,863,934 bytes")
    out.mkdir(parents=True)
    data = out / "data"
    for item in inputs:
        target = data / item["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["source"]["path"], target)
        item["copy"] = record(target)
        if item["copy"]["sha256"] != item["source"]["sha256"]:
            raise ValueError("Copied resource does not match its source")
    patch = out / "source-worktree.patch"
    patch.write_bytes(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT))
    manifest = {"schema": 1, "created_utc": utc(), "app": str(app),
                "source_patch": record(patch),
                "source_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
                "harness": record(Path(__file__)),
                "app_files": [record(p) for p in app_files], "executable": record(executable),
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "discs": [str(p) for p in discs], "resources": inputs,
                "resource_count": len(inputs), "resource_bytes": sum(r["copy"]["bytes"] for r in inputs),
                "scope": "Independent copies directly from supplied discs; no Windows executable or research checkout"}
    if build_manifest:
        manifest.update(freeze_build_provenance(out, build_manifest, executable))
    save_json(out / "installation.json", manifest)
    print(json.dumps({"installation": str(out), "resources": len(inputs), "app": str(app)}), flush=True)


def verify_installation(out):
    manifest = json.loads((out / "installation.json").read_text())
    if manifest.get("build_provenance"):
        for item in [manifest["build_provenance"], manifest["build_source_patch"], *manifest["build_source_files"], *manifest.get("build_checkpoint_files", [])]:
            if digest(item["path"]) != item["sha256"]:
                raise ValueError("Frozen build provenance changed: " + item["path"])
        proof = json.loads(Path(manifest["build_provenance"]["path"]).read_text())
        if proof["executable"]["sha256"] != manifest["executable"]["sha256"]:
            raise ValueError("Frozen build provenance describes another executable")
    app = Path(manifest["app"])
    actual = {str(p) for p in regular_files(app)}
    if actual != {r["path"] for r in manifest["app_files"]}:
        raise ValueError("App file inventory changed")
    for item in manifest["app_files"]:
        if digest(item["path"]) != item["sha256"]:
            raise ValueError("App bundle changed after setup: " + item["path"])
    actual = {str(p.relative_to(out / "data")) for p in regular_files(out / "data")}
    if actual != {r["destination"] for r in manifest["resources"]}:
        raise ValueError("Game resource inventory changed")
    for item in manifest["resources"]:
        if digest(out / "data" / item["destination"]) != item["copy"]["sha256"]:
            raise ValueError("Staged resource changed: " + item["destination"])
    return manifest


def png_stats(path):
    raw = Path(path).read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Capture is not PNG")
    pos, compressed, header = 8, bytearray(), None
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise ValueError("Truncated PNG chunk")
        size = struct.unpack_from(">I", raw, pos)[0]
        kind, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + size]
        if len(data) != size or pos + 12 + size > len(raw):
            raise ValueError("Truncated PNG payload")
        expected_crc = struct.unpack_from(">I", raw, pos + 8 + size)[0]
        if zlib.crc32(kind + data) != expected_crc:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        pos += size + 12
        if kind == b"IEND":
            break
    if header is None:
        raise ValueError("Missing PNG header")
    width, height, depth, colour, compression, filtering, interlace = header
    if depth != 8 or colour not in (2, 6) or compression or filtering or interlace:
        raise ValueError("Expected noninterlaced RGB/RGBA 8-bit engine capture")
    channels = 3 if colour == 2 else 4
    stride = width * channels
    decoded = zlib.decompress(compressed)
    if len(decoded) != height * (stride + 1):
        raise ValueError("PNG dimensions do not match decoded bytes")
    previous = bytearray(stride)
    rgb = bytearray()
    for y in range(height):
        offset = y * (stride + 1)
        mode = decoded[offset]
        row = bytearray(decoded[offset + 1:offset + 1 + stride])
        if mode not in range(5):
            raise ValueError("Unknown PNG filter")
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            above = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if mode == 1:
                prediction = left
            elif mode == 2:
                prediction = above
            elif mode == 3:
                prediction = (left + above) // 2
            elif mode == 4:
                p = left + above - corner
                distances = [abs(p - left), abs(p - above), abs(p - corner)]
                prediction = [left, above, corner][distances.index(min(distances))]
            else:
                prediction = 0
            row[x] = (row[x] + prediction) & 255
        for x in range(0, stride, channels):
            rgb.extend(row[x:x + 3])
        previous = row
    nonblack = sum(any(rgb[i:i + 3]) for i in range(0, len(rgb), 3))
    return {**record(path), "dimensions": [width, height], "nonblack_pixels": nonblack,
            "nonblack": bool(nonblack), "rgb_sha256": hashlib.sha256(rgb).hexdigest()}


def inspect_profile(path):
    raw = Path(path).read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    if len(data) < 12:
        raise ValueError("Truncated native profile")
    magic, version, fixed_size = struct.unpack_from("<III", data)
    if magic != 0xface01 or version not in (0x101, 0x102) or 12 + fixed_size > len(data):
        raise ValueError("Invalid native profile header")
    info = {"save": record(path), "decoded_bytes": len(data),
            "decoded_sha256": hashlib.sha256(data).hexdigest(), "version": version, "sections": []}
    pos = 12
    while pos < 12 + fixed_size:
        if pos + 12 > len(data):
            raise ValueError("Truncated profile chunk")
        kind, key, size = struct.unpack_from("<III", data, pos)
        payload = data[pos + 12:pos + 12 + size]
        if len(payload) != size or pos + 12 + size > 12 + fixed_size:
            raise ValueError("Invalid profile fixed chunk")
        if kind == 0x100 and size >= 36:
            info.update(name=payload[:24].split(b"\0", 1)[0].decode("cp1252"),
                        month=struct.unpack_from("<i", payload, 28)[0], day=struct.unpack_from("<i", payload, 32)[0])
        elif kind == 0x301:
            info["awards"] = list(struct.unpack("<" + "i" * (size // 4), payload))
        elif kind == 0x200:
            for offset in range(0, size - 43, 44):
                row = payload[offset:offset + 44]
                if not row[0]:
                    break
                block_offset, block_size = struct.unpack_from("<II", row, 36)
                block = data[block_offset:block_offset + block_size]
                if block_offset < 12 + fixed_size or len(block) != block_size:
                    raise ValueError("Invalid profile scene index")
                records, cursor = [], 0
                while cursor < len(block):
                    if cursor + 12 > len(block):
                        raise ValueError("Truncated profile scene record")
                    record_type, record_key, record_size = struct.unpack_from("<III", block, cursor)
                    value = block[cursor + 12:cursor + 12 + record_size]
                    if len(value) != record_size:
                        raise ValueError("Profile record extends beyond its scene")
                    entry = {"type": record_type, "key": record_key, "bytes": record_size,
                             "sha256": hashlib.sha256(value).hexdigest()}
                    if record_size == 4:
                        entry["int32"] = struct.unpack("<i", value)[0]
                    elif record_size and record_size % 4 == 0:
                        values = struct.unpack("<" + "I" * (record_size // 4), value)
                        entry["nonzero_uint32"] = sum(bool(v) for v in values)
                    records.append(entry)
                    cursor += 12 + record_size
                info["sections"].append({"records": records, "name": row[:36].split(b"\0", 1)[0].decode("ascii"),
                                         "bytes": block_size, "sha256": hashlib.sha256(block).hexdigest()})
        pos += 12 + size
    return info


def run_case(label, name, scene=None, delay=2500, timeout=30, clicks=None, keys=None,
             gui=False, audio=False, reload_from=None, interactive=False, graphics="surfacesdl", architecture=None):
    out = output_path(label)
    manifest = verify_installation(out)
    cases = out / "cases"
    if cases.is_symlink():
        raise ValueError("Cases directory must not be a symlink")
    case = cases / identifier(name)
    if case.exists() or case.is_symlink():
        raise ValueError("Existing case refused; choose a new case name")
    if graphics not in ("surfacesdl", "default") or architecture not in (None, "arm64", "x86_64"):
        raise ValueError("Unsupported graphics mode or architecture")
    if graphics == "default" and not (gui or interactive):
        raise ValueError("Default macOS graphics requires --gui")
    if scene and scene not in scene_names():
        raise ValueError("Unknown scene")
    if not 100 <= delay <= 1800000 or not delay / 1000 < timeout <= 1900:
        raise ValueError("Invalid capture delay/timeout")
    reload_files = []
    if reload_from:
        old_case = cases / identifier(reload_from)
        if old_case.is_symlink():
            raise ValueError("Reload case must not be a symlink")
        reload_files = [p for p in regular_files(old_case / "saves") if re.fullmatch(r"flaaklypa\.[0-9]{3}", p.name)]
        if not reload_files:
            raise ValueError("Reload case has no saved native profiles")
    case.mkdir(parents=True)
    saves = case / "saves"
    saves.mkdir()
    for path in reload_files:
        shutil.copy2(path, saves / path.name)
    frame = case / "frame.png"
    support = Path(manifest["app"]) / "Contents/Resources"
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["scummvm"] = {"fullscreen": "false", "savepath": str(saves), "macos_savepath_migrated": "true",
                       "filtering": "false", "confirm_exit": "false",
                       "extrapath": str(support), "themepath": str(support),
                       "app_data_path": str(case / "app-data"), "logfile": str(case / "backend.log")}
    if graphics != "default":
        cfg["scummvm"]["gfx_mode"] = graphics
    cfg["flaaklypa"] = {"engineid": "flaaklypa", "gameid": "flaaklypa", "path": str(out / "data"),
                        "savepath": str(saves), "random_seed": "1735"}
    if not interactive:
        cfg["flaaklypa"].update(autoshot=str(frame), autoshot_delay=str(delay), autoshot_quit="true")
    if scene:
        cfg["flaaklypa"]["start_scene"] = scene
    if clicks:
        cfg["flaaklypa"]["autoclick"] = clicks
    if keys:
        cfg["flaaklypa"]["autokey"] = keys
    config = case / "config.ini"
    with config.open("x") as stream:
        cfg.write(stream)
    config_before = record(config)
    (case / "validation-source.py").write_bytes(HARNESS_SOURCE)
    environment = os.environ.copy()
    environment.update(SDL_AUDIODRIVER="dummy", SDL_VIDEODRIVER="dummy", SDL_RENDER_DRIVER="software", LC_ALL="C")
    if gui or interactive:
        environment["SDL_VIDEODRIVER"] = "cocoa"
    if graphics == "default":
        environment.pop("SDL_RENDER_DRIVER")
    if audio:
        environment.pop("SDL_AUDIODRIVER")
    command = [manifest["executable"]["path"], "-c", str(config), "--debuglevel=2"]
    if graphics != "default":
        command.append("--gfx-mode=" + graphics)
    command.append("flaaklypa")
    if architecture:
        command = ["/usr/bin/arch", "-" + architecture, *command]
    log = case / "runtime.log"
    started, before = utc(), time.monotonic()
    with log.open("xb") as stream:
        process = subprocess.Popen(command, cwd=case, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        if interactive:
            result = {"kind": "Interactive launch", "pid": process.pid, "command": command,
                      "started_utc": started, "savepath": str(saves), "log": str(log), "configuration": config_before}
            save_json(case / "launch.json", result)
            print(json.dumps(result), flush=True)
            return result
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
    entries = re.findall(SCENE_PATTERN, text)
    before_capture = re.findall(SCENE_PATTERN, text.split("Wrote screenshot ", 1)[0])
    result = {"schema": 1, "name": name, "started_utc": started, "completed_utc": utc(),
              "seconds": round(time.monotonic() - before, 3), "requested_scene": scene,
              "graphics": graphics, "requested_architecture": architecture, "host_architecture": platform.machine(), "scene_shortcut": bool(scene),
              "entered_scenes": [n for n, _ in entries], "scene_at_capture": before_capture[-1][0] if before_capture else None,
              "exit_code": code, "timed_out": timed_out, "command": command, "executable": manifest["executable"],
              "harness_source": record(case / "validation-source.py"), "configuration_before": config_before,
              "configuration_after": record(config), "log": record(log),
              "backend_log": record(case / "backend.log") if (case / "backend.log").is_file() else None,
              "environment": {k: environment.get(k) for k in ("SDL_VIDEODRIVER", "SDL_AUDIODRIVER", "SDL_RENDER_DRIVER")},
              "input": {"autoclick": clicks, "autokey": keys}, "reload_from": reload_from,
              "frame": png_stats(frame) if frame.is_file() else None,
              "warnings": [line for line in text.splitlines() if "WARNING:" in line or "ERROR:" in line],
              "saves": [inspect_profile(p) for p in regular_files(saves) if re.fullmatch(r"flaaklypa\.[0-9]{3}", p.name)],
              "scope": "Packaged native app checkpoint. Scene entry shortcuts do not prove normal progression; audio backend selection does not prove audible quality."}
    save_json(case / "result.json", result)
    print(json.dumps({"case": name, "exit": code, "timeout": timed_out, "scenes": result["entered_scenes"],
                      "frame": result["frame"] and result["frame"]["dimensions"], "nonblack": result["frame"] and result["frame"]["nonblack"],
                      "profiles": [r.get("name") for r in result["saves"]]}), flush=True)
    return result


def behavioral_checks(out, rows):
    by_name = {r["name"]: r for r in rows}

    def log(name):
        return Path(by_name[name]["log"]["path"]).read_text(errors="replace")

    def profile_record(name, scene, kind, key):
        for profile in by_name[name]["saves"]:
            for section in profile["sections"]:
                if section["name"] == scene:
                    for item in section.get("records", []):
                        if item["type"] == kind and item["key"] == key:
                            return item
        raise ValueError("Missing persisted record for " + name)

    checks = {}
    if "fresh-profile" in by_name:
        text = log("fresh-profile")
        passed = ("returned 1: 'MacPort'" in text and "Calendar returned 1: month 0, day 1" in text and
                  any(p.get("name") == "MacPort" for p in by_name["fresh-profile"]["saves"]))
        checks["real_profile_creation"] = {"passed": passed, "case": "fresh-profile", "name": "MacPort", "birthday": "January 1", "profile_import": False}
    for name, expected in [("normal-boot", ["logo", "birthday", "menu"]),
                           ("normal-story-yard", ["logo", "birthday", "menu", "intro", "yard"]),
                           ("story-resume-yard", ["menu", "yard"])]:
        if name in by_name:
            checks[name] = {"passed": by_name[name]["entered_scenes"] == expected,
                            "case": name, "entered": by_name[name]["entered_scenes"], "scene_shortcut": by_name[name]["scene_shortcut"]}
    if "cocoa-default-graphics" in by_name:
        row, text = by_name["cocoa-default-graphics"], log("cocoa-default-graphics")
        checks["default_macOS_graphics"] = {"passed": row["graphics"] == "default" and "creating OpenGL graphics manager" in text and 'Using SDL Video Driver "cocoa"' in text,
                                           "case": row["name"], "graphics_override": False,
                                           "native_audio_backend_selected": 'Using SDL Audio Driver "coreaudio"' in text,
                                           "audio_quality": "Not assessed"}
    if "rosetta-x86-menu" in by_name:
        row = by_name["rosetta-x86-menu"]
        checks["x86_64_slice_runtime"] = {"passed": row["command"][:2] == ["/usr/bin/arch", "-x86_64"] and row["exit_code"] == 0,
                                          "case": row["name"], "host": row["host_architecture"], "scope": "Rosetta on Apple Silicon; physical Intel Mac untested"}
    if "menu-race-locked" in by_name:
        text = log("menu-race-locked")
        checks["normal_race_gate"] = {"passed": "Message box (1): 'Bilspillet'" in text and by_name["menu-race-locked"]["entered_scenes"] == ["menu"],
                                      "case": "menu-race-locked", "result": "Fresh profile is told to build more of Il Tempo Gigante"}
    if "racing-held-up" in by_name:
        text = log("racing-held-up").split("Wrote screenshot ", 1)[0]
        samples = [(int(x), int(y), float(speed)) for x, y, speed in re.findall(r"Racing: car 0 .*? at\s+(-?[0-9]+),\s*(-?[0-9]+) angle\s+[-0-9.]+ speed\s+([-0-9.]+)", text)]
        checks["real_racing_acceleration"] = {"passed": "Racing: start" in text and bool(samples) and max(s[2] for s in samples) > 0 and samples[0][:2] != samples[-1][:2],
                                             "case": "racing-held-up", "position_and_engine_speed_samples": samples,
                                             "scope": "Help dismissal, natural countdown and held Up; direct entry bypasses unlock, no completed lap or race finish"}
    if "sliding-complete" in by_name and "sliding-completed-reload" in by_name:
        played = profile_record("sliding-complete", "sliding", 0x402, 0)
        reloaded = profile_record("sliding-completed-reload", "sliding", 0x402, 0)
        score = profile_record("sliding-complete", "sliding", 0x401, 0)
        reload_score = profile_record("sliding-completed-reload", "sliding", 0x401, 0)
        checks["sliding_real_completion_and_reload"] = {"passed": "Sliding: 1 motives solved" in log("sliding-complete") and played["int32"] == 3 and played["sha256"] == reloaded["sha256"] and score["sha256"] == reload_score["sha256"],
                                                        "cases": ["sliding-complete", "sliding-completed-reload"],
                                                        "ordinary_mouse_moves": len(by_name["sliding-complete"]["input"]["autoclick"].split(";")),
                                                        "persisted_solved_state": played["int32"], "highscore_record_preserved": score["sha256"] == reload_score["sha256"],
                                                        "scope": "One complete 3x3 picture solved through legal adjacent tile clicks; no solve or award helper"}
    if "trivia-full-round" in by_name and "trivia-completed-reload" in by_name:
        text = log("trivia-full-round")
        finishes = [{"correct": int(correct), "questions": int(total), "score": int(score)} for correct, total, score in re.findall(r"Gametrivia: game over, ([0-9]+) of ([0-9]+) right, score ([0-9]+)", text)]
        history = profile_record("trivia-full-round", "gametrivia", 0x402, 0)
        reload_history = profile_record("trivia-completed-reload", "gametrivia", 0x402, 0)
        score = profile_record("trivia-full-round", "gametrivia", 0x401, 0)
        reload_score = profile_record("trivia-completed-reload", "gametrivia", 0x401, 0)
        checks["trivia_real_completion_and_reload"] = {"passed": bool(finishes) and all(r["questions"] == 32 for r in finishes) and history["sha256"] == reload_history["sha256"] and score["sha256"] == reload_score["sha256"],
                                                       "cases": ["trivia-full-round", "trivia-completed-reload"], "completed_games": finishes,
                                                       "shown_questions_persisted": history["nonzero_uint32"], "question_count": history["bytes"] // 4,
                                                       "history_record_preserved": history["sha256"] == reload_history["sha256"],
                                                       "highscore_record_preserved": score["sha256"] == reload_score["sha256"],
                                                       "scope": "Ordinary repeated mouse drags, natural animations and Return for result dialogs; mixed answers. Repeated input starts another game before capture; no in-progress round resume claimed."}
    if "anaglyph-pingpong" in by_name:
        checks["anaglyph_activity"] = {"passed": "Anaglyph: activity 15" in log("anaglyph-pingpong"), "case": "anaglyph-pingpong",
                                       "scope": "Chooser Return enters ping-pong activity in existing JPEG-disabled release configuration"}
    if any(not check["passed"] for check in checks.values()):
        raise ValueError("A behavioral checkpoint failed: " + repr([name for name, check in checks.items() if not check["passed"]]))
    return checks


def report(label):
    out = output_path(label)
    manifest = verify_installation(out)
    cases = out / "cases"
    if cases.is_symlink():
        raise ValueError("Cases directory must not be a symlink")
    rows = []
    for path in sorted(cases.glob("*/result.json")):
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Case evidence must not be a symlink")
        row = json.loads(path.read_text())
        for item in [row["log"], row["configuration_after"], row["harness_source"], row.get("frame"), row.get("backend_log")]:
            if item and digest(item["path"]) != item["sha256"]:
                raise ValueError("Frozen case artifact changed: " + item["path"])
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(path.parent / "config.ini")
        if any(cfg[d]["savepath"] != str(path.parent / "saves") for d in ("scummvm", "flaaklypa")):
            raise ValueError("Case save paths must be private")
        if cfg["scummvm"].get("macos_savepath_migrated") != "true":
            raise ValueError("Case must disable macOS default save migration")
        permitted = {"engineid", "gameid", "path", "savepath", "random_seed", "autoshot", "autoshot_delay", "autoshot_quit", "start_scene", "autoclick", "autokey", "description", "guioptions", "language", "platform"}
        if set(cfg["flaaklypa"]) - permitted:
            raise ValueError("Unreviewed game helper option in case")
        for saved in row["saves"]:
            if digest(saved["save"]["path"]) != saved["save"]["sha256"]:
                raise ValueError("Frozen case save changed")
        if cfg["scummvm"].get("logfile") != str(path.parent / "backend.log"):
            raise ValueError("Backend log must be private")
        rows.append(row)
    matrix = [r for r in rows if r["name"].startswith("matrix-")]
    if len(matrix) != 58 or {r["requested_scene"] for r in matrix} != set(scene_names()):
        raise ValueError("Report requires all 58 matrix scenes exactly once")
    if any(r["exit_code"] or r["timed_out"] or r["requested_scene"] not in r["entered_scenes"] or
           not (r["frame"] and r["frame"]["nonblack"]) for r in matrix):
        raise ValueError("Matrix has failed, timed out or blank captures; inspect individual results")
    summary = {"schema": 1, "collected_utc": utc(), "installation": record(out / "installation.json"),
               "app": manifest["app"], "executable": manifest["executable"],
               "build_provenance": manifest.get("build_provenance"), "assets": {"count": manifest["resource_count"], "bytes": manifest["resource_bytes"], "all_fingerprints_rechecked": True},
               "matrix": {"cases": len(matrix), "exit_zero": sum(r["exit_code"] == 0 for r in matrix),
                          "timeouts": sum(r["timed_out"] for r in matrix), "nonblack": sum(bool(r["frame"] and r["frame"]["nonblack"]) for r in matrix),
                          "capture_redirects": [{"requested": r["requested_scene"], "captured": r["scene_at_capture"]} for r in matrix if r["requested_scene"] != r["scene_at_capture"]]},
               "case_count": len(rows), "exit_zero": sum(r["exit_code"] == 0 for r in rows),
               "warnings": dict(collections.Counter(w for r in rows for w in r["warnings"])), "cases": rows,
               "behavioral_checks": behavioral_checks(out, rows),
               "limitations": ["Direct scene entries bypass normal unlock gates", "No claim of full campaign completion or original binary equivalence", "Dummy audio in default bounded cases; native backend selection does not assess audible output"]}
    save_json(out / "report.json", summary)
    print(json.dumps({"report": str(out / "report.json"), "matrix": summary["matrix"], "cases": len(rows)}), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="packaged-001")
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("setup")
    prepare.add_argument("--app", required=True)
    prepare.add_argument("--build-manifest")
    prepare.add_argument("--disc-one", required=True)
    prepare.add_argument("--disc-two", required=True)
    for action in ("run-case", "play"):
        case = sub.add_parser(action)
        case.add_argument("--name", required=True)
        case.add_argument("--scene")
        case.add_argument("--delay-ms", type=int, default=2500)
        case.add_argument("--timeout", type=float, default=30)
        case.add_argument("--clicks")
        case.add_argument("--keys")
        case.add_argument("--gui", action="store_true")
        case.add_argument("--graphics", choices=("surfacesdl", "default"), default="surfacesdl")
        case.add_argument("--architecture", choices=("arm64", "x86_64"))
        case.add_argument("--audio", action="store_true")
        case.add_argument("--reload-from")
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--reload-from", required=True)
    matrix.add_argument("--delay-ms", type=int, default=1500)
    matrix.add_argument("--timeout", type=float, default=30)
    sub.add_parser("report")
    args = parser.parse_args()
    try:
        if args.action == "setup":
            setup(args.label, args.app, args.disc_one, args.disc_two, args.build_manifest)
        elif args.action in ("run-case", "play"):
            run_case(args.label, args.name, args.scene, args.delay_ms, args.timeout, args.clicks, args.keys,
                     args.gui, args.audio, args.reload_from, args.action == "play", args.graphics, args.architecture)
        elif args.action == "matrix":
            for name in scene_names():
                run_case(args.label, "matrix-" + name, scene=name, delay=args.delay_ms,
                         timeout=args.timeout, reload_from=args.reload_from)
        else:
            report(args.label)
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, zlib.error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
