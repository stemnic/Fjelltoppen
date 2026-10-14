# Upstream provenance and local scope

The full source tree comes from
[alehel/Fjelltoppen](https://github.com/alehel/Fjelltoppen), branch `flaaklypa`,
at commit `dbbf9d9a2463078bfe3eb8d94de68edd949b7f6f`.
This is an unofficial ScummVM-based engine reimplementation. The original
Flåklypa Gold Windows engine has not been identified as ScummVM-based.

We retain the whole fork because its game support also changes shared code,
including Smacker media decoding and application startup. Copying only
`engines/flaaklypa` into stock ScummVM would omit those changes. This checkout
does not require the decompilation repository at build or runtime.

This checkout is a separate Git repository, not a subdirectory or submodule of the
research repository. The latter keeps its Windows oracle, independent C
reconstructions and reverse-engineering evidence. Those sources and original
executable bytes are not imported into this project.

## Follow upstream

The `upstream` remote points to `https://github.com/alehel/Fjelltoppen.git`.
The existing `stemnic/Fjelltoppen` fork is retained as `origin`, pointing to
`https://github.com/stemnic/Fjelltoppen.git`. The development branch is
`macos-development`. Local commit identities use the public GitHub handle
and GitHub's noreply address; no personal email is needed in this project.
No release or upstream pull request is part of this setup.
The tested baseline is retained as `fgp-macos-upstream-2026-10-13`;
macOS changes are developed on `macos-development`.

Fetch new work without changing the local working branch:

```sh
git fetch upstream flaaklypa
git log --oneline HEAD..upstream/flaaklypa
git diff --stat HEAD...upstream/flaaklypa
```

Review the commits, merge or select the applicable changes, rebuild, and run
the validation checks before advancing the documented baseline. A tag or an
upstream release is not a substitute for testing our specific Gold data and
the packaged application.

## Local changes and contributions

The independent clone has its own object database, without alternates or
hardlinks to the research checkout. It includes upstream Git history and
uses the existing hosted fork for the macOS development branch.

The initial local work adds a macOS project guide, build isolation,
independent validation tooling and an opt-in application data directory.
The latter allows the existing first-launch importer to be exercised in a
private location while normal launches retain the upstream default paths.
The standalone application also leaves ScummVM's separate icon/DLC defaults
empty, avoiding unrelated ScummVM folders during startup. That change is
limited to `FJELLTOPPEN` builds.
The macOS build passes explicit compiler/linker architecture flags and
verifies each slice before combining them. On the initial Apple Silicon host,
`arch -x86_64 clang++ -dumpmachine` still reported ARM64; the unmodified
script therefore failed to link the x86_64 SDL dependency during configure.
The fix retains the upstream static/universal build workflow.

SurfaceSDL initializes its dirty-rectangle count and defers redraws until its
screen surfaces exist. New packaged startup checks exposed an intermittent
null-surface blit before any importer dialog or scene. A native debugger
confirmed that early graphics-mode restoration read an uninitialized count.
This is a backend startup fix; the SVG metadata cleanup preserves rendering
content.

This project adopts the existing engine instead of creating another engine
or treating its C++ as recovered original source.
We keep the upstream name and existing icon and preserve upstream credits.

Read [CONTRIBUTING.md](../../CONTRIBUTING.md) and
[AI-GUIDELINES.md](../../AI-GUIDELINES.md) before preparing an upstream
contribution. The latter requires tested, reviewed work, an `Assisted-by:`
disclosure when AI assistance is used, and human-made commits/PR submissions
for the upstream project. No PR, public release or message to maintainers is
part of this local setup. Our documentation and tooling remain GPL-compatible;
the root license and third-party notices are retained.

The separate [macOS build workflow](../../.github/workflows/macos-port.yml)
is manual and build-only. It runs tooling fixtures and packages the app with
read-only repository permissions; it publishes no release and uses no game
assets. The inherited release workflow is unchanged. The new macOS port
workflow has not been run on GitHub; the recorded checks ran locally.

The workflow uses the ARM64 `macos-15` label documented in
[GitHub's runner table](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job),
and pins [checkout v7](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1)
and [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)
to the verified tag commits. These pins were checked on 14 October 2026.

## Private inputs

Tests that need real game content require explicit paths to the owner's
original disc directories:

```text
/path/to/FGPGULL1
/path/to/FGPGULL2
```

Required resources are copied into an ignored validation installation or
the application's own data directory. Do not commit the game archives,
videos, music, textures, original executables, profiles or screenshots to
this source repository. Dependency downloads and app/DMG build outputs stay
ignored too. Tracked validation summaries contain facts and fingerprints,
not original game content.
The importer regression tests use generated synthetic disc files and need no
original game content. See [PUBLICATION.md](PUBLICATION.md) for the public
source, metadata and package checks.
