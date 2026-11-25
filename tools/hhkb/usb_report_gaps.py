#!/usr/bin/env python3
"""Measure host-observed intervals between HHKB keyboard reports in a USB capture.

Reads Wireshark captures (.pcapng or .pcap) made with USBPcap on Windows or usbmon on
Linux. No extra packages are needed.

The HID polling rate is fixed by bInterval=1 (the host asks every 1 ms). A keyboard
only answers with a report when its state changes, so this tool looks at the gaps
between reports that did arrive: during fast typing with Rapid Trigger, gaps of 1 ms
mean the firmware delivered a report in every frame it had one for, and gaps of 2-3 ms
inside a burst mean a report missed a frame.

Examples:
  python usb_report_gaps.py rt_on_1000.pcapng
  python usb_report_gaps.py rt_on_1000.pcapng rt_off_1000.pcapng rt_on_500.pcapng
  python usb_report_gaps.py --list capture.pcapng
  python usb_report_gaps.py --device 1:5 --csv gaps.csv capture.pcapng
"""

import argparse
import struct
import sys
from collections import Counter

LINKTYPE_USB_LINUX = 189
LINKTYPE_USB_LINUX_MMAPPED = 220
LINKTYPE_USBPCAP = 249

USBPCAP_HEADER = struct.Struct("<HQIHBHHBBI")
USBMON_HEADER = struct.Struct("<QBBBBHBBqiiII")
TRANSFER_INTERRUPT = 1

# Gap classes in milliseconds: (label, upper bound).
GAP_CLASSES = (
    ("<0.75 ms", 0.75),
    ("~1 ms", 1.5),
    ("~2 ms", 2.5),
    ("~3 ms", 3.5),
    ("3.5-10 ms", 10.0),
    (">10 ms (pause)", float("inf")),
)
BURST_LIMIT_MS = 3.5


class CaptureError(Exception):
    pass


def read_packets(path):
    """Yield (timestamp_seconds, linktype, packet_bytes) from a pcapng or pcap file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 24:
        raise CaptureError(f"{path}: file too short")
    if data[:4] == b"\x0a\x0d\x0d\x0a":
        yield from _read_pcapng(data, path)
    else:
        yield from _read_pcap(data, path)


def _read_pcap(data, path):
    magic = data[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
        b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9),
    }
    if magic not in formats:
        raise CaptureError(f"{path}: not a pcap or pcapng file")
    endian, frac = formats[magic]
    linktype = struct.unpack_from(endian + "I", data, 20)[0] & 0x0FFFFFFF
    pos = 24
    rec = struct.Struct(endian + "IIII")
    while pos + rec.size <= len(data):
        sec, sub, incl, _orig = rec.unpack_from(data, pos)
        pos += rec.size
        yield sec + sub * frac, linktype, data[pos:pos + incl]
        pos += incl


def _read_pcapng(data, path):
    pos = 0
    endian = "<"
    interfaces = []
    while pos + 12 <= len(data):
        if data[pos:pos + 4] == b"\x0a\x0d\x0d\x0a":
            bom = data[pos + 8:pos + 12]
            if bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise CaptureError(f"{path}: bad pcapng byte-order magic")
            interfaces = []
        btype, blen = struct.unpack_from(endian + "II", data, pos)
        if blen < 12 or pos + blen > len(data):
            break  # truncated capture: keep what was read
        body = data[pos + 8:pos + blen - 4]
        if btype == 1:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            interfaces.append((linktype, _pcapng_tsresol(body[8:], endian)))
        elif btype == 6:  # Enhanced Packet Block
            iface, ts_hi, ts_lo, cap_len, _orig = struct.unpack_from(endian + "IIIII", body, 0)
            if iface < len(interfaces):
                linktype, resol = interfaces[iface]
                yield ((ts_hi << 32) | ts_lo) * resol, linktype, body[20:20 + cap_len]
        pos += blen


def _pcapng_tsresol(options, endian):
    pos = 0
    while pos + 4 <= len(options):
        code, length = struct.unpack_from(endian + "HH", options, pos)
        if code == 0:
            break
        if code == 9 and length >= 1:
            v = options[pos + 4]
            return 2.0 ** -(v & 0x7F) if v & 0x80 else 10.0 ** -v
        pos += 4 + ((length + 3) & ~3)
    return 1e-6


def interrupt_in_completions(path):
    """Yield (timestamp, (bus, device, endpoint), payload) for interrupt IN data."""
    for ts, linktype, pkt in read_packets(path):
        if linktype == LINKTYPE_USBPCAP:
            if len(pkt) < USBPCAP_HEADER.size:
                continue
            (hdr_len, _irp, _status, _func, info, bus, dev, ep, transfer,
             data_len) = USBPCAP_HEADER.unpack_from(pkt, 0)
            # info bit 0: completion travelling back to the host (PDO -> FDO).
            if transfer != TRANSFER_INTERRUPT or not ep & 0x80 or not info & 1 or data_len == 0:
                continue
            yield ts, (bus, dev, ep), pkt[hdr_len:hdr_len + data_len]
        elif linktype in (LINKTYPE_USB_LINUX, LINKTYPE_USB_LINUX_MMAPPED):
            if len(pkt) < USBMON_HEADER.size:
                continue
            (_id, ev_type, xfer, ep, dev, bus, _setup, _flag_data, _sec, _usec, status,
             _length, len_cap) = USBMON_HEADER.unpack_from(pkt, 0)
            if ev_type != ord("C") or xfer != TRANSFER_INTERRUPT or not ep & 0x80:
                continue
            if status != 0 or len_cap == 0:
                continue
            offset = 64 if linktype == LINKTYPE_USB_LINUX_MMAPPED else 48
            yield ts, (bus, dev, ep), pkt[offset:offset + len_cap]


def is_keyboard_report(payload):
    """HHKB/DMK keyboard report: report ID 1, modifiers, reserved, six keycodes."""
    return len(payload) == 9 and payload[0] == 1


def find_streams(events):
    streams = {}
    for ts, key, payload in events:
        s = streams.setdefault(key, {"count": 0, "keyboard": 0, "lengths": Counter()})
        s["count"] += 1
        s["lengths"][len(payload)] += 1
        if is_keyboard_report(payload):
            s["keyboard"] += 1
    return streams


def pick_keyboard_stream(streams, device=None):
    candidates = [(s["keyboard"], key) for key, s in streams.items() if s["keyboard"]]
    if device is not None:
        candidates = [c for c in candidates if c[1][:2] == device]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if len(candidates) > 1 and device is None:
        print(f"note: several keyboard-like streams found, using bus {candidates[0][1][0]} "
              f"device {candidates[0][1][1]}; see --list and --device", file=sys.stderr)
    return candidates[0][1]


def pressed_set(payload):
    mods = {("mod", bit) for bit in range(8) if payload[1] & (1 << bit)}
    keys = {("key", code) for code in payload[3:9] if code}
    return mods | keys


def classify(gap_ms):
    for label, upper in GAP_CLASSES:
        if gap_ms < upper:
            return label
    return GAP_CLASSES[-1][0]


def analyse(reports):
    """reports: list of (timestamp_seconds, payload) for one keyboard endpoint."""
    reports = sorted(reports, key=lambda r: r[0])
    gaps = [(b[0] - a[0]) * 1000.0 for a, b in zip(reports, reports[1:])]
    classes = Counter(classify(g) for g in gaps)
    burst = [g for g in gaps if g < BURST_LIMIT_MS]
    burst_classes = Counter(classify(g) for g in burst)

    changes = Counter()
    duplicates = 0
    for (_, prev), (_, cur) in zip(reports, reports[1:]):
        diff = len(pressed_set(prev) ^ pressed_set(cur))
        if diff == 0:
            duplicates += 1
        changes[min(diff, 3)] += 1

    # Most reports in any 100 ms window.
    times = [r[0] for r in reports]
    best = 0
    j = 0
    for i, t in enumerate(times):
        while times[j] < t - 0.1:
            j += 1
        best = max(best, i - j + 1)

    return {
        "reports": len(reports),
        "duration_s": (times[-1] - times[0]) if len(times) > 1 else 0.0,
        "gaps": gaps,
        "classes": classes,
        "burst_gaps": len(burst),
        "burst_classes": burst_classes,
        "min_gap_ms": min(gaps) if gaps else None,
        "peak_per_100ms": best,
        "duplicates": duplicates,
        "changes": changes,
    }


def pct(part, whole):
    return f"{100.0 * part / whole:5.1f} %" if whole else "    -  "


def print_result(path, key, r):
    print(f"\n=== {path}")
    print(f"stream: bus {key[0]}, device {key[1]}, endpoint 0x{key[2]:02x}")
    print(f"keyboard reports: {r['reports']} over {r['duration_s']:.1f} s; "
          f"most in 100 ms: {r['peak_per_100ms']} (= {r['peak_per_100ms'] * 10} reports/s)")
    if not r["gaps"]:
        print("not enough reports to measure gaps")
        return
    print(f"shortest gap: {r['min_gap_ms']:.3f} ms")
    print("all gaps:")
    for label, _ in GAP_CLASSES:
        n = r["classes"][label]
        print(f"  {label:<16} {n:6d}  {pct(n, len(r['gaps']))}")
    print(f"gaps inside bursts (< {BURST_LIMIT_MS} ms): {r['burst_gaps']}")
    for label in ("<0.75 ms", "~1 ms", "~2 ms", "~3 ms"):
        n = r["burst_classes"][label]
        print(f"  {label:<16} {n:6d}  {pct(n, r['burst_gaps'])}")
    ch = r["changes"]
    print(f"key changes per report: 1: {ch[1]}, 2: {ch[2]}, 3+: {ch[3]}, "
          f"none (repeated report): {r['duplicates']}")
    if r["burst_classes"]["<0.75 ms"] > r["burst_gaps"] * 0.05:
        print("note: many gaps under 0.75 ms. The host timestamps completions when its driver "
              "handles them, so some reports arrive bunched. Read ~1/~2 ms together with "
              "<0.75 ms: a short gap often follows a long one.")


def print_comparison(results):
    print("\n=== comparison (gaps inside bursts)")
    header = f"{'file':<32} {'reports':>8} {'peak/s':>7} {'~1 ms':>8} {'~2 ms':>8} {'~3 ms':>8} {'<0.75':>8}"
    print(header)
    for path, _key, r in results:
        b = r["burst_gaps"]
        bc = r["burst_classes"]
        name = path if len(path) <= 32 else "..." + path[-29:]
        print(f"{name:<32} {r['reports']:>8} {r['peak_per_100ms'] * 10:>7} "
              f"{pct(bc['~1 ms'], b):>8} {pct(bc['~2 ms'], b):>8} {pct(bc['~3 ms'], b):>8} "
              f"{pct(bc['<0.75 ms'], b):>8}")


def parse_device(text):
    try:
        bus, dev = text.split(":")
        return int(bus), int(dev)
    except ValueError:
        raise argparse.ArgumentTypeError("use BUS:DEVICE, e.g. 1:5")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="+", help=".pcapng or .pcap files")
    parser.add_argument("--list", action="store_true", help="list interrupt IN streams and exit")
    parser.add_argument("--device", type=parse_device, help="keyboard as BUS:DEVICE (see --list)")
    parser.add_argument("--csv", help="write every gap (file, ms) to this CSV file")
    args = parser.parse_args(argv)

    results = []
    for path in args.captures:
        try:
            events = list(interrupt_in_completions(path))
        except (OSError, CaptureError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        streams = find_streams(events)
        if args.list:
            print(f"=== {path}")
            if not streams:
                print("no interrupt IN data found (was the right USBPcap interface captured?)")
            for key, s in sorted(streams.items()):
                lengths = ", ".join(f"{n} B x{c}" for n, c in s["lengths"].most_common(4))
                print(f"bus {key[0]} device {key[1]} endpoint 0x{key[2]:02x}: {s['count']} "
                      f"packets ({lengths}), keyboard reports: {s['keyboard']}")
            continue
        key = pick_keyboard_stream(streams, args.device)
        if key is None:
            print(f"error: {path}: no keyboard reports (9 bytes, report ID 1) found; "
                  f"try --list", file=sys.stderr)
            return 1
        reports = [(ts, p) for ts, k, p in events if k == key and is_keyboard_report(p)]
        r = analyse(reports)
        results.append((path, key, r))
        print_result(path, key, r)

    if len(results) > 1:
        print_comparison(results)
    if args.csv and results:
        with open(args.csv, "w", encoding="utf-8") as f:
            f.write("file,gap_ms\n")
            for path, _key, r in results:
                for g in r["gaps"]:
                    f.write(f"{path},{g:.4f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
