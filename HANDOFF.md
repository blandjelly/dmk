# Handoff: HHKB scan rate and calibration work

State at the end of the session of 2026-11-23, branch `feat/hhkb-per-key-rt` (blandjelly/dmk).
Hardware: the owner's **HHKB Hybrid ANSI** (AHUX01), USB, VIA in Chrome. The owner flashes `.hfb`
files built from the stock `HHKB800_FW_A048.hfb` plus our STM32 `.bin` (`tools/hhkb/patch_hybrid_hfb.py`
or `tools/hhkb/hhkb.html`). Built files live in `build/` (ignored by git); rebuild them as needed.

## What was done

| Commit | Change | Hardware result |
| --- | --- | --- |
| `ff8e5ae` | Scan config rebuilt only after a settings change (revision bumped in `hhkb_settings_mark_dirty`); per-key profile from a table; `rt_update_curve` really in RAM | Not enough alone |
| `bebc3a8` (PR #1, merged) | Hybrid fits Bank 2 with GCC 13.2 | - |
| `12ee6a7` | Key logic for a sample runs while C57 discharges for the next key | **1000 Hz**: scan `raw` about 715-740 us (max about 800), was about 1040 |
| `4fc7182` + revert `157ce23` | Tried a fixed 6 us release-to-strobe gap with interrupts off | Failed: per-key work on hardware is about 6 us, so 18 of 75 keys per scan overran; scan grew to 895 us and the USB task loop fell from 500 to 110 Hz. Reverted |
| `079e8a9` | Docs: EC stream shifts resting readings | - |
| `93a55eb` | Calibration samples stored during the scan, bookkeeping after the loop (not on Hybrid JIS: no RAM) | Resting offset after calibration -50 -> -40 counts |
| `de60609`, `4beb315` | "Print status to HID Console" lists keys still missing (`[CAL] missing: R.. C..`) and, after Finish, keys left unchanged (`[CAL] unchanged:`); procedure in via-debug.md; `hybrid.c` built with `-fno-data-sections` (about 200 B flash) | Works |
| `e5e64ee` | Resting keys take the same code path just below and above their baseline (no shortcut in the scan, no early return in `normalize()`) | Verified 2026-11-23: `avg` -5..0 (was -40..+38), R0C1 178-189 at rest (no 147/183 alternation) |
| `7c94823` | `CLAUDE.md`, this file, `tools/hhkb/scan_emu.py`, `[REST]` line in `HHKB_SCAN_DIAGNOSTICS` builds | - |

## Measurements and findings

- **Stock DMK (`7878092`) does not reach 1000 Hz on this board either**: `raw` about 1065 us,
  `matrix=` 500-660 Hz. The scan was over budget before the per-key RT work. Upstream may want
  `12ee6a7` and `e5e64ee`.
- Breakdown of the old scan on hardware (diagnostic build, per scan): analog sampling about 615 us
  (about 8.2 us per key: 2 us discharge, 3 us settle, ADC, overhead), key logic about 225 us,
  column settle waits about 84 us.
- **ADC readings depend on scan timing.** Measured on hardware with a non-intrusive snapshot
  (`[REST]`, one store per key):
  - EC Matrix Stream on: resting keys read about +95 counts (3-4 % travel) higher. Judge resting
    values with the stream off.
  - Inline calibration bookkeeping made calibration scans slower than normal ones, so the baseline
    came out about 10 counts too high; fixed by `93a55eb` (not on Hybrid JIS).
  - R0 C1 at rest alternated between about 147 and 183 counts. The previous key's processing took
    a shortcut at or below its baseline and the full curve path above it (about 16 instructions
    more), and resting noise crosses the baseline all the time. The average offset from a fresh
    baseline swung between -40 and +38. `e5e64ee` makes both paths the same length (emulated:
    16 -> 2 instructions difference). Stock DMK has the same coupling.
- RT logic was checked against `[EC]` logs: press/release sensitivity, bottom dead zone and
  classic hysteresis behave as configured.
- `scan_emu.py` reproduces relative changes well but runs 10-30 % faster than the hardware
  (per-key logic about 3.7 us emulated vs about 6 us measured).

## Open items

1. **Calibration scans overran 1 ms; R0 C0 rests 85 counts below its fresh baseline.** Owner's logs
   (2026-11-23, diagnostic Hybrid ANSI, `e24d47d`, keyboard untouched):
   - `[SCAN] actual` 1000 Hz, `raw` 860-875 us (max about 905), `work_max` about 910 us, USB loop
     about 310 Hz. `miss` (cumulative since power-up) stood at 33999 after a calibration and at 416
     after a replug, and did not grow while idle: the misses came from calibration scans.
   - `[REST]` `avg` +1..+4, `min` -82..-114 always at R0 C0 (L-Shift, the first key of each scan,
     read soon after the OpAmp is switched on), `max` +33..+51 nearly always at R1 C3 ("1").
     R0C1 182-193 with base 182: the +28 jumps every 7 s seen earlier did not return.
   - Emulator (`scan_emu.py`, `e24d47d`): normal scan 737 us, baseline capture 845-883 us, peak
     phase 971 us at rest. On hardware (about x1.18) both phases pass 1 ms, so calibration ran at
     500 Hz with the OpAmp off for about 1.1 ms between scans instead of about 0.13 ms.
   - Change (this commit): the OpAmp is switched off before the calibration work, and that work
     is cheaper (`hhkb_cal_process_scan`: a tight loop for the baseline; for peaks, half of the
     keys per scan and a compare-only path for keys that cannot change). Emulated: capture
     783 us, peak phase 795 us at rest (normal 732 us). Expected on hardware: `miss` no longer
     grows during calibration and R0 C0 rests near its baseline.
   - **`457f147` broke calibration on hardware**: its scans took about 990 us (the emulator said
     about 795), so at 1000 Hz the matrix task left almost no CPU. USB loop 20-97 Hz (normally
     about 320), Key Tester showed stale or stuck keys, and after about 20 s the keyboard stopped
     responding until replugged. Fixed in the next commit: the peak phase scans every other
     period (500 Hz, as the old overrunning code did in effect); the 16-scan baseline capture keeps
     1000 Hz. **Not verified on hardware.**
   - Matrix task stack (1024 B): the emulator measures 816 B in the peak phase with `[CAL]`
     printing, plus 96 B for `matrix_task` and about 64 B for an interrupt and context save. An
     overflow resets the keyboard. Keep printf calls off this path or give the task more stack.
   - If R0 C0 still reads low, give the OpAmp a fixed settle time before the first key.
   - If R1 C3 stays at +40 after this, it is probably the key itself, not timing.
2. **"59/60 saved custom ranges" right after a 60/60 calibration** was one key, R1 C4 ("4"), resting
   at 99 counts: the save was complete (`[CAL] record!=RAM:0`), but loading and the status label
   accepted only baselines >= 100, so after power-up that key fell back to the default range
   (baseline 800). The floor is now `HHKB_CAL_BASE_MIN` 20. Not yet confirmed after a replug.
   `hhkb_calibration_finish` still accepts any captured baseline; one below 20 would be saved and
   then dropped the same way.
3. **Calibration often misses keys** that Key Tester shows pressed (59/60, earlier 45/60 and 57/60).
   The emulator shows the logic counting all 60 keys (`scan_emu.py cal`). A key counts once its
   reading exceeds the captured baseline + 150 counts (`RT_MIN_RANGE`), so a full press should
   always count. Suspects, in order:
   - VIA's "Calibration status" label is not live; it refreshes only on a submenu change or page
     reload. Use "Print status to HID Console", which now lists missing keys.
   - A key touched during the 16-scan (16 ms) baseline capture right after Start gets a high
     baseline and can then never pass the threshold. Possible firmware fix: delay or lengthen the
     capture, or reject keys whose capture reads far above their previous baseline.
   - Ask the owner for the `[CAL] missing:` output and the Calibration Monitor log of one session.
4. **Hybrid JIS is at its limits** with GCC 13.2: about 224 B flash and 104 B RAM free, no deferred
   calibration. Any change touching it needs savings elsewhere.
5. Not tested on hardware: Classic and JIS builds of these changes, battery (Bluetooth) mode
   after `12ee6a7`/`e5e64ee` (scan there runs at 4 MHz with interrupts enabled outside sampling).
6. The scan budget at 1000 Hz now has about 200-250 us left on Hybrid ANSI. The EC stream and
   heavy console output still push the rate below 1000 Hz; that is expected for a debug feature.

## Mapping of matrix positions to keys (ANSI)

Rows R0-R3, columns C0-C14, as printed by `[CAL]` and `[EC]` (from the `row,col` labels in `via.json`):

| | C0 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 | C10 | C11 | C12 | C13 | C14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R0 | L-Shift | Z | X | L-Opt | L-Cmd | F | Space | N | , | . | R-Cmd | M | / | R-Opt | R-Shift |
| R1 | Esc | 2 | 3 | 1 | 4 | 5 | 6 | 7 | 9 | 0 | - | 8 | = | ` | \ |
| R2 | Tab | W | E | Q | R | T | G | Y | I | O | P | U | [ | Delete | ] |
| R3 | Ctrl | S | D | A | C | V | B | H | K | L | ; | J | ' | Fn | Return |
