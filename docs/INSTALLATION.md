# Installing Cactus Canyon Continued on Batocera

## What this project does

This project connects the Cactus Canyon Continued (CCC) Python game software to native Linux Visual Pinball X on Batocera. It does **not** run the Windows P-ROC COM server or Windows VPX under Wine.

The integration forwards table switches to the real CCC game and returns game-generated coil, lamp and GI changes. An isolated Python 2 runtime supplies the legacy dependencies. The installation uses its own application files, configuration and saves rather than replacing your normal Cactus Canyon table or Batocera's VPX executable.

## Compatibility and honest limitations

- The original integration was exercised on **Batocera 43.1, x86_64**, using its native VPX 10.8 player.
- An x86_64 build does not run on a Raspberry Pi/ARM system. Other Batocera releases and differently built VPX players require separate qualification.
- A physical color DMD is optional. A normal desktop installation must not require a DMDserver or cabinet-specific hardware.
- Table/camera geometry and controls vary by installation. Do not copy another cabinet's display rotation, monitor positions, audio device identifiers or scores blindly.
- Python 2 is end-of-life. The runtime is included only for this legacy local game; do not expose it as a network service or use it to run untrusted Python/YAML files.
- Passing an installer check is not a certification of every game mode or of hard real-time input latency.

## Files you must supply

Obtain the game and table from sources authorized by their authors. Public availability alone does not grant redistribution permission.

1. A compatible **CCCforVP game directory**, containing `game.py`, `assets.py`, `procgame/`, `ep/`, `config/`, the animations/fonts and the required sound assets.
2. The matching **shared** P-ROC data directory, including the DMD fonts/configuration referenced by that game.
3. The supported **Cactus Canyon Continued Bigusmod VPX table** and its matching original external `.vbs` script.

The known table SHA-256 is:

```text
542b775831a1876c37d46eb8a825e7027562f6bab648f4ee2e6b20db483353a8
```

The known matching original script SHA-256 is:

```text
f20205c66d564b6bbc88f6b0ee7635f0e2fd6c9256b6255d6fa2065899618f7e
```

An ordinary Cactus Canyon ROM or an unrelated Cactus Canyon table is **not** interchangeable with Continued. Do not rename a ROM or disable the original table's controller to conceal an unsupported input.

The shareable project must not contain your personal `game_data.yaml`, `user_settings.yaml`, backup saves, credentials or private cabinet logs. A fresh installation should start from the supplied game's defaults. Import existing personal saves only through an explicit opt-in operation after backing them up.

## Before installing

1. Back up your Batocera user data and any existing CCC saves.
2. Exit running games. Do not install or change the integration while another VPX table owns the display or DMD.
3. Place the installer and your legally obtained input files under Batocera's writable `/userdata` area.
4. Run the installer's compatibility/input check before committing the installation. Resolve missing files or unsupported architecture/version errors rather than bypassing them.
5. Keep the installer archive, checksums and license notices together when sharing the project.

## First launch

Start with a normal software-display configuration and the existing Batocera audio output. Enable a physical DMD only when a compatible local DMDserver is already configured and working; the installer must not restart it or replace working colorization services.

Verify these steps separately:

- The table opens without a controller/script error.
- The CCC display changes and shows the game rather than a static frontend logo.
- Start/coin inputs reach the game; note whether the supplied settings select free play.
- The trough feeds a ball and the plunger/flippers respond.
- Scoring changes during play.
- CCC music/quotes and table effects are audible on the intended output.
- Normal exit returns to EmulationStation and saves remain available after restarting.

Do not treat an animation screenshot or a successful file copy as proof of playable integration.

## Display setup

Use the current machine's resolution and orientation. Desktop and cabinet views need different camera settings. If the table is clipped or too narrow, adjust its own camera override rather than changing every VPX table.

When launching through Ports, check that Ports has not selected a lower resolution than VPX expects. Any resolution override should apply only to the CCC launcher, not to the entire Ports library.

For a 256x64 physical DMD, the tested dense-grid rendering samples the actual CCC 128x32 source-dot colors and expands each logical pixel into a filled 2x2 block. This avoids scaling the desktop renderer's simulated dot gaps onto the physical LEDs.

## Troubleshooting

### Unsupported architecture or player

Use an x86_64 Batocera system and the documented supported player. Do not assume an ARM image or a later VPX plugin API is equivalent. The installer should stop with a clear diagnostic rather than silently replacing Batocera's emulator.

### Missing sound or font files

Check the exact source folder and the required shared directory. Some P-ROC installations contain an incomplete game tree while another authorized game/installer copy contains the missing assets. Supply the correct original files; do not substitute invented silent samples.

### A controller or runtime error

Read the current attempt's launcher, VPX and game logs. Confirm that the table and script match the supported inputs. The native player does not provide the Windows `VPROC.Controller` COM object; the generated sibling script and native adapter are both required.

### No sound

Confirm that Batocera itself can play audio. Inspect the new launch's BASS and SDL/Pulse errors. The portable Python runtime and native VPX need separate library environments; do not globally replace ALSA/Pulse configuration or system libraries.

### DMD remains blank

First verify the software display. Enable hardware forwarding only for a compatible endpoint and verify that another game/screensaver is not the current display owner. Sending a packet successfully does not prove that the physical panel rendered it correctly.

### Table fills only part of the screen or is cropped

Check the actual X11 resolution, the launcher-specific Ports video mode, VPX's window dimensions and the table-local camera. Preserve the global screen rotation. Recheck the top lanes, both flippers and apron after each adjustment.

### Updating or uninstalling

Keep personal saves/configuration separate from replaceable application files. Back up before updating. An uninstall should remove only files owned by this project and preserve user saves unless deletion is explicitly requested. Never remove an unrelated existing Ports entry or ordinary Cactus Canyon table.

## Reporting a problem

Include the Batocera version, CPU architecture, VPX version, table/script hashes, selected display/audio/DMD mode and the failed attempt's diagnostic summary. Remove personal names, scores, local account paths and credentials before attaching logs. State whether the problem occurs during installation, game startup, play, pause, exit or the next launch.
