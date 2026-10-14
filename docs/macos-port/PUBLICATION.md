# Public source and packages

This fork contains the open-source engine and development tooling. Original
game content, disc images, player profiles, captures and full local build
records remain private. A `.gitignore` rule does not remove a file or personal
metadata that was already committed.

## Source and history

Run the publication check before sharing commits:

```sh
python3 tools/macos_port/public_audit.py
```

It checks additions relative to the pinned upstream baseline and the changed
files in every new commit, rather than just the latest tree. It rejects local
user-directory paths, common credential patterns, private data directories,
game archives and personal commit identities. Existing upstream credits,
licenses and public history are retained.

Use your public GitHub handle for `user.name` and your GitHub-provided noreply
address for `user.email`, scoped to this repository. Both author and committer
metadata are public. The checker accepts the numeric-ID noreply format too.
See [GitHub's commit email instructions](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address).

`--skip-commits` checks the current files while preparing a history cleanup;
it is insufficient for final publication. Removing sensitive text in a new
commit leaves it in earlier commits. Retain any recovery copy outside the
publishable repository and replace only affected development history,
preserving upstream commits.

If earlier commits were already hosted, a history rewrite removes them from
the branch's ordinary history but cannot erase existing clones or GitHub's
cached commit views. GitHub documents additional cleanup and support options
in [removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Do not publish a recovery bundle or merge old development history back in.

## Reviewed cleanup on 14 October 2026

The two earlier development commits contained personal Git identities and
local paths. Their replacements start directly from the reviewed upstream
baseline: one sanitized source commit and one documentation/evidence commit.
Only `macos-development` is rewritten; original upstream branches, tags,
credits and license notices are preserved. A verified recovery bundle stays
outside this repository.

The reviewed package was rebuilt with compiler path mappings and resource
metadata cleanup. Its public facts are in
[the package manifest](evidence/package.json); aggregate runtime results and
their limits are in [VALIDATION.md](VALIDATION.md). Full local reports and
the earlier prototype remain private.

## Packages

Compiler file-prefix mappings prevent local source paths from entering
assertion strings and debug data. Stripping symbols alone does not remove
these strings. The packaging script applies mappings to the engine and all
third-party C/C++ dependencies. It also removes nonvisual SVG editor filesystem
metadata from copied GUI resources. Original upstream archives, rendering
attributes, other decoded members and license notices are preserved.

Use a fresh output directory after changing these mappings: cached static
libraries and object files retain their old strings.

```sh
python3 tools/macos_port/build.py build --output artifacts/build/public-build
python3 tools/macos_port/public_audit.py \
  --artifact artifacts/build/public-build/Fjelltoppen.app
```

The package inspector checks both architectures, system-only dynamic links,
deployment targets, support files, signature and disk image. The publication
scan checks the unpacked app for private paths, common secrets and original
game resources, recursively inspecting ZIP-based support archives within
bounded size and nesting limits. The inspector mounts the DMG read-only and
checks its contents against the reviewed app; searching compressed image
bytes alone is insufficient.

Share the reviewed app or DMG and `public-manifest.json`. This manifest is an
allowlisted projection of package names, sizes, hashes, compiler identity,
architecture, dependency identities and check results. It does not contain
raw commands, command output, local filesystem paths, logs or game-data
inventories. Full provenance and logs stay under ignored `artifacts/`.
The manual CI workflow uploads the DMG and this public manifest only.

## Regression tests

The Python fixture tests generate artificial PNG, profile and resource data.
The native importer regression also uses generated files, exits at the CD 2
prompt and never starts the game:

```sh
python3 -m unittest discover -s tools/macos_port -p 'test_*.py' -v
python3 tools/macos_port/importer_regression.py \
  --app artifacts/build/public-build/Fjelltoppen.app --label synthetic-import
```

Tests using real discs require explicit paths supplied by their owner. Their
configs, saves, screenshots, raw reports and copied resources remain private.
Public validation summaries record aggregate results and limitations without
including game content or personal paths.

The scanner is a targeted check, not a proof that arbitrary files contain no
private information. Review the staged diff and intended release contents
as well. Keep the GPL and third-party license notices with redistributed code
and binaries.
