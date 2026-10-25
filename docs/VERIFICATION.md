# Native acceptance status

## Qualified scope

This is a development installer with installed-game evidence on Batocera 43.1 x86_64 / native VPX 10.8. It is not a universal compatibility claim or a full gameplay-mode/hardware certification.

The recipient path was exercised from the public source package: checksum-verified upstream downloads, private runtime assembly, exact caller-supplied mapping/table/script, transactional installation, immutable-file check, real CCC constructor, native FakePinPROC processing and the actual installed VPX player.

Two defects were found by real execution and corrected: the EP/game circular-import order and Batocera Openbox forcing the software DMD fullscreen. The latter is fixed with scoped window-state/geometry changes; no global Openbox or screen configuration is changed.

Verified with the fresh native overlay:

- Real headless CCC processing, nonblank RGB output, mapped switch requests, clean protocol STOP and all outputs off.
- Visible upright cabinet view and a colored software DMD at 640x160, position (16,16), over the playfield.
- VPX retains keyboard focus; neither owned window remains forced fullscreen. Screenshots/X11 state were checked at three points during a bounded run.
- Coin/start/plunger keyboard requests; a player was created. This does not certify scoring/physics or all game modes.
- A reviewed bounded run completed 5,485 processing ticks and 1,888 frames. Native game audio initialized through Pulse at 44.1 kHz stereo. Initialization/log checks do not establish audible quality at the physical speakers.
- VPX and CCC exit status zero, normal F4/protocol-STOP shutdown, zero final outputs, restored prior hotkey context, removed launch lock and active pointer.
- Persistent cabinet/desktop profile selection, default-save isolation, update preservation and conservative uninstall are also covered by offline regression tests.

The public package does not include the private screenshots, input files, saves, diagnostic logs, assembled dependency runtime or download cache. Source/binary correspondence for the fresh pygame/pinproc overlay is supplied in the code-only source sidecar and allowlist.

## Not certified

- Another Batocera release, ARM/Raspberry Pi or a different VPX build.
- End-to-end fresh frontend menu discovery/launch/return for this portable installer. Direct installed launcher/player qualification is distinct from EmulationStation UI qualification.
- Physical DMD output for a recipient's device. A compatible local server at literal `127.0.0.1:PORT` is optional and must be selected explicitly.
- Actual speaker output, cabinet-controller wiring/latency, all table mechanisms, pause paths and complete game-mode/scoring coverage.
- Arbitrary game revisions or directories that merely contain a file with a matching name. A dots-only source is rejected; it is not silently treated as the supported original asset layout.

Use the documented originals, keep backups, and report the exact failed attempt rather than assuming a passed file check means gameplay is qualified.
