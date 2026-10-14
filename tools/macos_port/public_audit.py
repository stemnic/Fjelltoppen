#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check local additions, their Git history and unpacked distribution files."""

import argparse
import difflib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

UPSTREAM = "dbbf9d9a2463078bfe3eb8d94de68edd949b7f6f"
ROOT = Path(__file__).resolve().parents[2]
CHUNK_SIZE = 1024 * 1024
MAX_ARCHIVE_DEPTH = 4
MAX_ARCHIVE_ENTRIES = 10000
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
PRIVATE_PATH = re.compile(
    rb"(?:/" + b"Users" + rb"/|/" + b"home" + rb"/|[A-Za-z]:[\\/]{1,2}[Uu][Ss][Ee][Rr][Ss][\\/]{1,2})"
)
SECRET = re.compile(
    rb"(?:-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----|"
    rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    rb"https?://[^/\s]+:[^/@\s]+@)"
)
EMAIL = re.compile(rb"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PRIVATE_DIRECTORIES = {"artifacts", "dist", "private", "spilldata", "profiler", "captures",
                       "screenshots", "fgpgull1", "fgpgull2", "cd-cops"}
GAME_SUFFIXES = {".pfl", ".bin", ".tun", ".smk", ".bik", ".iso", ".mdf", ".mds", ".nrg"}
LOCAL_SUFFIXES = GAME_SUFFIXES | {".exe", ".dll", ".dmg", ".p12", ".pfx", ".key"}
SUPPORT_DATA = {"gui-icons.dat", "shaders.dat", "translations.dat", "fonts.dat"}


class PublicationError(ValueError):
    pass


def private_markers(forbidden_roots=()):
    markers = [b"/" + b"Users/", b"/" + b"home/"]
    for root in forbidden_roots:
        marker = os.fsencode(os.fspath(root)).rstrip(b"/\\")
        if len(marker) < 4 or len(marker) > 16384:
            raise ValueError("Forbidden roots must name specific directories")
        markers.append(marker)
    return tuple(set(markers + [m.decode("utf-8").encode(encoding)
                               for m in markers for encoding in ("utf-16-le", "utf-16-be")]))


def contains_private(data, markers):
    return PRIVATE_PATH.search(data.replace(b"\0", b"")) or any(marker in data for marker in markers)


def artifact_name_error(path):
    name = path.name.lower()
    if name == ".ds_store" or path.suffix.lower() in GAME_SUFFIXES:
        return "original game data or filesystem metadata"
    if path.suffix.lower() in (LOCAL_SUFFIXES - GAME_SUFFIXES - {".dmg"}):
        return "executable or private signing material"
    if path.suffix.lower() == ".dat" and name not in SUPPORT_DATA:
        return "unexpected data archive"
    if any(part.lower() in PRIVATE_DIRECTORIES - {"artifacts", "dist"} for part in path.parts):
        return "private data directory"
    return None


class ArchiveBudget:
    def __init__(self):
        self.entries = 0
        self.expanded = 0

    def consume(self, count):
        self.expanded += count
        if self.expanded > MAX_EXPANDED_BYTES:
            raise PublicationError("Distribution archives exceed the expanded byte limit")


def scan_stream(stream, label, markers, budget=None, retain_archive=False):
    overlap = max(128, *(len(marker) for marker in markers))
    previous, nested = b"", None
    first = True
    try:
        while block := stream.read(CHUNK_SIZE):
            if first and retain_archive and (block.startswith(ZIP_HEADERS) or label.lower().endswith(".zip")):
                nested = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            first = False
            if budget:
                budget.consume(len(block))
            data = previous + block
            if contains_private(data, markers):
                raise PublicationError(f"Distribution file {label} contains a private path")
            if SECRET.search(data):
                raise PublicationError(f"Distribution file {label} contains a possible secret")
            if nested:
                nested.write(block)
            previous = data[-overlap:]
        if nested:
            nested.seek(0)
        return nested
    except BaseException:
        if nested:
            nested.close()
        raise


def scan_archive(stream, label, markers, budget, depth=1):
    if depth > MAX_ARCHIVE_DEPTH:
        raise PublicationError("Distribution archives exceed the nesting limit")
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            budget.entries += len(entries)
            if budget.entries > MAX_ARCHIVE_ENTRIES:
                raise PublicationError("Distribution archives exceed the entry limit")
            names = set()
            for info in entries:
                name = info.orig_filename
                parts = name.rstrip("/").split("/")
                if (not name or "\0" in name or "\\" in name or PurePosixPath(name).is_absolute()
                        or re.match(r"^[A-Za-z]:", name) or any(part in ("", ".", "..") for part in parts)):
                    raise PublicationError(f"Distribution archive {label} contains an unsafe member path")
                normalized = str(PurePosixPath(name))
                if normalized in names:
                    raise PublicationError(f"Distribution archive {label} contains duplicate member paths")
                names.add(normalized)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise PublicationError(f"Distribution archive {label} contains a symlink or special file")
                problem = artifact_name_error(PurePosixPath(name))
                if problem:
                    raise PublicationError(f"Distribution archive {label} contains {problem}")
                if info.flag_bits & 1:
                    raise PublicationError(f"Distribution archive {label} contains an encrypted member")
                if info.file_size > MAX_EXPANDED_BYTES - budget.expanded:
                    raise PublicationError("Distribution archives exceed the expanded byte limit")
                if info.is_dir():
                    continue
                with archive.open(info) as member:
                    # Do not expose an unchecked archive member's name in an error.
                    nested = scan_stream(member, "member.zip" if name.lower().endswith(".zip") else "archive member",
                                         markers, budget, retain_archive=True)
                if nested:
                    with nested:
                        scan_archive(nested, label, markers, budget, depth + 1)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise PublicationError(f"Distribution archive {label} could not be inspected") from error


def assert_public_artifact(path, forbidden_roots=()):
    """Reject private strings and game resources in a file or unpacked app.

    ZIP resources are inspected recursively with shared expansion limits.
    Other compressed containers, including disk images, require extraction.
    """
    path = Path(path)
    if path.is_symlink() or not path.exists():
        raise PublicationError("Distribution input must be an independent file or directory")
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    markers = private_markers(forbidden_roots)
    budget = ArchiveBudget()
    for item in files:
        if item.is_symlink():
            raise PublicationError("Distribution contains a symlink")
        if not item.is_file():
            continue
        problem = artifact_name_error(item.relative_to(path) if path.is_dir() else Path(item.name))
        if problem:
            raise PublicationError(f"Distribution file {item.name}: {problem}")
        with item.open("rb") as stream:
            scan_stream(stream, item.name, markers)
            stream.seek(0)
            header = stream.read(4)
            stream.seek(0)
            if header.startswith(ZIP_HEADERS) or item.suffix.lower() == ".zip":
                scan_archive(stream, item.name, markers, budget)


def source_name_error(name):
    path = Path(name)
    if any(part.lower() in PRIVATE_DIRECTORIES for part in path.parts):
        return "private development directory"
    if path.name.lower() == ".ds_store" or path.suffix.lower() in LOCAL_SUFFIXES:
        return "private asset, executable or distribution file"
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return "private environment file"
    return None


def audit_source_blob(name, data, baseline=b"", context="worktree"):
    findings = []
    problem = source_name_error(name)
    if problem:
        return [{"context": context, "path": name, "problem": problem}]
    if b"\0" in data:
        return [{"context": context, "path": name, "problem": "new or changed binary source requires explicit review"}]
    old, new = baseline.splitlines(), data.splitlines()
    markers = private_markers()
    for operation, _, _, start, end in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if operation not in ("insert", "replace"):
            continue
        for index in range(start, end):
            if contains_private(new[index], markers):
                findings.append({"context": context, "path": name, "line": index + 1,
                                 "problem": "private absolute user path"})
            if SECRET.search(new[index]):
                findings.append({"context": context, "path": name, "line": index + 1,
                                 "problem": "possible credential or private key"})
            for email in EMAIL.finditer(new[index]):
                domain = email.group(1).decode().lower()
                if domain not in ("users.noreply.github.com", "github.com", "example.com", "example.org", "example.net") and not domain.endswith(".test"):
                    findings.append({"context": context, "path": name, "line": index + 1,
                                     "problem": "new personal contact address requires public review"})
    return findings


def public_identity(name, email):
    if (name, email) == ("GitHub", "noreply@github.com"):
        return True
    match = re.fullmatch(r"(?:[0-9]+\+)?([A-Za-z0-9][A-Za-z0-9-]{0,38})@users\.noreply\.github\.com", email)
    return bool(match and name == match.group(1))


def git(root, *args, optional=False):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode and not optional:
        raise PublicationError("Git audit command failed: " + " ".join(args[:2]))
    return None if result.returncode else result.stdout


def audit_repository(root, baseline=UPSTREAM, check_commits=True):
    root = Path(root).resolve()
    git(root, "merge-base", "--is-ancestor", baseline, "HEAD")
    names = set(filter(None, git(root, "diff", "--name-only", "--diff-filter=ACMRT", "-z", baseline).decode().split("\0")))
    names.update(filter(None, git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")))
    findings, baselines = [], {}

    def inspect(name, data, context):
        if name not in baselines:
            baselines[name] = git(root, "show", baseline + ":" + name, optional=True) or b""
        findings.extend(audit_source_blob(name, data, baselines[name], context))

    for name in sorted(names):
        path = root / name
        if path.is_symlink():
            findings.append({"context": "worktree", "path": name, "problem": "new or changed source symlink requires review"})
        elif path.is_file():
            inspect(name, path.read_bytes(), "worktree")
    commits = git(root, "rev-list", baseline + "..HEAD").decode().splitlines() if check_commits else []
    for commit in commits:
        fields = git(root, "show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", commit).decode(errors="replace").strip().split("\0")
        if len(fields) != 4 or not public_identity(*fields[:2]) or not public_identity(*fields[2:]):
            findings.append({"context": commit, "problem": "author and committer must use a matching public handle and GitHub noreply email"})
        findings.extend(audit_source_blob("commit-message", git(root, "show", "-s", "--format=%B", commit), context=commit))
        changed = git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", "--diff-filter=ACMRT", "-z", commit).decode().split("\0")
        for name in filter(None, changed):
            data = git(root, "show", commit + ":" + name, optional=True)
            if data is not None:
                mode = git(root, "ls-tree", commit, "--", name).split(b" ", 1)[0]
                if mode == b"120000":
                    findings.append({"context": commit, "path": name, "problem": "new or changed source symlink requires review"})
                else:
                    inspect(name, data, commit)
    return {"passed": not findings, "baseline": baseline, "changed_files": len(names),
            "checked_commits": len(commits), "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", default=UPSTREAM)
    parser.add_argument("--skip-commits", action="store_true", help="Review worktree files before replacing private history")
    parser.add_argument("--artifact", type=Path, action="append", default=[], help="Also scan an unpacked app or distribution file")
    args = parser.parse_args()
    result = audit_repository(args.root, args.baseline, not args.skip_commits)
    for path in args.artifact:
        try:
            assert_public_artifact(path, forbidden_roots=(args.root.resolve(), Path.home()))
        except PublicationError as error:
            result["findings"].append({"context": "distribution", "path": path.name, "problem": str(error)})
    result["passed"] = not result["findings"]
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
