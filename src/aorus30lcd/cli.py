"""aorus30-lcd: the AORUS RTX 30-series LCD Edge View from Linux.

  status                   D6/DE/DF/D8 queries (read 4 each, like Panel.exe)
  probe                    address-only pings of the LCD (0x61) and its bootloader; sends no data
  mode N                   E5: 0-2 logo animations, 3 image, 4 text, 5 gif, 6 pet, 9 carousel
  save                     AA: persist the current mode/config in the panel
  image FILE [--fit]       convert to 320x170 RGB565, upload, show (mode 3) and save
  testcard OUT.png         write a 320x170 test card (colour bars, corner markers)
  overlay off|WIDGET.. [--interval S] [--save]
                           E1: which stats the panel draws over the content (no flash write)
  feed temp=42 clock=1234 ...
                           E3: one set of values for the overlay
  live [--period S] [--set SLOT=SOURCE ...]
                           E3 every S seconds (like MonitorService.exe); no flash writes.
                           SOURCE: gpu_temp gpu_clock gpu_usage gpu_fan vram_clock vram_usage
                           gpu_power cpu_temp cpu_usage zero, a number, or cmd:<shell command>
  template [--color RRGGBB] [--pos X,Y] [--kind 1|2] [--save]
                           EB/ED read the overlay style; with options, EA writes colour/position

All bus access holds the "aorus-lcd" lock (shared with c-drew's aorus-lcd tool), so a running
`live` service pauses while an image upload or another command uses the bus.
"""
import argparse
import contextlib
import sys
import time

from . import __version__
from . import protocol as P
from .sources import DEFAULT_SOURCES, Sources

LCD_SUBSYSTEMS = {0x403D, 0x403E, 0x4041, 0x4042}   # Panel.exe's list of LCD cards

LOCK = contextlib.nullcontext()                    # replaced by a BusLock unless --dry-run


class DryRun:
    """Prints frames instead of touching the GPU; answers queries like firmware 0x20 would."""

    def write(self, addr, data, speed_khz=None):
        print(f"W {addr:#04x} [{len(data)}] {data[:24].hex(' ')}{' …' if len(data) > 24 else ''}")

    def read(self, addr, n, speed_khz=None):
        print(f"R {addr:#04x} [{n}]")
        return bytes([0, P.EXPECTED_FW, 0, 0][:n]) + bytes(max(0, n - 4))

    def ping(self, addr, speed_khz=None):
        print(f"P {addr:#04x} @ {speed_khz or 400} kHz")
        return False

    def close(self):
        pass


def open_bus(args):
    if args.dry_run:
        return DryRun()
    from .transport import find_gpu
    from .transport.nvrm import NvRmTransport
    try:
        gpu = find_gpu(args.gpu)
    except LookupError as e:
        sys.exit(str(e))
    if gpu.subdevice not in LCD_SUBSYSTEMS and not args.any_card:
        sys.exit(f"{gpu.describe()} is not one of Gigabyte's 30-series LCD cards "
                 f"({', '.join(f'{s:04x}' for s in sorted(LCD_SUBSYSTEMS))}); refusing")
    if not gpu.verified:
        print(f"note: {gpu.describe()} is untested; please report results", file=sys.stderr)
    return NvRmTransport(gpu, 400, port=1)


def query(bus, op, n=4):
    bus.write(P.ADDR, P.frame(op))
    return bus.read(P.ADDR, n)


def cmd_status(bus, args):
    for name, op in (("firmware", P.OP_GET_FW), ("mode", P.OP_GET_MODE), ("display", P.OP_GET_DISPLAY),
                     ("double status", P.OP_DOUBLE_STATUS)):
        print(f"{name:14} {query(bus, op).hex(' ')}")


def cmd_probe(bus, args):
    from .transport import BOOTLOADER_ADDRESSES
    for addr in (P.ADDR, *BOOTLOADER_ADDRESSES):
        row = []
        for speed in (400, 100):
            try:
                row.append(f"{speed} kHz: {'ACK' if bus.ping(addr, speed) else '--'}")
            except OSError as e:
                row.append(f"{speed} kHz: error ({e})")
        print(f"{addr:#04x}  " + "   ".join(row))


def cmd_mode(bus, args):
    bus.write(P.ADDR, P.set_mode(args.n))


def parse_pairs(pairs, allowed):
    out = {}
    for p in pairs:
        k, _, v = p.partition("=")
        if k not in allowed or not v:
            sys.exit(f"bad '{p}': use SLOT=VALUE with SLOT one of {', '.join(allowed)}")
        out[k] = v
    return out


def read_template(bus, kind):
    """Panel.exe FUN_00407da0: EB [kind] -> R G B enabled; ED [kind] -> X u16, Y u16 (BE)."""
    bus.write(P.ADDR, P.frame(P.OP_GET_TEMPLATE, bytes([kind])))
    a = bus.read(P.ADDR, 8)
    bus.write(P.ADDR, P.frame(P.OP_GET_TEMPLATE_POS, bytes([kind])))
    b = bus.read(P.ADDR, 8)
    return P.parse_template(a, b)


def cmd_template(bus, args):
    kinds = [args.kind] if args.kind else [1, 2]     # style is stored per mode
    for kind in kinds:
        cur = read_template(bus, kind)
        print(f"kind {kind}: color #{bytes(cur['color']).hex()} pos {cur['pos'][0]},{cur['pos'][1]} "
              f"{'enabled' if cur['enabled'] else 'disabled'}")
        if args.color or args.pos:
            color = tuple(bytes.fromhex(args.color.lstrip("#"))) if args.color else cur["color"]
            pos = tuple(int(v) for v in args.pos.split(",")) if args.pos else cur["pos"]
            bus.write(P.ADDR, P.set_template(kind, color, pos, cur["enabled"]))
            print(f"  -> color #{bytes(color).hex()} pos {pos[0]},{pos[1]}")
    if args.save and (args.color or args.pos):
        bus.write(P.ADDR, P.frame(P.OP_SAVE))


def cmd_overlay(bus, args):
    widgets = [] if args.widgets == ["off"] else args.widgets
    bad = set(widgets) - set(P.WIDGETS)
    if bad:
        sys.exit(f"unknown widgets {sorted(bad)}; choose from off or {', '.join(P.WIDGETS)}")
    bus.write(P.ADDR, P.set_display(widgets, args.interval))
    if args.save:
        time.sleep(0 if args.dry_run else 0.2)
        bus.write(P.ADDR, P.frame(P.OP_SAVE))


def cmd_feed(bus, args):
    vals = {k: float(v) for k, v in parse_pairs(args.values, P.WIDGETS).items()}
    bus.write(P.ADDR, P.sensor_feed(vals))


def cmd_live(bus, args):
    mapping = dict(DEFAULT_SOURCES)
    mapping.update(parse_pairs(args.set or [], P.WIDGETS))
    src = Sources()
    print("feeding " + ", ".join(f"{k}<-{v}" for k, v in mapping.items()) + f" every {args.period}s; Ctrl+C stops",
          flush=True)
    failures = 0
    try:
        while True:
            vals = src.read(mapping)
            try:
                with LOCK:
                    bus.write(P.ADDR, P.sensor_feed(vals))
                failures = 0
            except OSError as e:                        # includes "bus busy" lock timeouts
                failures += 1
                print(f"feed failed: {e}", file=sys.stderr)
                if failures >= 30:
                    sys.exit("30 failed feeds in a row; exiting (systemd restarts the service)")
            if args.verbose or args.dry_run:
                print(" ".join(f"{k}={vals[k]:.0f}" for k in P.WIDGETS))
            if args.count:
                args.count -= 1
                if not args.count:
                    break
            time.sleep(args.period)
    except KeyboardInterrupt:
        pass


def cmd_save(bus, args):
    bus.write(P.ADDR, P.frame(P.OP_SAVE))


def testcard():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (P.W, P.H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (255, 255, 0), (0, 255, 255)]
    bw = P.W // len(bars)
    for i, c in enumerate(bars):
        d.rectangle([i * bw, 40, (i + 1) * bw - 1, 129], fill=c)
    d.rectangle([0, 0, P.W - 1, 11], fill=(255, 0, 255))          # top stripe
    d.rectangle([0, P.H - 12, P.W - 1, P.H - 1], fill=(255, 128, 0))  # bottom stripe = payload tail
    d.text((16, 16), "TL", fill=(255, 255, 255))
    d.text((P.W - 40, P.H - 30), "BR", fill=(255, 255, 255))
    d.text((120, 16), "LINUX 3080", fill=(255, 255, 255))
    return img


def load_image(path, fit):
    from PIL import Image, ImageOps
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    if fit:   # letterbox
        return ImageOps.pad(img, (P.W, P.H), color=(0, 0, 0))
    return ImageOps.fit(img, (P.W, P.H))    # fill and crop


def cmd_image(bus, args):
    fw = query(bus, P.OP_GET_FW)
    if fw[1] != P.EXPECTED_FW and not args.force:
        sys.exit(f"panel firmware {fw[1]:#04x}, this tool was derived for {P.EXPECTED_FW:#04x}; "
                 "use --force to continue anyway")
    payload = P.bin_payload([P.rgb565(load_image(args.file, args.fit))])
    if args.save_bin:
        with open(args.save_bin, "wb") as f:
            f.write(payload)
    frames = P.upload_frames(payload, P.FB_IMAGE, 1, erase=args.erase)
    print(f"uploading {len(payload)} bytes in {len(payload) // 256 + 1} chunks to {P.FB_IMAGE:#010x}")
    bus.write(P.ADDR, P.set_mode(P.MODE_IMAGE))                # Panel.exe: image page selected
    time.sleep(0 if args.dry_run else 2.0)                     # Panel.exe sleeps 2 s first
    for i, (f, pause) in enumerate(frames):
        for attempt in range(6):
            try:
                bus.write(P.ADDR, f)
                break
            except OSError as e:
                if attempt == 5:
                    raise
                print(f"  frame {i}: {e}; retrying", file=sys.stderr)
                time.sleep(0.05)
        if not args.dry_run:
            time.sleep(pause)
            if i % 50 == 0:
                print(f"  {i}/{len(frames)}", file=sys.stderr)
    bus.write(P.ADDR, P.set_mode(P.MODE_IMAGE))
    if not args.dry_run:
        time.sleep(0.3)
    bus.write(P.ADDR, P.frame(P.OP_SAVE))
    print("done")


def build_parser():
    p = argparse.ArgumentParser(prog="aorus30-lcd", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true", help="print the frames instead of touching the GPU")
    p.add_argument("--gpu", metavar="PCI", help="PCI address of the card, if there is more than one")
    p.add_argument("--any-card", action="store_true",
                   help="skip the check against Gigabyte's list of 30-series LCD cards (risky)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sub.add_parser("status", help="read firmware, mode and overlay state").set_defaults(func=cmd_status)
    sub.add_parser("probe", help="ping the LCD addresses (no data sent)").set_defaults(func=cmd_probe)
    m = sub.add_parser("mode", help="switch the panel mode")
    m.add_argument("n", type=int, choices=sorted(P.MODES),
                   help=", ".join(f"{k} {v}" for k, v in P.MODES.items()))
    m.set_defaults(func=cmd_mode)
    i = sub.add_parser("image", help="upload a still image (writes flash)")
    i.add_argument("file")
    i.add_argument("--fit", action="store_true", help="letterbox instead of crop")
    i.add_argument("--erase", type=int, choices=[1, 2], help="override Panel.exe's erase mode")
    i.add_argument("--save-bin", help="also write the uploaded payload here")
    i.add_argument("--force", action="store_true", help="continue on an unexpected firmware version")
    i.set_defaults(func=cmd_image)
    sub.add_parser("save", help="persist the current mode/config").set_defaults(func=cmd_save)
    tp = sub.add_parser("template", help="read or set the overlay colour and position")
    tp.add_argument("--color", help="overlay text colour, RRGGBB")
    tp.add_argument("--pos", help="overlay data position X,Y (pixels)")
    tp.add_argument("--kind", type=int, choices=[1, 2], help="only this template (default both)")
    tp.add_argument("--save", action="store_true", help="persist (AA)")
    tp.set_defaults(func=cmd_template)
    o = sub.add_parser("overlay", help="choose the panel's stats widgets")
    o.add_argument("widgets", nargs="+", help=f"off, or any of {' '.join(P.WIDGETS)}")
    o.add_argument("--interval", type=int, default=3, help="seconds per widget when rotating")
    o.add_argument("--save", action="store_true", help="persist (AA)")
    o.set_defaults(func=cmd_overlay)
    f = sub.add_parser("feed", help="send one set of overlay values")
    f.add_argument("values", nargs="+", help="SLOT=NUMBER")
    f.set_defaults(func=cmd_feed)
    lv = sub.add_parser("live", help="feed live values to the overlay")
    lv.add_argument("--period", type=float, default=1.0)
    lv.add_argument("--set", action="append", metavar="SLOT=SOURCE")
    lv.add_argument("--verbose", action="store_true")
    lv.add_argument("--count", type=int, default=0, help="stop after N feeds (default: run forever)")
    lv.set_defaults(func=cmd_live)
    t = sub.add_parser("testcard", help="write a 320x170 test image")
    t.add_argument("out")
    t.set_defaults(func=None)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "testcard":
        testcard().save(args.out)
        return
    global LOCK
    bus = open_bus(args)
    if not args.dry_run:
        from .transport import BusLock                 # abstract socket "aorus-lcd", as c-drew
        LOCK = BusLock(timeout=120)
    try:
        if args.cmd == "live":
            args.func(bus, args)                         # locks per frame
        else:
            with LOCK:                                   # whole command, e.g. a full image upload
                args.func(bus, args)
    finally:
        bus.close()


if __name__ == "__main__":
    main()
