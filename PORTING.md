# FGP Gold macOS development

This is the separate playable-game repository for Flåklypa Grand Prix Gold.
It adopts the full Fjelltoppen/ScummVM source tree, preserving upstream history
and GPL licensing. The original source recovery and Windows comparison work
remain in their separate research repository.

Development uses the `macos-development` branch. `upstream` points to
`https://github.com/alehel/Fjelltoppen.git`; the reviewed starting revision is
`dbbf9d9a2463078bfe3eb8d94de68edd949b7f6f`, tagged locally as
`fgp-macos-upstream-2026-10-13`. This checkout has its own Git objects and full
commit history. The public `stemnic/Fjelltoppen` GitHub fork is retained as
`origin`. Public source and package checks are documented in
[PUBLICATION.md](docs/macos-port/PUBLICATION.md).

## Build and play

```sh
python3 tools/macos_port/build.py build
open artifacts/build/macos/Fjelltoppen.app
```

The packaging script builds a universal ARM64/x86_64 application and DMG,
with static third-party libraries and macOS system frameworks. It requires
Xcode command-line tools and Rosetta for the Intel build. The app imports the
original Gold disc folders or uses an existing game-data installation.

The app, downloaded dependencies, game-data copies, profiles, logs and captures
are under ignored `artifacts/` for development. Original game data is excluded
from Git and the app bundle. Test profiles/data come directly from the supplied
discs and native gameplay; the runnable project does not use research artifacts.

The upstream [README](README.md) is retained. Start with the local
[build/play guide](docs/macos-port/README.md),
[upstream and patch record](docs/macos-port/UPSTREAM.md), and
[validation results](docs/macos-port/VALIDATION.md).

## Local scope

The first changes fix explicit compiler architecture selection in the universal
build and add opt-in private application data for development tests. Standalone
Fjelltoppen also avoids creating unused ScummVM launcher folders. Public builds
map compiler source paths to generic paths, and the importer checks resumed
copies against their source content. The existing
game engine, data importer, media decoders, profile UI and package artwork are
reused.

Run the fixture tests without game assets:

```sh
python3 -m unittest discover -s tools/macos_port -p 'test_*.py' -v
```

The manual GitHub Actions workflow builds and inspects packages without original
assets or release publication. It is available for manual runs on the existing
fork. The local validation record identifies what actually ran.
