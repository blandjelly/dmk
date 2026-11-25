"""Tests for tools/hhkb/usb_report_gaps.py on synthetic USBPcap and usbmon captures."""
import importlib.util
import io
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools/hhkb/usb_report_gaps.py"
spec = importlib.util.spec_from_file_location("usb_report_gaps", TOOL)
gaps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gaps)


def kb_report(*codes, mods=0):
    codes = list(codes) + [0] * (6 - len(codes))
    return bytes([1, mods, 0] + codes)


def usbpcap_packet(payload, dev=5, ep=0x81, info=1, transfer=1):
    hdr = struct.pack("<HQIHBHHBBI", 27, 0x1234, 0, 9, info, 1, dev, ep, transfer, len(payload))
    return hdr + payload


def pcapng_block(btype, body):
    pad = (-len(body)) % 4
    body += b"\0" * pad
    length = 12 + len(body)
    return struct.pack("<II", btype, length) + body + struct.pack("<I", length)


def make_pcapng(packets, tsresol=None):
    """packets: list of (time_us, bytes)."""
    shb = pcapng_block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    opts = b""
    if tsresol is not None:
        opts = struct.pack("<HHB", 9, 1, tsresol) + b"\0\0\0" + struct.pack("<HH", 0, 0)
    idb = pcapng_block(1, struct.pack("<HHI", gaps.LINKTYPE_USBPCAP, 0, 65535) + opts)
    out = shb + idb
    scale = 1 if tsresol in (None, 6) else 1000
    for t_us, pkt in packets:
        ts = t_us * scale
        body = struct.pack("<IIIII", 0, ts >> 32, ts & 0xFFFFFFFF, len(pkt), len(pkt)) + pkt
        out += pcapng_block(6, body)
    return out


def make_usbmon_pcap(packets):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, gaps.LINKTYPE_USB_LINUX_MMAPPED)
    for t_us, payload in packets:
        hdr = struct.pack("<QBBBBHBBqiiII", 1, ord("C"), 1, 0x81, 7, 3, 0, 0, 0, 0, 0,
                          len(payload), len(payload)) + b"\0" * 24
        pkt = hdr + payload
        out += struct.pack("<IIII", t_us // 1_000_000, t_us % 1_000_000, len(pkt), len(pkt)) + pkt
    return out


class UsbReportGapsTest(unittest.TestCase):
    def write(self, data, suffix):
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        f.write(data)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_usbpcap_gaps_and_filtering(self):
        t = 1_000_000
        packets = [
            (t, usbpcap_packet(kb_report(4))),
            (t + 1000, usbpcap_packet(kb_report())),
            (t + 2000, usbpcap_packet(kb_report(5))),
            (t + 4000, usbpcap_packet(kb_report())),          # missed a frame: 2 ms
            (t + 4100, usbpcap_packet(kb_report(), info=0)),  # submit, not a completion
            (t + 4200, usbpcap_packet(b"\x02\xe9\x00", ep=0x81)),  # consumer report
            (t + 4300, usbpcap_packet(bytes(32), ep=0x83)),   # console
            (t + 60000, usbpcap_packet(kb_report(6))),        # pause
        ]
        path = self.write(make_pcapng(packets), ".pcapng")
        events = list(gaps.interrupt_in_completions(path))
        streams = gaps.find_streams(events)
        key = gaps.pick_keyboard_stream(streams)
        self.assertEqual(key, (1, 5, 0x81))
        reports = [(ts, p) for ts, k, p in events if k == key and gaps.is_keyboard_report(p)]
        r = gaps.analyse(reports)
        self.assertEqual(r["reports"], 5)
        self.assertEqual(r["classes"]["~1 ms"], 2)
        self.assertEqual(r["classes"]["~2 ms"], 1)
        self.assertEqual(r["classes"][">10 ms (pause)"], 1)
        self.assertEqual(r["burst_gaps"], 3)
        self.assertEqual(r["changes"][1], 4)
        self.assertEqual(r["duplicates"], 0)

    def test_nanosecond_resolution_and_usbmon(self):
        base = [(2_000_000 + i * 1000, kb_report(4 + i % 2)) for i in range(10)]
        ng = self.write(make_pcapng([(t, usbpcap_packet(p)) for t, p in base], tsresol=9), ".pcapng")
        mon = self.write(make_usbmon_pcap(base), ".pcap")
        for path in (ng, mon):
            events = list(gaps.interrupt_in_completions(path))
            key = gaps.pick_keyboard_stream(gaps.find_streams(events))
            reports = [(ts, p) for ts, k, p in events if k == key]
            r = gaps.analyse(reports)
            self.assertEqual(r["classes"]["~1 ms"], 9, path)
            self.assertEqual(r["peak_per_100ms"], 10)

    def test_main_compares_files(self):
        a = self.write(make_pcapng([(i * 1000, usbpcap_packet(kb_report(4 + i % 2))) for i in range(5)]), ".pcapng")
        b = self.write(make_pcapng([(i * 2000, usbpcap_packet(kb_report(4 + i % 2))) for i in range(5)]), ".pcapng")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(gaps.main([a, b]), 0)
        self.assertIn("comparison", out.getvalue())

    def test_no_keyboard_stream(self):
        path = self.write(make_pcapng([(0, usbpcap_packet(bytes(32), ep=0x83))]), ".pcapng")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(gaps.main(["--list", path]), 0)
        self.assertIn("endpoint 0x83", out.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(gaps.main([path]), 1)


if __name__ == "__main__":
    unittest.main()
