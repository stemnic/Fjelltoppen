#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Remove SVG editor filesystem metadata from copied application resources."""

import argparse
import copy
import io
from pathlib import Path
import re
import tempfile
import xml.parsers.expat
import zipfile

EDITOR_ATTRIBUTES = {
    "http://www.inkscape.org/namespaces/inkscape": {"export-filename"},
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd": {"docbase", "docname", "absref"},
}
ATTRIBUTE = re.compile(rb"\s+([^\s=/>]+)\s*=\s*(['\"]).*?\2", re.DOTALL)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def sanitize_svg(data):
    """Validate XML and remove only recognized editor attributes by byte span."""
    parser = xml.parsers.expat.ParserCreate(namespace_separator="|")
    pending, scopes, spans = {}, [], []

    def namespace(prefix, uri):
        pending[prefix or ""] = uri

    def start(name, attributes):
        scope = dict(scopes[-1]) if scopes else {}
        scope.update(pending)
        pending.clear()
        scopes.append(scope)
        wanted = {key for key in attributes if "|" in key
                  and key.rsplit("|", 1)[1] in EDITOR_ATTRIBUTES.get(key.rsplit("|", 1)[0], ())}
        if not wanted:
            return
        offset = parser.CurrentByteIndex
        end, quote = offset, None
        while end < len(data):
            byte = data[end]
            if quote:
                if byte == quote:
                    quote = None
            elif byte in (ord("'"), ord('"')):
                quote = byte
            elif byte == ord(">"):
                break
            end += 1
        found = set()
        for match in ATTRIBUTE.finditer(data[offset:end + 1]):
            attribute = match.group(1).decode("utf-8")
            if ":" not in attribute:
                continue
            prefix, local = attribute.split(":", 1)
            expanded = (scope.get(prefix) or "") + "|" + local
            if expanded in wanted:
                spans.append((offset + match.start(), offset + match.end()))
                found.add(expanded)
        if found != wanted:
            raise ValueError("Unsupported SVG attribute encoding")

    parser.StartNamespaceDeclHandler = namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: scopes.pop()
    parser.Parse(data, True)
    result = data
    for begin, end in reversed(spans):
        result = result[:begin] + result[end:]
    xml.parsers.expat.ParserCreate(namespace_separator="|").Parse(result, True)
    return result


def sanitize_zip(data):
    """Preserve decoded members and ZIP metadata while sanitizing SVG members."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Resource archive is too large")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len({info.filename for info in infos}) != len(infos):
            raise ValueError("Duplicate resource archive entries")
        if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES or any(info.file_size > MAX_MEMBER_BYTES or info.flag_bits & 1 for info in infos):
            raise ValueError("Oversized or encrypted resource archive")
        changed = {}
        for info in infos:
            if info.filename.lower().endswith(".svg"):
                original = archive.read(info)
                sanitized = sanitize_svg(original)
                if sanitized != original:
                    changed[info.filename] = sanitized
        if not changed:
            return data, []
        payloads = {info.filename: changed.get(info.filename, archive.read(info)) for info in infos}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as replacement:
            replacement.comment = archive.comment
            for info in infos:
                replacement.writestr(copy.copy(info), payloads[info.filename])
    output = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(output)) as check:
        if check.namelist() != [info.filename for info in infos] or any(check.read(info.filename) != payloads[info.filename] for info in infos):
            raise ValueError("Sanitized resource archive failed content or CRC verification")
    return output, [info.filename for info in infos if info.filename in changed]


def sanitize_directory(resources):
    resources = Path(resources)
    if resources.is_symlink() or not resources.is_dir():
        raise ValueError("Expected a copied resource directory")
    changed = []
    for path in sorted(resources.iterdir()):
        if path.suffix.lower() not in (".zip", ".dat"):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Resource archive must be an independent regular file")
        if not zipfile.is_zipfile(path):
            continue
        output, members = sanitize_zip(path.read_bytes())
        if members:
            with tempfile.NamedTemporaryFile(dir=resources, prefix=".sanitized-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(output)
            try:
                temporary.chmod(path.stat().st_mode & 0o777)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            changed.extend((path.name, member) for member in members)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="Copied application Contents/Resources directory")
    args = parser.parse_args()
    changed = sanitize_directory(args.resources)
    print(f"Removed SVG editor filesystem metadata from {len(changed)} packaged members")


if __name__ == "__main__":
    main()
