# Proposal: more accurate and easier to check key calibration

## Summary

While working on Rapid Trigger in my fork ([blandjelly/dmk](https://github.com/blandjelly/dmk), branch
`feat/hhkb-per-key-rt`) I found several problems with calibration on my Hybrid ANSI (AHUX01). Most of
them are also in `dev`: the saved baseline is biased by scan timing, a key resting below 100 counts
loses its calibration after power-up, and it is hard to see which keys a calibration missed. I'd like
to propose the fixes below as one or more PRs against `dev`.

## Problems and fixes

### 1. Calibration scans are slower than normal scans, so the baseline comes out too high

The ADC reading of a key depends on how long the scan spends on the previous key before the next
strobe. In `dev`, `hhkb_cal_accumulate_baseline` / `hhkb_cal_record_peak` run inside the scan loop
for every key (`scan_report_pending`), so during calibration each key's step is longer than in normal
use. In the emulator this was 7.6 µs instead of 6.6 µs from row release to the next strobe. The
captured baseline then differs from what the same key reads during normal scans: on my keyboard keys
rested about 10 counts away from their fresh baseline because of it.

**Fix ([93a55eb](https://github.com/blandjelly/dmk/commit/93a55eb)):** during calibration the scan
only stores each sample in a buffer (one store per key); the baseline and peak bookkeeping run after
the loop. A calibration scan then times each key like a normal one (6.6 µs vs 6.4 µs). The buffer
takes 120-150 bytes of RAM, so Hybrid JIS keeps the inline path (it had 88 bytes free with GCC 13.2).

### 2. Calibration scans overrun the 1 ms period

Even with the bookkeeping after the loop, calibration scans were longer than 1 ms on my keyboard
(emulated: capture 845-883 µs and peak phase 971 µs, against 737 µs normally; the keyboard is about
18 % slower). They then ran at 500 Hz with the OpAmp off for about 1.1 ms between scans instead of
about 0.13 ms, which shifts the first key's reading (R0 C0 rested about 85 counts below its baseline).

**Fix ([457f147](https://github.com/blandjelly/dmk/commit/457f147),
[b30c317](https://github.com/blandjelly/dmk/commit/b30c317),
[d2d362c](https://github.com/blandjelly/dmk/commit/d2d362c)):**
- The OpAmp is switched off before the calibration work, so it is on for the same time in every scan.
- `hhkb_cal_process_scan`: the baseline capture is one tight loop; the peak phase covers half of the
  keys per scan and does full work only for keys that set a new peak or cross a threshold. A host test
  compares it with the full update over 20000 random scans.
- The peak phase scans every other period (500 Hz). At 1000 Hz it fit the period only just (about
  990 µs on hardware) and left the USB task almost no CPU: Key Tester showed stuck keys and the
  keyboard stopped responding after about 20 s. The 16-scan baseline capture keeps 1000 Hz.
- In the skipped period the OpAmp stays on with C57 held discharged, as between keys, and goes off
  before the next scan for the same time as before a normal 1 kHz scan. Without this, R0 C0
  (L-Shift, the first key read after the OpAmp switches on) read +90..+130 counts above its baseline
  during the peak phase, and once counted as pressed by itself at exactly its threshold. The peak
  phase uses the timer divider instead of an extra wait, so slower scan rates no longer halve during
  calibration.

Verified on my keyboard: the peak phase runs at exactly 500 Hz without missed periods, the USB loop
stays at 520-530 Hz, R0 C0 no longer stands out, and the margin of the noisiest resting key to the
+150 count threshold went from about 20 to about 75 counts. Calibration saves 60/60 and the saved
record matches RAM.

### 3. A key resting below 100 counts loses its calibration after power-up

After a 60/60 calibration VIA showed "59/60 saved custom ranges". The record was saved completely, but
one key (R1 C4, "4") rests at 99 counts. Loading and the status label accept only baselines
`>= 100` (`ec_settings.c` in `dev`, lines 148, 390, 413 and 759), so after the next power-up that key
fell back to the default range (baseline 800) and behaved very differently from the others.

**Fix ([e24d47d](https://github.com/blandjelly/dmk/commit/e24d47d)):** the floor only has to reject
erased or zeroed records, so it is now `HHKB_CAL_BASE_MIN` (20), used in all those places. A host test
loads a 99-count baseline. Confirmed on my keyboard: "4" captured a baseline of 95, saved 60/60, and
works normally after a replug. A smaller follow-up:
`hhkb_calibration_finish` still accepts a captured baseline below the floor, which would be saved and
then dropped the same way.

### 4. Resting keys take different code paths around the baseline

At or below its baseline a key took a shortcut; just above it, the full curve path (about 16
instructions longer). Resting noise crosses the baseline all the time, so the next key's reading
jumped: R0 C1 alternated between about 147 and 183 counts, and the average offset from a fresh baseline
swung between -40 and +38. Without the shortcut it stays at -5..0
([e5e64ee](https://github.com/blandjelly/dmk/commit/e5e64ee)). In `dev`, `rt_scan_normalize` still
returns early for `raw <= key->base`. This is also part of the [1000 Hz issue](https://github.com/blandjelly/dmk/blob/feat/hhkb-per-key-rt/keyboards/hhkb/docs/upstream-proposals/scan-1000hz.md).

### 5. It is hard to see which keys a calibration missed

The progress label shows a count (for example 57/60) but not which keys are missing, and it only
refreshes on a submenu change. **"Print status to HID Console"** now also prints:

- during a calibration, the keys not pressed yet: `[CAL] missing: R0 C1 R2 C5 ...`
  ([de60609](https://github.com/blandjelly/dmk/commit/de60609));
- after **Finish and save**, the keys the calibration left unchanged: `[CAL] unchanged: ...`
  ([4beb315](https://github.com/blandjelly/dmk/commit/4beb315));
- in `HHKB_SCAN_DIAGNOSTICS` builds only, whether the saved record matches RAM:
  `[CAL] record!=RAM:<n>` and the keys the status label does not count, with their stored values
  ([5e38e9e](https://github.com/blandjelly/dmk/commit/5e38e9e)).

The guide (`via-debug.md`, EN and RU) now describes the procedure: release all keys, press each key
to the bottom once with normal force (much harder presses raise the peak, because a bottomed-out Topre
key reads finger pressure), and check the console for missing keys. The `row,col` labels in `via.json`
name the key at each position.

## Open problems

**Keys missed by a calibration.** Earlier calibrations sometimes ended at 59/60, 57/60 or 45/60 although
Key Tester showed every key pressed. Part of this was problem 3 and the status label, which is not
live. All my recent calibrations reached 60/60. If it comes back, my suspect is a key touched during
the 16 ms baseline capture: it gets a high baseline and can never pass `baseline + RT_MIN_RANGE`.
Delaying the capture or rejecting a capture far above the previous baseline would help.

**R0 C0 (L-Shift) is sensitive to how long the OpAmp was off.** It is the first key read after the
OpAmp switches on. On my keyboard it rests at -30..+20 counts from its baseline most of the time, but
for stretches of a few seconds at +55..+95, together with a slightly shorter scan (846-849 µs instead of
853-858 µs). My guess is the USB interrupt drifting against the TIM2 scan period. Its calibration is
less repeatable than the other keys' (baselines 249-335, peaks 2416-2938 across sessions), so it
actuates at 75-88 % of its real range instead of 75 %. With the EC stream on it reads about +110 at
rest. This is at most about 4 % of travel and causes no false presses, but it would matter with a very
shallow actuation point. A fixed OpAmp settle time before the first key (15-60 µs, there is about
110-150 µs of headroom at 1000 Hz) would probably fix it.

**Could you check whether your board shows the same?** My fork's diagnostic build (`HHKB_SCAN_DIAGNOSTICS`)
prints a `[REST]` line once a second with the R0 C0 sample, also while the key is pressed. Useful logs
would be: 60 s untouched, 2 minutes of typing, and L-Shift held for 5 s, all with the EC stream off. I
can provide a build for your board.

## Hardware status and costs

- Tested on Hybrid ANSI over USB: all of the above. The release build with these changes is in daily
  use, including Bluetooth on battery (typing, long holds, sleep and wake).
- Not tested: Classic, JIS.
- Hybrid JIS: no deferred calibration for lack of RAM (about 104 B free with GCC 13.2); the other fixes
  fit. In my fork Hybrid builds have only tens of bytes of flash left with GCC 13.2; `dev` with
  GCC 15.3 has more room.

## Proposed PR

1. Baseline floor (3) and the console output (5): small and independent of scan timing.
2. Deferred bookkeeping and calibration scan timing (1, 2). These touch `classic_matrix.c` and
   `matrix.c`, so I'd port them onto the `scan_pending_t` pipeline in `dev` and adapt them to the
   calibration requests the matrix task now applies at scan boundaries.

Would you like these as separate PRs?
