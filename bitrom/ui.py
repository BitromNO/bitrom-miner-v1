import curses
import time

from . import config as config_mod
from .miner import human_rate, effective_threads

_B = ("██████", "██  ██", "██  ██", "██████", "██  ██", "██  ██", "██████")
_I = ("██████", "  ██  ", "  ██  ", "  ██  ", "  ██  ", "  ██  ", "██████")
_T = ("██████", "  ██  ", "  ██  ", "  ██  ", "  ██  ", "  ██  ", "  ██  ")
_R = ("██████", "██  ██", "██  ██", "██████", "██ ██ ", "██ ██ ", "██  ██")
_O = ("██████", "██  ██", "██  ██", "██  ██", "██  ██", "██  ██", "██████")
_M = ("██  ██", "██████", "██  ██", "██  ██", "██  ██", "██  ██", "██  ██")

BITROM_LOGO = [
    "".join(word[i] + ("  " if j < 5 else "") for j, word in enumerate((_B, _I, _T, _R, _O, _M)))
    for i in range(7)
]

PANEL_NAMES = ["WORKER", "NETWORK", "BLOCKS", "STATUS"]

_VERBOSE_TICK = 5


def _fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _fmt_num(n):
    if n is None:
        return "?"
    if isinstance(n, float):
        return f"{n:,.2f}"
    return f"{int(n):,}"


def _rate_big(n):
    n = abs(float(n))
    for unit, div in (("ZH", 1e21), ("EH", 1e18), ("PH", 1e15), ("TH", 1e12),
                      ("GH", 1e9), ("MH", 1e6), ("kH", 1e3)):
        if n >= div:
            return f"{n / div:.2f} {unit}/s"
    return f"{n:.2f} H/s"


def _human_num(n):
    n = float(n)
    for div, unit in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            return f"{n / div:,.2f} {unit}"
    return f"{n:,.0f}"


def _safe(win, y, x, text, attr, width=None):
    if y < 0 or x < 0 or y >= win.getmaxyx()[0]:
        return
    maxx = win.getmaxyx()[1]
    if x >= maxx:
        return
    if width is None:
        width = maxx - x
    try:
        win.addnstr(y, x, text[:width], width, attr)
    except curses.error:
        pass


class Dashboard:
    def __init__(self, state, network, config, proc=None):
        self.state = state
        self.net = network
        self.config = config
        self.proc = proc
        self.panel = 0
        self.last_cycle = 0.0
        self.notice = ("", 0.0)
        self.colors = {}

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        pairs = [
            (1, curses.COLOR_GREEN, -1),
            (2, curses.COLOR_GREEN, curses.COLOR_BLACK),
            (3, curses.COLOR_WHITE, curses.COLOR_GREEN),
            (4, curses.COLOR_YELLOW, -1),
            (5, curses.COLOR_RED, -1),
            (6, curses.COLOR_CYAN, -1),
        ]
        for num, fg, bg in pairs:
            curses.init_pair(num, fg, bg)
        for name, attr in (
            ("normal", curses.color_pair(1)),
            ("dim", curses.A_DIM | curses.color_pair(1)),
            ("bold", curses.A_BOLD | curses.color_pair(1)),
            ("logo", curses.A_BOLD | curses.color_pair(2)),
            ("title", curses.A_BOLD | curses.color_pair(3)),
            ("warn", curses.A_BOLD | curses.color_pair(4)),
            ("err", curses.A_BOLD | curses.color_pair(5)),
            ("accent", curses.A_BOLD | curses.color_pair(6)),
        ):
            self.colors[name] = attr

    def draw(self, stdscr):
        curses.curs_set(0)
        self._setup_colors()
        stdscr.nodelay(True)
        stdscr.keypad(True)

        tick = 0
        while True:
            key = stdscr.getch()
            if key in (ord("q"), 27):
                break
            if key in (ord("s"), ord(" ")):
                self.panel = (self.panel + 1) % len(PANEL_NAMES)
                self.last_cycle = time.time()
            if key == ord("m"):
                self._menu(stdscr)
            if key == ord("n"):
                self._toggle_quick(stdscr)

            now = time.time()
            if now - self.last_cycle >= self.config.get("update_interval") * 4:
                self.panel = (self.panel + 1) % len(PANEL_NAMES)
                self.last_cycle = now

            self.state.up = now - self.state.start_time
            if tick % _VERBOSE_TICK == 0:
                self._sample()
            self._render(stdscr)
            stdscr.refresh()
            time.sleep(0.2)
            tick += 1

    def _sample(self):
        sm = self.state.show_rate
        sm = sm * 0.8 + self.state.hashrate * 0.2
        self.state.show_rate = sm
        self.state.samples.append(sm)
        del self.state.samples[:-80]

    def _render(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        state = self.state

        self._draw_header(stdscr, h, w)

        ph = max(8, h - 14)
        if w >= 84:
            self._draw_panel(stdscr, 9, ph, w, 0)
            self._draw_panel_right(stdscr, 9, ph, w, 1)
        else:
            self._draw_panel(stdscr, 9, ph, w, self.panel)

        self._draw_bottom(stdscr, h, w)

    def _draw_header(self, win, h, w):
        state = self.state
        logo_w = max(len(r) for r in BITROM_LOGO)
        logo_h = len(BITROM_LOGO)

        logo_y = 1
        for i, row in enumerate(BITROM_LOGO):
            _safe(win, i + logo_y, 3, row, self.colors["logo"], width=logo_w)

        x_right = max(logo_w + 10, 56)
        rate_str = human_rate(state.show_rate) if state.show_rate else human_rate(state.hashrate)
        if not state.running and not state.error:
            rate_str = "starting..."
        _safe(win, 1, x_right, "MINER  v1", self.colors["title"], width=16)
        _safe(win, 2, x_right,
              f"hash    {rate_str:<14}", self.colors["bold"])
        _safe(win, 3, x_right,
              f"threads {state.threads} x SHA-256d", self.colors["normal"])
        _safe(win, 4, x_right, f"uptime  {_fmt_duration(state.up)}", self.colors["normal"])
        _safe(win, 5, x_right, f"pool    {self.config.get('pool').split('//')[-1]}",
              self.colors["normal"])
        _safe(win, 6, x_right, "mode    ",
              self.colors["dim"])
        mode_attr = self.colors["warn"] if self._mode_state() > 0 else self.colors["normal"]
        _safe(win, 6, x_right + 7, self._mode_label(), mode_attr)

        _safe(win, 8, 2, "\u2550" * (w - 4), self.colors["dim"])

    def _draw_panel(self, win, y, ph, w, index):
        title = PANEL_NAMES[index]
        _safe(win, y, 2, f" {title} ", self.colors["title"], width=w - 4)
        x = 4
        row = y + 1
        for line in self._panel_lines(index, w - 6):
            _safe(win, row, x, line, self.colors["normal"], width=w - 6)
            row += 1
            if row >= y + ph - 1:
                break

    def _draw_panel_right(self, win, y, ph, w, index):
        title = PANEL_NAMES[index]
        left = max(4, w // 2)
        _safe(win, y, left, f" {title} ", self.colors["title"], width=w - left - 2)
        row = y + 1
        for line in self._panel_lines(index, w - left - 6):
            _safe(win, row, left + 2, line, self.colors["normal"], width=w - left - 6)
            row += 1
            if row >= y + ph - 1:
                break

    def _panel_lines(self, index, width):
        state = self.state
        if index == 0:
            username = self.config.worker_username or "(no wallet)"
            wallet = self.config.get("wallet_address") or "(none)"
            pool = self.config.get("pool")
            conn = "connected" if state.connected else ("connecting..." if state.running else "idle")
            lines = [
                f"worker   {self.config.get('worker_name')}",
                f"auth     {username}",
                f"wallet   {wallet}",
                f"pool     {pool}",
                f"conn     {conn}",
            ]
            if state.error:
                lines.append(f"error    {state.error[:30]}")
            return [self._clip(line, width) for line in lines]

        if index == 1:
            h = self.net.height
            d = self.net.difficulty
            nr = self.net.network_rate
            age = self.net.age()
            title = "offline - cached" if age >= 120 else ("online" if self.net.ok else "none")
            lines = [
                f"blocks   {_fmt_num(h)}",
                f"diff     {_human_num(d) if d else '?'}",
                f"net rate {_rate_big(nr) if nr else '?'}",
                f"feed     {title} ({age}s ago)".replace("-1s", "-"),
            ]
            return [self._clip(line, width) for line in lines]

        if index == 2:
            kh = state.show_rate
            if kh and self.net.difficulty:
                expected_h = self.net.difficulty * (2 ** 48)
                years = expected_h / (kh * 1000) / (365.25 * 86400)
                est = f"{years:.2e} yr" if years >= 1e4 else f"{years:.1f} yr" if years >= 1 else f"{years * 365.25:.0f} d"
            else:
                est = "n/a"
            lines = [
                f"blocks found {self.state.blocks_found}",
                f"shares  {_fmt_num(state.accepted)}",
                f"last    diff {state.last_diff_share or 0:.2f}",
                "est/sol  " + est,
                "",
                "  NO BLOCK FOUND YET",
                "  JUST HODL",
            ]
            return [self._clip(line, width) for line in lines]

        total = state.accepted + state.rejected
        pct = (state.accepted / total * 100) if total else 0
        kh = state.show_rate
        hashes = kh * 1000 * state.up
        lines = [
            f"mode     {self._mode_label()}",
            f"accepted {state.accepted}",
            f"rejected {state.rejected}",
            f"acc%     {pct:.1f}",
            f"hash     {_rate_big(hashes)} mined",
            f"rate     {human_rate(kh)}",
        ]
        if state.error:
            lines.append("")
            lines.append(f"err: {state.error[:44]}")
        return [self._clip(line, width) for line in lines]

    def _mode_state(self):
        return int(self.config.get("cooling_level") or 0)

    def _mode_label(self):
        level = self._mode_state()
        if level > 0:
            threads = effective_threads(self.config)
            return f"cool L{level} ({threads} thr)"
        return "full speed"

    def _apply_level(self, stdscr, level, quick=False):
        level = min(10, max(0, level))
        cfg = self.config
        cfg.set("cooling_level", level)
        config_mod.save(cfg)
        threads = effective_threads(self.config)
        if self.proc:
            try:
                self.proc.restart(level)
                msg = f"COOLING LEVEL {level} - {threads} threads {'(applied)' if not quick else '(restarting...)'}"
            except Exception:
                msg = "restart failed - change saved for next launch"
        else:
            msg = f"COOLING LEVEL {level} set ({threads} threads - next launch)"
        if level == 0:
            msg = "COOLING OFF - full speed (" + str(effective_threads(self.config)) + " threads)"
        self.notice = (msg, time.time())
        self.last_cycle = time.time()

    def _toggle_quick(self, stdscr):
        cur = self._mode_state()
        self._apply_level(stdscr, 0 if cur > 0 else 10, quick=True)

    def _menu(self, stdscr):
        pending = self._mode_state()
        while True:
            self._render_menu(stdscr, pending)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), 27, ord("m")):
                break
            if key == -1:
                time.sleep(0.15)
                continue
            if key in (ord("n"), ord("N")):
                pending = 0 if self._mode_state() == 0 else 10
                self._apply_level(stdscr, pending)
                break
            if ord("1") <= key <= ord("9"):
                pending = key - ord("0")
            if key == ord("0"):
                pending = 10
            if key in (13, 10, ord(" ")):
                self._apply_level(stdscr, pending)
            if key == ord("s"):
                self.panel = (self.panel + 1) % len(PANEL_NAMES)

    def _render_menu(self, stdscr, pending=None):
        h, w = stdscr.getmaxyx()
        mw = min(w - 6, 46)
        mx = max(2, (w - mw) // 2)
        top = max(1, (h - 12) // 2)
        cur = self._mode_state()
        if pending is None:
            pending = cur
        threads = effective_threads(self.config, level=None if pending == cur else pending)
        base = int(self.config.get("threads") or 1)
        scale = f"{int(round((11 - pending) * 100 / 10))}" if pending > 0 else "100"
        lines = [
            ("  BITROM MINER v1 - MENU          ", "title"),
            ("  " + "\u2550" * (mw - 6) + "                ", "dim"),
            (f"  COOLING LEVEL:  {'{:>2}'.format(pending)}   (1-10)", "bold"),
            ("       1  2  3  4  5  6  7  8  9  10", "normal"),
            ("       full speed                coolest", "dim"),
            (f"       => {threads} of {base} threads (CPU ~{scale}%)", "normal"),
            ("   [enter]  apply   [n] off/full   [q] close", "dim"),
            ("  " + "\u2500" * (mw - 6) + "                ", "dim"),
        ]
        for i, (text, style) in enumerate(lines):
            _safe(stdscr, top + i, mx, text, self.colors[style], width=mw)
        marker = (mw - 6) * pending // 10
        _safe(stdscr, top + 2, mx + marker, "^", self.colors["warn"])

    def _clip(self, line, width):
        return line if len(line) <= width else line[: max(1, width - 4)] + ".."

    def _draw_bottom(self, win, h, w):
        y = h - 4
        _safe(win, y - 1, 2, "\u2550" * (w - 4), self.colors["dim"])
        samples = self.state.samples
        if len(samples) >= 2:
            self._draw_spark(win, y, w, samples)
        _safe(win, h - 1, 2, "q quit  s screen  m menu  n cooling  -- bitrom miner v1",
              self.colors["dim"], width=w - 4)
        level = self._mode_state()
        if level > 0:
            _safe(win, h - 2, 2,
                  f"cooling level {level} active: {effective_threads(self.config)} of {self.config.get('threads')} threads",
                  self.colors["warn"], width=w - 4)
        elif self.state.error:
            _safe(win, h - 2, 2, self.state.error[: w - 4], self.colors["err"], width=w - 4)
        if self.notice and time.time() - self.notice[1] < 4:
            msg = self.notice[0]
            _safe(win, h - 3, max(2, (w - len(msg)) // 2), msg, self.colors["warn"],
                  width=w - 4)

    def _draw_spark(self, win, y, w, samples):
        plot_w = max(10, w - 8)
        n = len(samples)
        bins = []
        for k in range(plot_w):
            start = int(n * k / plot_w)
            end = max(start + 1, int(n * (k + 1) / plot_w))
            bins.append(sum(samples[start:end]) / (end - start))
        lo, hi = min(bins), max(bins)
        span = (hi - lo) or 1
        attr = self.colors["normal"]
        for r in (0, 1):
            for col in range(len(bins)):
                height = int((bins[col] - lo) / span * 2)
                if height >= 2 - r:
                    _safe(win, y + r, 4 + col, "\u2588", attr, 1)
        _safe(win, y + 1, 2, f"{human_rate(lo)}<", self.colors["dim"], width=6)
        _safe(win, y + 1, w - len(human_rate(hi)) - 5, f">{human_rate(hi)}",
              self.colors["dim"])


def run(state, network, config, proc=None):
    ui = Dashboard(state, network, config, proc=proc)
    curses.wrapper(ui.draw)
    return True