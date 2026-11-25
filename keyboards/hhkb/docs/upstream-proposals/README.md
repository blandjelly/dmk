# Proposals for upstream (aroum/dmk)

Changes from this fork that we propose to [aroum/dmk](https://github.com/aroum/dmk). Each file is the
text of a GitHub issue opened there before any PR, in English. The analysis was
done against upstream `dev` at 6ef2a5e (2026-11-24).

| File | Topic | Status upstream |
| --- | --- | --- |
| [scan-1000hz.md](scan-1000hz.md) | Bug: the scan takes longer than 1 ms, so the 1000 Hz scan rate drops to about 500 Hz | [aroum/dmk#8](https://github.com/aroum/dmk/issues/8), open; scan changes adapted in `dev` (`5d0e68e`) |
| [per-key-rapid-trigger.md](per-key-rapid-trigger.md) | Feature: per-key Rapid Trigger, per-key actuation point, bottom dead zone | [aroum/dmk#11](https://github.com/aroum/dmk/issues/11), open; bottom-zone safeguard in `dev` (`6008891`), per-key parts pending |
| [calibration.md](calibration.md) | Calibration accuracy and diagnostics | [aroum/dmk#12](https://github.com/aroum/dmk/issues/12), open; partly adapted in `dev` (`65c7b02`) |

The issue on GitHub still uses the original title of #8
("polling rate"), while this copy says "scan rate": the USB polling rate stays at 1000 Hz either way.
