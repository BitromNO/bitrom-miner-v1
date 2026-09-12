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


def _set_nice(level):
    try:
        os.nice(level)
    except OSError:
        pass


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
        self.nice = None
        self.nice_error = ""
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


def find_existing_binary():
    if os.path.exists(BINARY) and os.access(BINARY, os.X_OK):
        return BINARY
    for name in ("cpuminer", "minerd"):
        found = shutil.which(name)
        if found:
            return found
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
    binary = find_existing_binary()
    if not force and os.path.exists(binary) and os.access(binary, os.X_OK):
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

    def start(self):
        args = [
            BINARY,
            "-a", "sha256d",
            "-o", self.config.get("pool"),
            "-u", self.config.get("worker_username") or self.config.get("wallet_address"),
            "-t", str(self.config.get("threads")),
        ]
        print("[miner] starting:", " ".join(args))
        kwargs = {}
        if self.config.get("quiet_mode"):
            level = int(self.config.get("quiet_nice") or 0)
            if level > 0:
                kwargs["preexec_fn"] = lambda: _set_nice(level)
                print(f"[miner] quiet mode: running at nice level {level}")
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **kwargs,
        )
        if self.config.get("quiet_mode"):
            self.set_nice(int(self.config.get("quiet_nice") or 0))
        self.state.start_time = time.time()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        return self.proc

    def set_nice(self, level):
        if not self.proc or self.proc.poll() is not None:
            self.state.nice = level
            self.state.nice_error = ""
            return True
        try:
            os.setpriority(os.PRIO_PROCESS, self.proc.pid, level)
            self.state.nice = level
            self.state.nice_error = ""
            return True
        except OSError as exc:
            self.state.nice_error = str(exc)
            return False

    def _read_loop(self):
        for line in iter(self.proc.stdout.readline, ""):
            for part in line.split("\r"):
                parse_chunk(part.strip(), self.state)

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def wait(self):
        try:
            return self.proc.wait()
        except KeyboardInterrupt:
            self.stop()
            return None