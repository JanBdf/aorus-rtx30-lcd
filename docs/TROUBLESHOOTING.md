# Troubleshooting and safety

## The panel does not answer (`status` fails with "I2C error (NACK or stalled bus)")

1. Run `aorus30-lcd probe`. It only sends I2C address phases (no data). On a working card,
   `0x61` answers `ACK` at 400 kHz. On the verified RTX 3080 (firmware 0x20), `0x22` also ACKs and
   `0x23` does not. Pings are harmless; never *read* from `0x22`/`0x23`.
2. If nothing answers, the panel MCU's side of the bus is probably **wedged**. This happens
   after a *read* from the wrong address (for example `i2cdetect -r` on a GPU bus). A reboot does
   not fix it, because the GPU keeps standby power. Either:
   - suspend to RAM for ~30 s (`systemctl suspend`) and resume: S3 removes the 12 V rails,
     which is enough to reset the panel MCU, or
   - shut down, switch the PSU off for ~30 s, power on.
3. Make sure no other program uses the bus at the same time (OpenRGB, another LCD tool).
   This tool takes the `aorus-lcd` lock, as c-drew's tool does, but other software does not.

## Things never to do on these cards

- **Never run `i2cdetect -r`** (read probes) or any read scan on the NVIDIA I2C adapters. A read
  of the wrong device can hold the bus until power is removed.
- **Never read from the RGB controller** or from the bootloader addresses without a status
  request first.
- **Don't use RTX 5090 / 40-series LCD tools to write to a 30-series panel.** Their upload header
  targets the 5090's framebuffer addresses (`0x01300000` / `0x01320000`). The 30-series panel
  uses `0x01F25800` / `0x01F00000`, and what lives at the 5090 addresses on this flash is unknown.
  Read-only commands (status/query) are fine.
- **Never run Gigabyte's panel firmware updater** (`FWUpgrade.exe` / `AP` files) unless you know
  exactly which firmware your panel needs.

## Flash wear

Every `image` upload erases flash (two 64 KB blocks for a still image). Uploading a picture
occasionally is fine. Don't drive live data through `image`. For live data use `live` (the
panel's own stats overlay), which writes no flash.

## `live` shows zeros

- GPU values come from `nvidia-smi`. Check that it runs as your user.
- `cpu_temp` reads the first hwmon device named `k10temp`, `coretemp` or `zenpower`.
- `cmd:<shell command>` must print a number as its first word.
- Run with `--verbose` to see what is fed every tick. Errors are printed per slot.

## Permissions

The tool opens `/dev/nvidiactl` and `/dev/nvidiaN`, like any CUDA or OpenGL program, and needs no
root. If your user can run `nvidia-smi`, it can use this tool.

## The card is refused

`aorus30-lcd` only talks to cards whose PCI subsystem is on Gigabyte's own list of 30-series LCD
cards (`1458:403D`, `403E`, `4041`, `4042`). Check yours with
`lspci -nn -d 10de: -v | grep Subsystem`. `--any-card` overrides the check. Use it only if you know
your panel speaks this protocol (`status` must show firmware byte `0x20` first).
