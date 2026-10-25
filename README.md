# CCC Native for Batocera

Native Linux bridge between Cactus Canyon Continued (CCCforVP) and Visual Pinball X. No Wine or Windows COM server is used.

**Development candidate with native Batocera test evidence — not a universal compatibility guarantee.** The recipient-assembled runtime, real CCC backend, upright cabinet view, visible software DMD and native VPX exit have been exercised on Batocera 43.1 x86_64. See [exact tested scope and remaining gates](docs/VERIFICATION.md).

## Supported target

Batocera 43/43.1, Linux x86_64, native VPX 10.8, glibc 2.35 or newer. Raspberry Pi/ARM is not supported. Software color DMD and sound are enabled by default; physical DMD output is opt-in. No global emulator, audio, rotation or DMD service is replaced.

## Bring your own original files

The installer does not distribute CCC, tables, fonts, music, scores or cabinet configuration. Obtain authorized copies of:

- Compatible CCCforVP game directory and shared P-ROC data/fonts.
- Supported Bigusmod `.vpx` table and matching original `.vbs` script. Exact supported hashes are documented in [Installation](docs/INSTALLATION.md).
- Original compatible `register_vpcom.py` controller mapping source, supplied to the local installer. Redistribution rights for this legacy mapping were not established; it must not be bundled or posted with a release.

Private scores/settings are excluded by default. Existing saves can be imported explicitly with `--import-saves`.

## Entry point

Run on Batocera from an extracted trusted project directory:

```sh
sh install.sh --help
sh install.sh install --help
sh install.sh install \
  --source /userdata/ccc-input/cactuscanyon \
  --shared /userdata/ccc-input/shared \
  --table '/userdata/ccc-input/Cactus Canyon Continued (Bally 1998) Bigusmod.vpx' \
  --script '/userdata/ccc-input/Cactus Canyon Continued (Bally 1998) Bigusmod.vbs' \
  --mapping-source /userdata/ccc-input/register_vpcom.py
sh install.sh check
```

The paths above are examples: supply your own authorized files. The original mapping source must have SHA256 `d69cc40d4a4b3131f900c7021b26f02525f9f90c20569e50c4188c2a5cda5871`. Its required methods are extracted locally without executing the supplied module during installation. The generated mapping stays in the recipient's private installation; do not redistribute it.

A successful installation creates **Ports → Cactus Canyon Continued**. Start with software DMD and the machine's normal audio output. `xdotool` and `wmctrl` are required for the tested window/focus handling. Direct player/backend tests passed on the target described above; normal frontend discovery and complete gameplay/hardware coverage remain separate gates.

`ccc.py` is the Python entry point; `install.sh` only locates it. A real install first checks the platform and prepares the private legacy runtime from pinned upstream downloads. `check`, `--dry-run` and `--check` never download dependencies or write files. When a local runtime has not yet been prepared, package validation may report the missing runtime; that is not a successful installability verdict.

Supported operations: `install`, `check`, `configure`, `uninstall`. Installation owns only its private `/userdata/system/ccc-native` directory and its exact Ports launcher. Updates preserve saves/configuration and keep a previous-root backup. Uninstall preserves changed/untracked files and personal saves.

## Portrait cabinet view

For an already rotated portrait playfield, persist the cabinet camera without changing global display rotation:

```sh
sh install.sh configure --profile cabinet
```

Use `--profile cabinet` during installation to select it immediately. Updates preserve the selected profile. Return to the default desktop table view with `sh install.sh configure --profile desktop`. Cabinet view requires a detected portrait screen; it does not rotate the operating system display. A direct launcher `--profile` flag overrides the saved profile for that run only.

## Runtime and licenses

Python 2 is end-of-life; use this runtime only for this local legacy game. Do not run untrusted Python/YAML inputs or expose it as a network service. Ubuntu/Python/Pillow dependencies are downloaded directly by the recipient and are not intended for public redistribution in this project. The small pygame/pinproc overlay has separate licenses and corresponding build sources/notices. Wrapper licensing does not relicense any third-party content.

**Do not upload this working directory wholesale.** It contains private audit material, locally assembled dependencies and development-only mapping files. Public packaging must use a reviewed allowlist and exclude game inputs, mapping derivations, local runtimes, downloads, private provenance and logs.

See [Installation and troubleshooting](docs/INSTALLATION.md) for compatibility, backups and separate game/audio/DMD acceptance checks.
