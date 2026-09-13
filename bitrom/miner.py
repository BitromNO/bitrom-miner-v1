import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from .config import CACHE_DIR

REPO = "https://github.com/tpruvot/cpuminer-multi.git"
SRC_DIR = os.path.join(CACHE_DIR, "cpuminer-multi")
BINARY = os.path.join(SRC_DIR, "cpuminer")
REQUIRED_TOOLS = [
    ("git", "git"),
    ("autoconf", "autoconf"),
    ("automake", "automake"),
    ("make", "make"),
    ("gcc", "gcc"),
]

APT_PACKAGES = (
    "git build-essential automake autoconf libtool "
    "libcurl4-openssl-dev libjansson-dev libssl-dev"
)

HR_UNITS = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s"]
_HR_NAMES = {"k": 1, "m": 2, "g": 3, "t": 4, "p": 5}


def normalize_khash(v, unit):
    unit = unit.lower()
    if not unit:
        return v / 1000.0
    first = unit[0]
    if first == "k":
        return v
    return v * (1000 ** (_HR_NAMES.get(first, 0) - 1))


def human_rate(khash):
    hps = khash * 1000.0
    i = 0
    while hps >= 1000.0 and i < len(HR_UNITS) - 1:
        hps /= 1000.0
        i += 1
    return f"{hps:.2f} {HR_UNITS[i]}"


_HR_RE = re.compile(r"([\d.]+)\s*(?:([kmgtp])(?:hash|h)?)?h(?:ash)?/s", re.I)
_ACC_RE = re.compile(r"accepted:\s*(\d+)/(\d+)", re.I)
_REJ_RE = re.compile(r"reject reason", re.I)
_THREAD_RE = re.compile(r"(\d+)\s+miner threads started")
_DIFF_RE = re.compile(r"\(diff\s+([\d.]+)\)")
_ERR_RE = re.compile(
    r"(authentication failed|auth\s*fail|invalid\s+(username|address|worker|password)|"
    r"stratum\s+(error|connection\s+refused|timeout|failed)|login\s+failed)",
    re.I,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return _ANSI_RE.sub("", text)


class MinerState:
    def __init__(self):
        self.hashrate = 0.0
        self.show_rate = 0.0
        self.accepted = 0
        self.rejected = 0
        self.attempts = 0
        self.threads = 1
        self.running = False
        self.error = ""
        self.connected = False
        self.last_diff_share = None
        self.blocks_found = 0
        self.samples = []
        self.start_time = time.time()
        self.up = 0


def parse_chunk(chunk, state):
    chunk = strip_ansi(chunk)
    if not chunk or not chunk.strip():
        return
    hr = _HR_RE.search(chunk)
    if hr:
        state.hashrate = normalize_khash(float(hr.group(1)), hr.group(2) or "")
        state.running = True
    acc = _ACC_RE.search(chunk)
    if acc:
        state.accepted = int(acc.group(1))
        state.attempts = int(acc.group(2))
        state.connected = True
    if _REJ_RE.search(chunk):
        state.rejected += 1
    th = _THREAD_RE.search(chunk)
    if th:
        state.threads = int(th.group(1))
    df = _DIFF_RE.search(chunk)
    if df:
        state.last_diff_share = float(df.group(1))
        if state.last_diff_share >= 1e9:
            state.blocks_found += 1
    if _ERR_RE.search(chunk):
        state.error = chunk.strip()


def resolve_binary():
    """Return a usable miner binary, preferring $BITROM_BINARY (container)."""
    candidates = [os.environ.get("BITROM_BINARY"), BINARY]
    for name in ("cpuminer", "minerd"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for c in candidates:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return BINARY


def missing_tools():
    return [name for tool, name in REQUIRED_TOOLS if not shutil.which(tool)]


def run(cmd, cwd=None):
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in proc.stdout:
        print("  " + line.rstrip())
    proc.wait()
    return proc.returncode


def ensure_backend(force=False):
    binary = resolve_binary()
    if not force and binary is not None and os.access(binary, os.X_OK):
        return binary

    print()
    print("[build] no usable cpuminer binary found, building a CPU miner...")
    print()
    missing = missing_tools()
    if missing:
        print("[error] missing build tools:", ", ".join(missing))
        print()
        print(f"  Install them on Pop!_OS / Ubuntu with:")
        print(f"      sudo apt update && sudo apt install {APT_PACKAGES}")
        print()
        sys.exit(1)

    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.isdir(SRC_DIR):
        print(f"[build] updating {SRC_DIR} ...")
        run(["git", "-C", SRC_DIR, "pull", "--ff-only"])
    else:
        print(f"[build] cloning cpuminer-multi ...")
        if run(["git", "clone", "--depth", "1", REPO, SRC_DIR]) != 0:
            print("[error] git clone failed. Check your network.")
            sys.exit(1)

    print("[build] running autogen.sh ...")
    if run(["./autogen.sh"], cwd=SRC_DIR) != 0:
        print("[error] autogen.sh failed; missing autotools?")
        sys.exit(1)

    print("[build] configuring (crypto + curl) ...")
    if run(["./configure", "--with-crypto", "--with-curl"], cwd=SRC_DIR) != 0:
        print(f"[error] configure failed.\n  Install: sudo apt install {APT_PACKAGES}")
        sys.exit(1)

    threads = os.cpu_count() or 1
    print(f"[build] compiling with -j{threads} (this can take a few minutes) ...")
    if run(["make", "-j", str(threads)], cwd=SRC_DIR) != 0:
        print("[error] build failed")
        sys.exit(1)

    if not os.path.exists(BINARY):
        print("[error] build finished but binary not found")
        sys.exit(1)
    return BINARY


class MinerProcess:
    def __init__(self, config, state):
        self.config = config
        self.state = state
        self.proc = None

    def start(self, threads=None):
        if threads is None:
            threads = effective_threads(self.config)
        if not self.config.get("wallet_address"):
            print("[error] no wallet configured; miner not started", flush=True)
            return None
        args = [
            resolve_binary(),
            "-a", "sha256d",
            "-o", self.config.get("pool"),
            "-u", self.config.worker_username or self.config.get("wallet_address"),
        ]
        password = self.config.get("pool_password") or ""
        if password:
            args += ["-p", password]
        args += ["-t", str(threads)]
        print("[miner] starting:", " ".join(args))
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.proc = proc
        self.state.accepted = 0
        self.state.attempts = 0
        self.state.rejected = 0
        self.state.hashrate = 0.0
        self.state.show_rate = 0.0
        self.state.samples = []
        self.state.start_time = time.time()
        self.reader = threading.Thread(target=self._read_loop, args=(proc,), daemon=True)
        self.reader.start()
        return proc

    def restart(self, level):
        intended = effective_threads(self.config, level=level)
        self.save_level(level)
        self.stop()
        self.proc = None
        time.sleep(0.4)
        return self.start(threads=intended)

    def save_level(self, level):
        self.config.set("cooling_level", level)

    def _read_loop(self, proc):
        for line in iter(proc.stdout.readline, ""):
            for part in line.split("\r"):
                parse_chunk(part.strip(), self.state)

    def stop(self):
        proc = self.proc
        if not proc:
            return
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def wait(self):
        try:
            return self.proc.wait()
        except KeyboardInterrupt:
            self.stop()
            return None


def effective_threads(config, level=None):
    base = int(config.get("threads") or (os.cpu_count() or 1))
    if level is None:
        level = int(config.get("cooling_level") or 0)
    if level <= 0 or level > 10:
        return base
    return max(1, int(round(base * (11 - level) / 10.0)))


def run_headless(state, proc, interval=10):
    """Run the miner without a terminal dashboard, logging to stdout (journald).
    Stops cleanly on SIGTERM/SIGINT."""
    import signal

    level = int(proc.config.get("cooling_level") or 0)
    threads = effective_threads(proc.config)
    pool = proc.config.get("pool")
    wallet = proc.config.get("worker_username") or proc.config.get("wallet_address")
    wmask = (wallet[:12] + "..." + wallet[-6:]) if len(wallet) > 20 else wallet
    print(f"\n=== bitrom miner v1 (headless) ===")
    print(f"pool    {pool}")
    print(f"wallet  {wmask}")
    print(f"threads {threads}   cooling level {level if level else 0} "
          f"({'off' if level == 0 else 'level ' + str(level)})")

    stopped = {"flag": False}

    def _stop(_sig, _frame):
        stopped["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print("[miner] mining started; press Ctrl-C or systemctl stop to finish")
    while not stopped["flag"]:
        alive = proc.proc and proc.proc.poll() is None
        if not alive:
            msg = proc.state.error or "unknown"
            print(f"[error] miner process died: {msg}", flush=True)
            break
        rate = human_rate(state.hashrate)
        print(f"[miner] {rate:<12} accepted {state.accepted}  submitted {state.attempts}"
              f"  rejected {state.rejected}  blocks {state.blocks_found}  "
              f"last_diff {state.last_diff_share or '-'}", flush=True)
        for _ in range(10):
            if stopped["flag"]:
                break
            time.sleep(max(1.0, interval / 10.0))
    print("[miner] stopping...", flush=True)
    proc.stop()
    print("[miner] stopped cleanly", flush=True)