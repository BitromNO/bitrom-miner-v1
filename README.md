# Bitrom Miner v1

A Linux solo **Bitcoin CPU miner** with a retro-NerdMiner-v2-style TUI dashboard,
an on-device cooling control, a systemd service so it keeps mining after you
log out, and a browser dashboard (native + umbrelOS app package) with widgets.

```
██████  ██████  ██████  ██████  ██████  ██  ██        MINER  v1
██  ██    ██      ██    ██  ██  ██  ██  ██████        hash    2.50 kH/s
██  ██    ██      ██    ██  ██  ██  ██  ██  ██        threads 1 x SHA-256d
██████    ██      ██    ██████  ██  ██  ██  ██        uptime  1h 02m
██  ██    ██      ██    ██ ██   ██  ██  ██  ██        pool    public-pool.io:3333
██  ██    ██      ██    ██ ██   ██  ██  ██  ██        mode    cool L5 (5 thr)
██████  ██████    ██    ██  ██  ██████  ██  ██
```

## Why

Mining Bitcoin on a CPU is a lottery, not a money-maker. This project is the
Linux version of the popular **NerdMiner v2** / "toy miner" — a fun, visible,
self-contained way to watch a real ASIC-less miner talk to a real pool, parse
real shares, and hunt for the (incredibly unlikely) block. Treat it as an
educational experiment.

## Features

- **TUI dashboard** with a BITROM block-letter logo, panel rotation
  (WORKER / NETWORK / BLOCKS / STATUS), hashrate sparkline and network stats
  (block height + difficulty).
- **Real mining backend** — builds `tpruvot/cpuminer-multi` automatically on
  first run, parses its actual log output (`kH/s`, share difficulty, reject
  reasons, per-thread rates).
- **Cooling level 1-10** — the menu maps a level to a fraction of your CPU
  threads (`level N → max(1, round(threads × (11−N)/10))`), applied live by
  restarting the miner. Perfect for a strictly quiet, cool box.
- **Systemd service** — run headless, survive logout and restarts themselves.
- **Web dashboard (`--web`)** — a browser UI with the same live stats, cooling
  control, and wallet/pool setup. Also powers the **umbrelOS app** with a home
  screen widget (`umbrel/bitrom-miner/`).
- **Works with low-difficulty solo pools** like `public-pool.io`, which accept
  CPU shares within minutes (unlike ckpool's min-diff 10000 that can take weeks
  to register a CPU).

## Requirements

- Linux (tested on Pop!_OS / Ubuntu)
- Python 3.10+
- Build tools for cpuminer-multi (`gcc`, `make`, `autoconf`, `automake`,
  `libcurl`, `libssl`, `libjansson`):
  ```bash
  sudo apt install -y build-essential git autoconf automake libtool libcurl4-openssl-dev libssl-dev libjansson-dev libgmp-dev pkg-config
  ```

## Install

```bash
git clone https://github.com/<you>/bitrom-miner-v1.git
cd bitrom-miner-v1
python3 bitrom.py
```

On first run you'll be asked for your Bitcoin address. The miner binary is
auto-built into `~/.cache/bitrom-miner/`, config lives in
`~/.config/bitrom-miner/config.json`.

## Usage (TUI)

| Key | Action |
| --- | --- |
| `q` / `Esc` | quit |
| `s` / `Space` | cycle panel |
| `m` | open menu (cooling level) |
| `n` | quick toggle full speed ↔ max cooling |

CLI options:

```
python3 bitrom.py -w <address> -t <threads> --level N
python3 bitrom.py --headless
python3 bitrom.py --web [--web-port 8080]   # browser dashboard at http://localhost:8080
python3 bitrom.py --web --headless          # headless + web (container/systemd)
python3 bitrom.py --no-network
python3 bitrom.py --rebuild
```

## Run after logout (systemd)

```bash
sudo cp systemd/bitrom-miner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bitrom-miner
```

Watch it:

```bash
journalctl -u bitrom-miner -f
```

The service runs headless, restarts on crash, and stops cleanly on
`systemctl stop`.

## Pools

This project works with any sha256d stratum pool:

- **public-pool.io** (`stratum+tcp://public-pool.io:3333`) — 0% fee, solo,
  low difficulty; an 11 MH/s CPU sees accepted shares within minutes. Stats:
  `https://web.public-pool.io/#/<your-address>`
- **solo.ckpool.org** (`stratum+tcp://solo.ckpool.org:3333`) — 2% fee, solo,
  but enforces difficulty ≥ 10000. A CPU will submit a share only on average
  once every ~6 weeks, so use public-pool for feedback.

## Configuration

`~/.config/bitrom-miner/config.json`:

```json
{
  "wallet_address": "bc1q...",
  "worker_name": "pop-os",
  "pool": "stratum+tcp://public-pool.io:3333",
  "threads": 8,
  "update_interval": 5,
  "network_refresh": 60,
  "cooling_level": 0
}
```

`cooling_level`: `0` = all threads (full speed), `1-10` = progressively fewer
threads (cooler, quieter).

## How it's built

```
bitrom/
  config.py    config load/save + first-run setup
  miner.py     MinerProcess, cpuminer log parser, headless runner
  network.py   block height + difficulty fetchers
  ui.py        curses dashboard (logo, panels, menu, sparkline)
  web.py       web dashboard + widget API
bitrom.py      entry point / CLI
systemd/       bitrom-miner.service
umbrel/        umbrelOS app package (bitrom-miner)
```

## Disclaimer

CPU solo mining virtually never finds a block. This is a learning/demo project;
run it because it's fun and educational, not as an income stream.