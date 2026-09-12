import json
import threading
import time
import urllib.request

MEMPOOL = "https://mempool.space/api"
BLOCKCHAIN_INFO = "https://blockchain.info"


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "bitrom-miner/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _get_json(url, timeout=10):
    return json.loads(_get(url, timeout))


class Network:
    def __init__(self, interval=60):
        self.interval = interval
        self.height = None
        self.difficulty = None
        self.network_rate = None
        self.last_update = 0.0
        self.network_tries = 0
        self.ok = False
        self._stop = False
        self._thread = None

    def start(self):
        if self.interval <= 0:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            try:
                self.refresh()
            except Exception:
                pass
            for _ in range(self.interval):
                if self._stop:
                    break
                time.sleep(1)

    def refresh(self):
        height = None
        difficulty = None
        try:
            height = int(_get(f"{MEMPOOL}/blocks/tip/height").strip())
        except Exception:
            try:
                height = int(_get(f"{BLOCKCHAIN_INFO}/q/latestblock").strip())
            except Exception:
                height = None
        try:
            difficulty = float(_get(f"{BLOCKCHAIN_INFO}/q/getdifficulty").strip())
        except Exception:
            try:
                adj = _get_json(f"{MEMPOOL}/v1/difficulty-adjustment")
                difficulty = adj.get("difficulty")
            except Exception:
                difficulty = None

        if height is not None:
            self.height = height
        if difficulty is not None:
            self.difficulty = difficulty
            self.network_rate = difficulty * (2 ** 48) / 600

        self.network_tries += 1
        self.ok = height is not None and difficulty is not None
        self.last_update = time.time()

    def age(self):
        return time.time() - self.last_update if self.last_update else -1