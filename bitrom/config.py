import json
import os
import socket
import sys

APP_NAME = "bitrom-miner"
_home = os.path.expanduser("~")
CONFIG_DIR = os.environ.get("BITROM_CONFIG_DIR") or os.path.join(_home, ".config", APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CACHE_DIR = os.environ.get("BITROM_CACHE_DIR") or os.path.join(_home, ".cache", APP_NAME)

DEFAULT_POOL = "stratum+tcp://public-pool.io:3333"

DEFAULTS = {
    "wallet_address": "",
    "worker_name": socket.gethostname()[:16] or "01",
    "pool": DEFAULT_POOL,
    "threads": os.cpu_count() or 1,
    "update_interval": 5,
    "network_refresh": 60,
    "show_network": True,
    "quiet_mode": False,
    "quiet_nice": 10,
    "cooling_level": 0,
}


def default_worker_name():
    name = socket.gethostname()
    if name == "." or "localhost" in name:
        name = "cpu01"
    return name


class Config:
    def __init__(self, data, path=CONFIG_PATH):
        self.data = data
        self.path = path

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value

    @property
    def worker_username(self):
        wallet = self.get("wallet_address")
        if not wallet:
            return ""
        worker = self.get("worker_name")
        return f"{wallet}.{worker}" if worker else wallet


def load(path=CONFIG_PATH, data=None):
    if data is None:
        data = {}
    try:
        with open(path) as fh:
            loaded = json.load(fh)
        data.update(loaded)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    cfg = Config(data, path)
    for key, default in DEFAULTS.items():
        if cfg.get(key) is None:
            cfg.set(key, default)
    return cfg


def save(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(cfg.path, "w") as fh:
            json.dump(cfg.data, fh, indent=2)
    except OSError:
        print(f"[error] could not write config to {cfg.path}", file=sys.stderr)


def prompt_first_run(cfg):
    print("=" * 60)
    print("  Bitrom Miner v1 - Linux solo CPU miner")
    print("=" * 60)
    print()
    print("  No configuration found. First run setup.")
    print("  Mining solo needs a Bitcoin wallet. You can create a free")
    print("  wallet at, e.g., bluewallet.io or a hardware wallet.")
    print()

    while True:
        wallet = input("  Your BTC address (bc1q... / 1... / 3...): ").strip().replace(" ", "")
        if validator_options(wallet):
            break
        print("  That does not look like a valid-ish BTC address. Try again.")
    print()

    worker = input(f"  Worker name [default: {DEFAULTS['worker_name']}]: ").strip()
    if not worker:
        worker = DEFAULTS["worker_name"]
    print()

    pool = input(f"  Solo pool URL [default: {DEFAULT_POOL}]: ").strip()
    if not pool:
        pool = DEFAULT_POOL
    print()

    cfg.set("wallet_address", wallet)
    cfg.set("worker_name", worker)
    cfg.set("pool", pool)
    save(cfg)
    print("  Saved to", CONFIG_PATH)
    print()


def validator_options(address):
    if not address:
        return False
    if address.startswith(("bc1", "tb1")) and len(address) >= 26:
        return True
    if address.startswith(("1", "3")) and len(address) in (26, 33, 34, 35):
        return True
    return False