# Flåklypa Gold on macOS

This checkout is the macOS development project for Flåklypa Grand Prix
Gullutgave. It uses the existing Fjelltoppen engine and its ScummVM platform
code. The game runs as a native macOS application; Wine and the original
Windows executable are not runtime requirements.

The repository is separate from the decompilation/research project. It
contains the complete upstream source tree, preserving its history and
licensing. The original game's assets, local builds, logs, screenshots,
configuration and player profiles are private files outside tracked source.
The upstream [ScummVM README](../../README.md) remains unchanged.

## Build

Use an Apple Silicon Mac with the Xcode command-line tools and Rosetta
installed. The upstream script builds both ARM64 and x86_64 versions from
source and combines them into one application. Dependency downloads are
checked against the hashes in [deps.sh](../../dists/fjelltoppen/deps.sh).

From this repository's root:

```sh
python3 tools/macos_port/build.py build
```

The wrapper invokes `dists/fjelltoppen/macos/build.sh artifacts/build/macos`,
then checks both architectures, linked dependencies, deployment targets,
signature, packaged support files and the disk image. It writes a source,
dependency and package fingerprint manifest beside the app. Full provenance
and build logs remain private; `public-manifest.json` is the shareable summary.
To inspect an
existing build without recompiling, use `python3 tools/macos_port/build.py inspect`.

The outputs are `artifacts/build/macos/Fjelltoppen.app` and
`artifacts/build/macos/Fjelltoppen-0.1.0-macos.dmg`. The version comes from
`dists/fjelltoppen/VERSION`; `FJELLTOPPEN_VERSION` overrides it. The inspector
reads the version from the built bundle. Build products and downloaded
dependency archives are ignored by Git. The executable links
the packaged libraries statically and uses Apple's system frameworks and
system zlib, without relying on a Homebrew installation.

The default signature is ad hoc. A local build has no Apple notarization.
For distribution, the upstream script accepts `CODESIGN_IDENTITY` and
`NOTARY_PROFILE`; credentials and a Developer ID are separate requirements.
The [upstream packaging guide](../../dists/fjelltoppen/README.md) describes
those options and the package contents.

## Play

The latest reviewed build has convenient copies at
[`dist/Fjelltoppen.app`](../../dist/Fjelltoppen.app) and
[`dist/Fjelltoppen-0.1.0-macos.dmg`](../../dist/Fjelltoppen-0.1.0-macos.dmg).
Open the app, or copy it from the disk image into Applications. The `dist`
copies are a tested checkpoint; a later rebuild produces its
updated outputs in `artifacts/build/macos`.

The application asks for the game data on its first launch. Select these
original Gold disc folders in order, wherever you keep your own copy:

1. `FGPGULL1` (CD 1)
2. `FGPGULL2` (CD 2)

The application copies the required resources into its own `Spilldata`
directory. An existing installation containing `data` and `lang` can instead
be selected and used where it is. No assets are bundled inside the app.

Normal launches use
`~/Library/Application Support/Fjelltoppen/fjelltoppen.ini` for configuration,
`Spilldata` for copied resources and `Profiler` for player profiles.
These application folders are independent of either source repository.
Keep `Profiler` when rebuilding or replacing the application.

For a separate development installation, pass an explicit private config:

```sh
artifacts/build/macos/Fjelltoppen.app/Contents/MacOS/fjelltoppen \
  --config=/absolute/private/fjelltoppen.ini
```

Set this application-domain key in that config:

```ini
[scummvm]
app_data_path=/absolute/private/application-data
```

This local option redirects copied `Spilldata` and the default `Profiler`
directory. An explicit `savepath` takes precedence for profiles. The data
path must be absolute; an explicitly empty or relative value is rejected.
The option does not relocate the configuration file, which is selected
before the config is read. Normal launches omit it and keep the upstream
application folders. The validation harness uses it without changing `HOME`.

For an interactive development session with your own independent data copy,
first create a private installation:

```sh
python3 tools/macos_port/validation.py --label development setup \
  --app artifacts/build/macos/Fjelltoppen.app \
  --disc-one /path/to/FGPGULL1 --disc-two /path/to/FGPGULL2
python3 tools/macos_port/validation.py --label development play \
  --name first-play --graphics default --audio
```

Create a player through the normal game menu. Use a new case name for each
launch. Close the window normally; a later launch can use
`--reload-from first-play` to copy that session's saved progress.
The timed test hooks are omitted from `play`.

Option+Return switches between windowed and full-screen modes. Control+F5
opens the ScummVM settings menu; some Mac keyboards require Fn as well.
The blue lamp between the profile controls in the game's menu imports or
exports a player profile. See the packaged
[Norwegian instructions](../../dists/fjelltoppen/LESMEG.txt) for the full
first-launch and profile workflow.

## Development

Keep changes on the local macOS branch and preserve the pinned upstream
baseline described in [UPSTREAM.md](UPSTREAM.md). Reuse the engine, media
decoders, asset selection UI and packaging scripts already in this fork.
Investigate a game behavior only when a reproducible mismatch or failure
requires it. Keep regression commands and outcomes in
[VALIDATION.md](VALIDATION.md).
Before sharing commits or packages, follow [PUBLICATION.md](PUBLICATION.md).

The application and engine are GPL-3.0-or-later. Original game content is
separate from the open-source engine. Keep [COPYING](../../COPYING),
[COPYRIGHT](../../COPYRIGHT) and the third-party licenses with any code or
binary redistribution.
