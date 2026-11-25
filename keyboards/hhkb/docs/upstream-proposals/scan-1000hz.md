# HHKB: matrix scan takes longer than 1 ms, so the 1000 Hz scan rate drops to about 500 Hz

## Summary

On my HHKB Hybrid ANSI (AHUX01, USB) the 1000 Hz matrix scan rate does not work, with Rapid Trigger on
or off. (The USB HID polling rate itself stays at 1000 Hz, `bInterval=1`; the host just gets a
fresh matrix state only every second poll.) One matrix scan takes longer than the 1 ms TIM2 period, so the matrix task misses every
other tick and VIA reports `matrix=` about 500-660 Hz. This happens on the `2026.11.22` release and on
the current `dev` branch. In my fork the scan takes about 730 µs in the emulator and about 850 µs on
the keyboard, so it runs at 1000 Hz. I'd like to open a PR against `dev` with the changes described below.

## Environment

- Keyboard: HHKB Hybrid ANSI (AHUX01), USB connection, VIA in Chrome
- Firmware: release `2026.11.22`, and `dev` at 6ef2a5e ("perf(hhkb): cache adc thresholds and pipeline matrix processing")
- Compiler: GCC 14.2.1 (xPack), Hybrid ANSI build
- Measurements: VIA HID Console (debug flags in HHKB Settings), and `tools/hhkb/scan_emu.py` from my
  fork, which runs one scan of the built ELF in Unicorn. The emulator is 10-20 % faster than my
  keyboard, so differences between builds are more reliable than its absolute times.

## Measurements

Scan time for one full matrix scan, idle keys, Hybrid ANSI build:

| Build | Emulator, RT on | Emulator, RT off | Keyboard |
| --- | --- | --- | --- |
| `2026.11.22` release | 934 µs | 934 µs | about 1065 µs, `matrix=` 500-660 Hz |
| `dev` (6ef2a5e) | **1033 µs** | 924 µs | above 1000 µs, about 500 Hz |
| `dev` + one-line fix (see 1. below) | 941 µs | 924 µs | not tested |
| my fork ([`feat/hhkb-per-key-rt`](https://github.com/blandjelly/dmk/tree/feat/hhkb-per-key-rt)) | **727 µs** | **727 µs** | about 850 µs, `matrix=` 1000 Hz |

With a diagnostic build of the release I measured, per scan on the keyboard: about 615 µs of analog
sampling (about 8.2 µs per key: 2 µs C57 discharge, 3 µs row settle, ADC conversion, overhead), about
225 µs of key logic (RT/normalization) and about 84 µs of column settle waits. These steps run one
after another, so the scan cannot fit in 1 ms on this keyboard.

## Cause

### 1. The Rapid Trigger functions run from flash instead of RAM

`keyboards/hhkb/modules/ec/rapid_trigger.c` places its scan functions in `.RamFunc` only
`#ifdef HHKB_SCAN_1KHZ`. That macro is defined in `keyboards/hhkb/config.h`, which
`rapid_trigger.c` does not include, so the condition is always false. The functions are linked into
flash and called from the RAM-resident `hhkb_matrix_scan_raw` through long-call veneers, with flash
wait states on every call:

| Build | Function | Address |
| --- | --- | --- |
| `2026.11.22` | `rt_update_curve` | `0x08019824` (flash) |
| `dev` (6ef2a5e) | `rt_scan_normalize`, `rt_update_scan_travel` | `0x08019a7c`, `0x08019ad8` (flash) |

In `dev` each key with RT on now goes through two flash functions instead of one, which explains
why `dev` is about 100 µs slower than the release with RT on and about the same with RT off.
Adding `#include "config.h"` to `rapid_trigger.c` moves them to RAM (`0x200008e8`, `0x20000940`) and
brings `dev` from 1033 to 941 µs with RT on. That is still above 1 ms on the keyboard.

### 2. The key logic runs between two analog samples

In the release, each key goes through: 2 µs wait → C57 discharge 2 µs → row strobe + 3 µs settle → ADC
conversion → row release → key logic for this sample. The key logic (3.7 µs per key in the
emulator, about 6 µs on the keyboard) adds to the analog time of every key.

The pipeline in 6ef2a5e helps less than expected: normalization overlaps the 2 µs settle and the
RT update overlaps the ADC conversion, which is shorter than the key logic. The rest still adds to the
scan time, and in the RT update case it now runs while the row strobe is held.

## How my fork fixes it

The changes are in [blandjelly/dmk](https://github.com/blandjelly/dmk), branch `feat/hhkb-per-key-rt`.

### a) RT scan code in RAM ([ff8e5ae](https://github.com/blandjelly/dmk/commit/ff8e5aef16c2407d25616c2a6f39b0c4387e1ebc), `rapid_trigger.c` part)

```c
#if defined(HHKB_SCAN_1KHZ) || defined(__arm__)
__attribute__((section(".RamFunc"), optimize("O2")))
#endif
bool rt_update_curve(...)
```

`__arm__` keeps host test builds in the default section. Including `config.h` would work as well.

### b) Process the previous key while C57 discharges for the next one ([12ee6a7](https://github.com/blandjelly/dmk/commit/12ee6a7f6ad7b971d0cb8018886508242b72e5f2))

This is the main gain. After the row release the scan starts discharging C57 right away and
processes the previous key's sample during that time. Then it waits until 4 µs (the 2 µs settle and
the 2 µs discharge from before) have passed since the release:

```c
for (row ...) {
    GPIOC->BSRR = (1U << 8);                 // discharge C57
    if (pending) scan_process(&ctx, pending_col, pending_row, pending_val);
    scan_wait_since(settle_start, 4);        // >= 4 us since the previous row release

    __disable_irq();
    GPIOC->BRR = (1U << 8);
    GPIOB->BRR = row_pins[row];              // strobe
    scan_delay_us(row_settle_us);
    uint16_t val = adc_read_sample(saved_primask);
    GPIOB->BSRR = row_pins[row];             // release
    __set_PRIMASK(saved_primask);
    settle_start = TIM2->CNT;

    pending = true; pending_col = col; pending_row = row; pending_val = val;
}
if (pending) scan_process(...);              // last key of the scan
```

- The time per key goes from "4 µs + key logic" to "the longer of 4 µs and the key logic".
- Strobe-to-ADC timing is unchanged, and each strobe still follows the previous release by at
  least 4 µs.
- The only analog change: C57 is held discharged for the whole interval instead of only its last
  2 µs. Calibration and RT work normally on my keyboard with this change (checked with `[EC]` logs).
- `scan_process` is a RAM function (`noinline`) with the per-key logic, the matrix bit update
  and the debug feed.

Result: 940 → 760-815 µs in the emulator (with RT on and the code already in RAM). On the keyboard
the scan went from about 1040 µs to 715-850 µs depending on the build, and VIA shows `matrix=` 1000 Hz.

### c) Same code path just below and just above the baseline ([e5e64ee](https://github.com/blandjelly/dmk/commit/e5e64eea52c650c95a1dd96b768330afbdd32311))

This is not strictly part of the scan rate problem, but it is related. The ADC reading of a key
depends on how long the previous key's processing took. Resting noise crosses the baseline all the
time, and the release took a shortcut at or below the baseline (`val <= key->base`) but the full
curve path just above it (about 16 instructions more). On my keyboard one resting key alternated
between about 147 and 183 counts, and the average offset from a fresh baseline swung between -40
and +38. Without the shortcut (the reading lands in the top dead zone anyway) it stays at -5..0.
In `dev`, `rt_scan_normalize` still has `if (raw <= key->base) return 0;`, so the same effect is
likely there.

### Related: calibration scans

Calibration bookkeeping inside the scan made calibration scans longer than normal ones, which
biased the baseline upward by about 10 counts and made calibration overrun 1 ms. My fork stores the
samples during the scan and processes them after it ([93a55eb](https://github.com/blandjelly/dmk/commit/93a55ebf74e21c5055bd34c4d246e515b56ab8c3),
[457f147](https://github.com/blandjelly/dmk/commit/457f147f3ccd000f4ec116eb423e06ffeb768a26),
[b30c317](https://github.com/blandjelly/dmk/commit/b30c317bf20b2e67f68c0f014b78fcfe4e705095)).
This needs more RAM than Hybrid JIS has left, and the last commit is not tested on hardware yet,
so I'd keep it out of the first PR.

## Proposed PR

My fork also has per-key actuation and RT profiles, so these commits don't cherry-pick cleanly. I'd
port only the scan changes onto the current `dev` (on top of the `scan_pending_t` pipeline from 6ef2a5e):

1. Put the RT scan functions in RAM (include `config.h` or test `__arm__`).
2. Process the pending key while C57 discharges, instead of while the ADC converts.
3. Optionally: remove the `raw <= base` shortcut so resting keys take one code path.

I'd also add `tools/hhkb/scan_emu.py` so timing changes can be checked without hardware.

Tested so far: Hybrid ANSI, over USB and over Bluetooth on battery (the scan runs at 4 MHz with
`AUTOFF` there); the build with these changes is in daily use. Not tested: Classic and JIS.
Would you accept a PR like this, and should it target `dev`?
