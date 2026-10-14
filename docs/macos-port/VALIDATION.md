# Packaged macOS validation

The initial upstream baseline is
`dbbf9d9a2463078bfe3eb8d94de68edd949b7f6f`.
This document records checks of the independently built application in this
repository. Results from the earlier research checkout do not automatically
validate a new application package.

## Public package and importer improvements

The current package is built from source checkpoint
`c19d4bf7f9da4a802376a2defc3e86593abb696d`.
Its [public package manifest](evidence/package.json) contains structured facts
without local paths or raw tool output. Compiler path mappings cover both
architectures and dependencies; copied SVG resources lose editor filesystem
metadata while retaining graphics and credits. Independent scans of the app,
public manifest, mounted DMG and 610 decompressed ZIP members found no former
personal identifiers or local home/source paths. The DMG app matches the
standalone app byte for byte. Original game content is excluded.

The [current acceptance summary](evidence/public-acceptance.json) records:

- 39 passing synthetic tooling tests.
- 14 passing synthetic importer checks across four process runs, including
  same-size damage after two read chunks, matching-file preservation by mtime,
  truncated files, empty files and exact chunk boundaries.
- 14 passing real-disc startup checks across five process runs, including
  complete two-disc import and interrupted-copy resume with exact resource
  matches. Relative-path rejection intentionally exits with an error.
- 20 consecutive welcome/render/exit launches, with seven passing aggregate
  stability checks and no imports or game scenes.
- 65 bounded game cases with no retries, timeouts or warnings, covering all
  58 scene entries and seven normal-startup, story/reload, native Cocoa,
  Rosetta and race-gate checks. Every captured frame is nonblack and all 205
  copied resources were rechecked. Direct scene entry tests loading/rendering;
  it does not establish completed gameplay. Birthday redirects to Menu for
  the synthetic January 1 player.

The first privacy-clean package exposed an inherited intermittent SurfaceSDL
startup bug. LLDB confirmed that an uninitialized dirty-rectangle count was
used during graphics-mode restoration before screen allocation. Initializing
the count and deferring redraws until surfaces exist fixed the observed
failure. Failed package reports and debugger records remain private; the
corrected package uses a separate build/validation checkpoint.

Original discs, normal user folders and `HOME` were unchanged. Test configs,
profiles, captures and full reports remain private. Copy-dialog
cancellation and injected source-open/read failures are not runtime-tested.

## Verified package on 14 October 2026

The initial private prototype build produced `Fjelltoppen.app` and a verified
version 0.1.0 disk image. Its results below are historical checkpoints; this
prototype contained compiler source paths and must not be distributed.
Public packages require the checks in [PUBLICATION.md](PUBLICATION.md).
Apple clang 21.0.0 compiled the full engine and both static dependency sets.
The executable has ARM64 and x86_64 slices, each with macOS 11.0 as its
declared deployment target. It links only Apple system libraries/frameworks,
including system zlib; it requires no Homebrew libraries. The ad-hoc code
signature, all eight support-data files, GPL notice and disk image checksum
passed inspection.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Universal executable | 18,620,432 | `aad1ed3b6346f7b17f77ba854652d9f45399f6476c2252469cd08cbbdbc59282` |
| Disk image | 22,253,771 | `dae4061961215fa831fb51b51824024a19440d0f26a296ab5f447a7e76d00676` |

The tracked [historical package summary](evidence/initial-package.json) links the full local
inspection at `artifacts/build/macos/inspection-final.json`. It retains the
source/configuration fingerprints, dependency hashes and build-source
checkpoint. The original operator build finished before the current wrapper
was available; its enhanced checkpoint explicitly records an adoption after
the reviewed build. New wrapper builds freeze inputs before compilation and
check that those inputs remain unchanged. Inspection preserves that binding
instead of assigning current source to an older binary.

The historical
`build-manifest.json` remains frozen because it was used by the gameplay
installation. The enhanced final inspection adds a broader frozen source inventory.
Older macOS versions, separate Intel hardware and independent byte-reproducible
builds have not been verified merely by declaring the deployment target.

## Verified first-launch importer

[The tracked startup summary](evidence/startup.json) pins
`artifacts/validation/startup-001/summary.json`, which records five packaged-app
process runs and fourteen passing checks. Tests exercised the actual
welcome dialog, an explicitly relative data-path rejection, both original
disc-directory imports, and exiting after CD 1 then resuming from CD 1.
The resume check deliberately truncated `sceneindex.bin` to 128 bytes; the
next import recopied it and the final full file inventory matched the discs.

The importer copied 207 files, totaling 1,028,876,230 bytes. The 205 game
resources account for 1,028,863,934 bytes; two Finder metadata files account
for the rest. Every copied file's SHA-256 matched its source. Both complete
imports reached Menu with the new-player name prompt. Welcome, progress,
CD 2 prompt and Menu captures were inspected for legibility.

The app bundle, original discs and snapshots of the normal Application
Support, preferences, logs and Documents locations remained unchanged.
All run configuration, application data and profiles were private. This
checks directory import and restart/resume, without changing `HOME`.
It does not test the copy dialog's Cancel button or optical-disc image
parsing. At this checkpoint the inherited resume logic skipped existing
same-size files. The later importer regression adds a specific test for
repairing same-size damage; these original checks established shortened-file
repair only.

Run another independent first-launch collection with a new label:

```sh
python3 tools/macos_port/startup_validation.py \
  --app artifacts/build/macos/Fjelltoppen.app --label startup-002 \
  --disc-one /path/to/FGPGULL1 --disc-two /path/to/FGPGULL2
```

## Verified game and persistence checks

[The tracked gameplay summary](evidence/game.json) pins the full local report
at `artifacts/validation/packaged-001/report.json`. All 72 bounded cases
exited zero, with no timeouts, no warnings and nonblack captured frames.
All 205 private resource copies were rechecked against the originals.
These cases use the packaged executable, whose fingerprint matches the
universal executable above.

The matrix covers all 58 scene entries. Birthday immediately redirects to
Menu for this January 1 profile, so its captured frame is Menu. Racing's
first-visit Help overlay remains part of the observation. The matrix is a
loading/rendering check, not 58 completed games.

Eleven explicit behavior checks passed:

| Check | Observed behavior | Scope |
| --- | --- | --- |
| New profile | Menu text and calendar input create `MacPort`, birthday January 1 | Fresh native player; no imported research profile |
| Normal startup | Logo → Birthday → Menu | No scene shortcut |
| Normal story start | Logo → Birthday → Menu → Intro → Yard | Normal Start/Space input; campaign not completed |
| Story reload | Reloaded Menu's Start enters Yard | Menu entry is direct; separate full-boot test above |
| Default Mac backend | Cocoa/OpenGL renders Menu; CoreAudio selected | No graphics override; audible quality unassessed |
| Intel slice | x86_64 Menu renders under Rosetta | Apple Silicon host; physical Intel Mac untested |
| Race gate | Fresh profile is told to build more of Il Tempo Gigante | Normal menu attempt correctly remains locked |
| Race input | Help dismissal, natural countdown and held Up accelerate/move the car | Direct race entry; no lap, finish or normal unlock |
| Sliding completion | 21 ordinary legal tile clicks solve a 3×3 picture; solved state 3 and highscore survive reload | One picture; no solve/award helper |
| Trivia completion | Two 32-question games finish: 11 correct/6,950 points and 9 correct/5,140 points; history/highscore survive reload | Mixed answers; repeated input begins a third round before capture |
| Anaglyph activity | Return in chooser enters ping-pong | Activity entry, not completed activity |

Trivia preserves 71 previously shown questions out of 499 in the saved
history. This includes the third round begun by the repeated input schedule.
The highscore and history records survive a separate reload; resuming the
live third round is not claimed. The sliding solver plans legal moves from
the observed board and sends actual mouse input; the existing engine
recognizes completion and writes the score.

The harness's ten temporary-only fixture tests pass without original assets.
They cover private paths/symlinks, resource tampering, PNG filters/CRC,
native profile bounds, legal sliding moves and both build-manifest schemas.
The CI workflow uses the same fixture command; hosted CI remains unrun.

## Required package checks

After running the existing macOS packaging script, check:

```sh
APP=artifacts/build/macos/Fjelltoppen.app
BIN="$APP/Contents/MacOS/fjelltoppen"
lipo -archs "$BIN"
otool -L "$BIN"
plutil -lint "$APP/Contents/Info.plist"
codesign --verify --deep --strict "$APP"
hdiutil verify artifacts/build/macos/Fjelltoppen-0.1.0-macos.dmg
```

The app should contain ARM64 and x86_64 slices, its theme/font/GUI resources
and license notices. Its dynamic dependencies should resolve to Apple
system libraries/frameworks, with no Homebrew or source-checkout dependency.
Ad-hoc signature verification establishes bundle integrity locally; it does
not establish Apple notarization or Gatekeeper acceptance on another Mac.

## Isolated game checks

The local harness at `tools/macos_port/validation.py` uses the supplied disc
folders directly and creates its own ignored resource copies. Every bounded
case uses private configuration, player profiles, logs and captures; new case
names are required. It uses the packaged executable and resources, with no
Wine, Windows executable, or research-repository asset links.

`--config` names each private configuration file. The local
`[scummvm] app_data_path` option names an absolute private application data
directory, so the real first-launch importer can copy both discs without
writing to the normal Application Support directory or changing `HOME`.
An explicitly relative or empty data path must be rejected. Test the default
and overridden paths separately; an explicit profile `savepath` remains
independent of the data-directory override.

Validation begins with normal startup and a new player made through the game
menu. Cases that use direct scene entry are labeled as such. A scene matrix
checks scene loading and rendering; it does not establish complete gameplay.
Save/reload cases copy native profiles and omit repeated profile import.

Record each run's app fingerprint, source revision, resource fingerprints,
command line, private paths, exit status, observed scene and captured frame.
Timed automation must be bounded and terminate only its own process. Inspect
the screenshots and state evidence before reporting a successful action.

## Gameplay acceptance work

The engine is still marked unstable upstream. Complete these checks through
normal game input as work continues:

| Area | Required behavior |
| --- | --- |
| First launch | Select both original Gold discs; data copy completes; restart needs no discs |
| Profiles | Create/select/export/import a player; progress survives exit and restart |
| Campaign | Advance scenes and awards normally; reach the racing unlock without shortcuts |
| Racing | Steering/braking/acceleration, laps, finishes, results and tournament progression |
| Gold activities | Complete representative trivia, puzzle and other activities; awards persist |
| Media | Story videos advance; dialogue, effects and music are audible and correctly timed |
| Package | Open the app independently of its source checkout and Homebrew libraries |

Dummy-audio runs establish no audible-output or audio-quality result. Direct
race entry skips normal unlock. A nonblack frame is a rendering checkpoint,
not a completed game, solved puzzle or correct physics simulation.

When a behavior fails, retain the exact command, save and resource/build
fingerprints locally; compare that behavior with the known Windows reference
in the separate research project, then fix the existing port and add a
focused regression case. Whole-game and original-versus-native timing,
pixel, AI and physics equivalence remain open until specifically tested.
