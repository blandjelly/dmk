# CLAUDE.md

DMK is a FreeRTOS keyboard firmware for several MCUs. This fork (blandjelly/dmk, upstream aroum/dmk)
works on the **HHKB Professional** port: STM32L072 application in Bank 2 (`0x08010000`, 64 KiB),
Topre electrostatic-capacitive matrix read through an ADC, Rapid Trigger, VIA settings.
The owner tests on a **Hybrid ANSI** board (model AHUX01). Read `HANDOFF.md` first: it holds the
current state, open items and hardware measurements. `UPSTREAM_DEV_ANALYSIS.md` compares the fork
with upstream `dev` and plans the pull requests to it.

## Layout

- `dmk_core/`: shared core (tasks, USB/TinyUSB glue, VIA/Vial, keymap, debug console).
- `keyboards/hhkb/`: the HHKB port. `config.h` (matrix, VIA ids), `via.json` (VIA menus, partly
  generated), `docs/en|ru/` (user guides; keep both languages in step).
  - `modules/ec/classic_matrix.c`: the ADC matrix scan (`hhkb_matrix_scan_raw`, RAM-resident).
  - `modules/ec/matrix.c`: matrix task, TIM2-paced scan period, scan diagnostics.
  - `modules/ec/rapid_trigger.c`: per-key travel normalization and RT state machine.
  - `modules/ec/ec_settings.c`: settings, calibration, EEPROM records, debug output.
  - `modules/hybrid/`: Nordic nRF52 link (SPI slave), battery, sleep (Hybrid only).
  - `modules/via_settings/hhkb_via_controls.c`: VIA custom controls by id.
- `platforms/stm32l072/`: startup, linker script (`stm32l072_bank2.ld`), FreeRTOS port.
- `tools/hhkb/`: `hhkb.html` (browser patcher/flasher), `patch_hybrid_hfb.py`, `make_hfb.py`,
  `generate_per_key_menus.py`, `scan_emu.py` (scan timing emulator).
- `tests/`: `run_all_tests.sh` runs C unit tests (clang), host tests for HHKB code and Python tests.

## Build

TinyUSB comes from the pico-sdk submodule; a fresh clone does not build without it:

```bash
git submodule update --init --depth 1 platforms/raspberrypi/pico-sdk
(cd platforms/raspberrypi/pico-sdk && git submodule update --init --depth 1 lib/tinyusb)
```

```bash
./build_all.sh -b hhkb --mcu stm32l072 -d "HHKB_HYBRID" -c          # Hybrid ANSI
./build_all.sh -b hhkb --mcu stm32l072 -d "HHKB_HYBRID,HHKB_JIS" -c # Hybrid JIS
./build_all.sh -b hhkb --mcu stm32l072 -d "" -c                     # Classic ANSI (HHKB_JIS for JIS)
```

Output: `build/hhkb-script/dmk_hhkb_stm32l072{,.bin}` (Classic also gets an `.hfb`).
Extra defines go in the same `-d` list, e.g. `HHKB_SCAN_DIAGNOSTICS`.

- CI (`.github/workflows/build_test.yml`) uses Ubuntu `gcc-arm-none-eabi` **13.2**. Every release
  variant must link with it. Flash and RAM are nearly full: check `text` and the RAM total of
  `.data + .bss + ._user_heap_stack` (20480 bytes) for all four variants after a change.
- GCC 14 makes smaller code. Diagnostic Hybrid builds (`HHKB_SCAN_DIAGNOSTICS`) only fit with it.
  `developer.arm.com` may be blocked in cloud sessions; the xPack build from GitHub releases works:
  `https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v14.2.1-1.1/xpack-arm-none-eabi-gcc-14.2.1-1.1-linux-x64.tar.gz`
  (put its `bin/` first on `PATH` for one build).
- Hybrid update files: `python3 tools/hhkb/patch_hybrid_hfb.py --layout ansi <app.bin> HHKB800_FW_A048.hfb <out.hfb>`.
  The stock PFU file is not in the repo; the owner supplies it. `hhkb.html` does the same in a browser.

## Tests

```bash
bash tests/run_all_tests.sh
```

Some Python tests cut code blocks out of the C sources by text (e.g. the `if (capture) {` block in
`classic_matrix.c`); keep those blocks and their variable names when refactoring.

## Working on the scan

- The ADC reading of a key depends on scan timing: how long the previous key's processing takes
  before the next strobe, and similar effects. Changes that alter per-key timing shift readings;
  compare resting values against the calibration baseline after such a change (see HANDOFF.md).
- `tools/hhkb/scan_emu.py` (needs `pip install unicorn pyelftools`) runs one scan of a built ELF:
  `time`, `gaps` (release-to-strobe intervals), `profile`, `cal`. It underestimates hardware time
  by roughly 10-30 %; trust differences between builds more than absolute numbers.
- On hardware, the owner reads VIA's HID Console with HHKB Settings -> Debug flags. The EC Matrix
  Stream itself shifts readings by about 3-4 % travel; judge resting values with it off.

## Conventions

- Commit messages: `type(hhkb): summary` (feat, fix, perf, docs, refactor, build), body explaining
  the problem and the change, measurements where relevant.
- User-facing docs exist in English and Russian (`keyboards/hhkb/docs/en`, `.../ru`); update both.
- Work on the branch the session names (recently `feat/hhkb-per-key-rt`).
