# Upstream `dev` compared with this fork

Analysis of 2026-11-24. Fork branch `feat/hhkb-per-key-rt` (blandjelly/dmk) against upstream
aroum/dmk `dev` at `b72ea96`. Both start from `7878092`. No pull requests have been opened yet.

## Target branch

Upstream `dev` has 18 commits since `7878092`, `main` only 4, and the content of those 4 is in
`dev` too. Development happens on `dev`, so pull requests should target `dev`.

## What `dev` has that the fork lacks (HHKB)

| `dev` commit | Change | Relation to the fork |
| --- | --- | --- |
| `6ef2a5e` | Own scan rework: the previous key is processed during the next ADC conversion; cached raw press/release thresholds | Same area as the fork's `12ee6a7` and `ff8e5ae`; they cannot be cherry-picked as they are |
| `b20e7b2` | Settings stored as two compressed EEPROM snapshots, legacy records migrated | Direct conflict: the second snapshot uses `0x0808170C-0x080817FF`, where the fork keeps its per-key RT record (`0x08081700`) |
| `6846be9`, `4a83f9d` | VIA posts calibration start/finish requests; the matrix task applies them at scan boundaries | Touches the functions the fork's calibration fixes change |
| `7b78b4b`, `e1862d2` | CI pinned to Arm GCC 15.3 (`gitlab.arm.com`, blocked in cloud sessions); Hybrid LTO tried and removed | `dev` has far more free flash: with GCC 14 Hybrid ANSI 2504 B free against 1284 B in the fork |
| `3e48c21`, `b72ea96` | Scan diagnostic builds in CI (`[SCAN]` with `raw`, `work_max`, `miss`, `q_full`) | Overlaps the fork's diagnostics; `[REST]` has no counterpart |
| others | SW5/SW6 EEPROM erase, VIA modifier-side fix, less idle background work, curve memory, other platforms | No conflict |

## Emulator comparison (`tools/hhkb/scan_emu.py`, Hybrid ANSI, GCC 14 for both)

| | `dev` | fork |
| --- | --- | --- |
| Scan, RT on | 1033 us | 728 us |
| Scan, RT off | 924 us | 727 us |
| Release to strobe, median / max | 8.1 / 29.9 us | 6.0 / 12.7 us |
| C57 discharge before strobe | about 3.0 us (as stock) | 4.4-5.3 us |

Hardware has measured 10-30 % slower than the emulator, so `dev` probably still misses 1000 Hz
on Hybrid ANSI, especially with RT on. Its documentation says scan timing still needs hardware
verification.

## Fork fixes still missing in `dev`

- RT scan functions run from flash: `rt_scan_normalize` and `rt_update_scan_travel` link at
  `0x0801...` and are called through a veneer. `rapid_trigger.c` does not include `config.h`, so
  its `HHKB_SCAN_1KHZ` guard never applies (fixed in the fork by `ff8e5ae`).
- Baselines below 100 counts are rejected on load (`>= 100`), so a key resting at 95 (R1 C4 on
  the owner's board) loses its calibration after a power cycle (fixed by `e24d47d`, floor 20).
- Calibration bookkeeping runs per key inside the scan (after interrupts are restored), which
  shifted baselines in the fork before `93a55eb`. Nothing paces the peak phase or keeps the
  OpAmp-off gap normal (`b30c317`, `d2d362c`).

## Proposed plan

Step 0: run a `dev` diagnostic build on the owner's Hybrid ANSI (idle, typing, RT on and off) to
get hardware `[SCAN]` numbers for the pull requests. `dev` migrates settings to its snapshot
format and overwrites the fork's per-key record: save the VIA layout first and expect to
recalibrate and re-enter per-key RT settings after returning to the fork.

Then one branch per pull request, each from `upstream/dev`, kept in this repository until the
owner decides to open them. `feat/hhkb-per-key-rt` stays the daily firmware.

| Branch | Content | Effort |
| --- | --- | --- |
| `upstream/rt-ram-and-base-floor` | Include `config.h` in `rapid_trigger.c` (RT code in RAM); baseline floor 20 instead of 100 | Small, a few lines |
| `upstream/scan-1khz` | Key processing during the C57 discharge and a constant-length key path, ported onto the `dev` pipeline, with hardware numbers before and after | Medium, needs hardware |
| `upstream/calibration-timing` | Calibration bookkeeping after the scan; peak phase every other period with the OpAmp kept on, adapted to the request mechanism | Medium, needs hardware |
| `upstream/per-key-rt` | Per-key RT rewritten for the snapshot storage | Large; ask the author first |

`CLAUDE.md` and `HANDOFF.md` are fork-specific and do not go upstream. Each pull request gets a
short description (problem, change, hardware measurements, tested only on Hybrid ANSI).
