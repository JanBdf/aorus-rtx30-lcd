# AORUS RTX 30-series LCD Edge View on Linux

Control the **LCD Edge View** side screen of Gigabyte **AORUS GeForce RTX 30-series** cards
(verified on an RTX 3080 Master) from Linux: upload your own image, switch the built-in
modes and feed the panel's own **live stats overlay**. No Windows, no AORUS Engine, no root, no
kernel module.

```bash
aorus30-lcd status                       # firmware, mode, overlay state
aorus30-lcd image wallpaper.png          # 320x170, cropped to fill (--fit to letterbox)
aorus30-lcd mode 0                       # back to Gigabyte's eagle animation
aorus30-lcd overlay temp usage power     # panel draws these stats over the content
aorus30-lcd live --set temp=cpu_temp     # ...and gets fresh values every second
aorus30-lcd template --color 00ffcc --pos 10,120 --save   # overlay colour and position
```

## Status

| Card | PCI subsystem | Panel firmware | State |
|---|---|---|---|
| AORUS RTX 3080 Master/Xtreme rev 2.x | `1458:403E` | 0x20 | **verified**: status, probe, image upload. Live feed accepted by the panel (on-screen values not yet confirmed); mode, overlay and template commands follow Panel.exe byte for byte but have had less testing |
| other AORUS 30-series LCD cards | `1458:403D`, `4041`, `4042` | ? | untested, on Gigabyte's own LCD card list. Reports welcome |

Tested with the NVIDIA 615.78 open kernel module on Arch Linux. Find your
subsystem ID with `lspci -nn -d 10de: -v | grep Subsystem`.

Not supported yet: text mode, GIF upload (the protocol is documented in
[docs/PROTOCOL.md](docs/PROTOCOL.md), the code isn't written), RGB lighting.

## Install

Requires Python ≥ 3.9, Pillow, and the NVIDIA driver (`nvidia-smi` must work as your user).

```bash
pipx install git+https://github.com/JanBdf/aorus-rtx30-lcd
# or from a checkout:
pip install --user .
```

First run `aorus30-lcd status`. A healthy panel answers `firmware 00 20 ...`.

### Keep the live overlay running

```bash
aorus30-lcd overlay temp clock usage power --save
cp systemd/aorus30-lcd-live.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now aorus30-lcd-live
```

The unit runs `~/.local/bin/aorus30-lcd live`. Edit `ExecStart` to change the path or map slots to
other sources. Image uploads and other commands pause the service through a shared bus lock.

## Commands

| Command | What it does | Writes flash |
|---|---|---|
| `status` | D6/DE/DF/D8 queries: firmware, mode, overlay | no |
| `probe` | address-only ping of the LCD and its bootloader | no |
| `mode N` | 0–2 logo animations, 3 image, 4 text, 5 gif, 6 pet, 9 carousel | no |
| `save` | persist current mode and overlay config | settings |
| `image FILE [--fit]` | convert to 320×170 RGB565, upload, show, save | **yes** |
| `overlay off\|WIDGET… [--interval S] [--save]` | widgets: `temp clock usage fan vram_clock vram fps power` | no (with `--save`: settings) |
| `feed SLOT=N …` | send one set of overlay values | no |
| `live [--period S] [--set SLOT=SOURCE …]` | feed values continuously | no |
| `template [--color RRGGBB] [--pos X,Y] [--kind 1\|2] [--save]` | overlay text colour and position | no (with `--save`: settings) |
| `testcard OUT.png` | write a test image | – |

`live` sources: `gpu_temp gpu_clock gpu_usage gpu_fan vram_clock vram_usage gpu_power cpu_temp
cpu_usage zero`, a constant number, or `cmd:<shell command>` (first word of its output). The
panel labels the slots as GPU values; `--set temp=cpu_temp` shows the CPU temperature in the
temperature slot.

`--dry-run` prints every frame instead of touching the GPU. Use it to check what a command
would send.

## How it works

The panel's MCU sits at I2C address `0x61` on the GPU's internal I2C port 1. Gigabyte's Windows
software reaches it through NVAPI at 400 kHz. The Linux kernel's `/dev/i2c-N` adapters for NVIDIA
GPUs run at a fixed 100 kHz. This tool instead calls the NVIDIA resource manager
(`/dev/nvidiactl`, `NV402C_CTRL_CMD_I2C_TRANSACTION`) directly from Python `ctypes`, at 400 kHz.
The same path is used by NVIDIA's userspace libraries. That transport comes from
[c-drew/gigabyte-aorus-lcd-linux](https://github.com/c-drew/gigabyte-aorus-lcd-linux) (RTX 5090).

The 30-series panel speaks the same frame family as the 5090 (`[op] CB 55 AC 38 …`, 256 bytes),
but uses different framebuffer addresses, firmware and opcodes. All frames here reproduce what
Gigabyte's "AORUS LCD Panel Setting" (`Panel.exe`, AORUS Engine 2.1.2) and its
`MonitorService.exe` send, in the same order and pacing. Details:
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## Safety

Read [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) before experimenting. In short:

- never run `i2cdetect -r` on GPU I2C buses;
- don't point RTX 5090 / 40-series LCD tools' *write* commands at a 30-series card;
- a panel that stops answering usually recovers with a suspend-to-RAM cycle;
- every `image` upload erases flash, so use `live` for changing data.

## Development

```bash
pip install -e .[test]
pytest
```

Tests pin the frame layouts and never touch a GPU.

## Credits and license

MIT, see [LICENSE](LICENSE). The NVIDIA RM transport and bus lock are taken from
c-drew/gigabyte-aorus-lcd-linux (MIT, © Alban Creton, c-drew).

Not affiliated with or endorsed by Gigabyte / AORUS or NVIDIA. Use at your own risk: this
writes to your graphics card's LCD controller over I2C.
