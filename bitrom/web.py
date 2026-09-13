import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import save as config_save
from .config import validator_options
from .miner import MinerProcess, MinerState, effective_threads, human_rate
from . import __version__


def human_hash(n):
    if n is None:
        return "-"
    for unit in ("H", "kH", "MH", "GH", "TH", "PH", "EH"):
        if n < 1000:
            return f"{n:.2f} {unit}"
        n /= 1000
    return f"{n:.2f} EH"


def human_duration(seconds):
    if seconds is None or seconds < 0:
        return "-"
    if seconds >= 3.15576e16:
        return "> 1 By"
    parts = []
    s = int(seconds)
    for sec, label in ((31557600, "y"), (2629746, "mo"), (86400, "d"), (3600, "h"), (60, "min"), (1, "s")):
        v = s // sec if sec else 0
        if v:
            parts.append(f"{v}{label}")
            s -= v * sec
        if len(parts) == 2:
            break
    return " ".join(parts) if parts else "<1s"


class WebController:
    def __init__(self, config, net=None):
        self.config = config
        self.net = net
        self.state = MinerState()
        self.proc = None
        self.lock = threading.Lock()
        self.last_error = ""
        self.rate_hist = []
        self._last_sample = 0.0

    @property
    def configured(self):
        return bool(self.config.get("wallet_address"))

    def mining(self):
        return bool(self.proc and self.proc.proc and self.proc.proc.poll() is None)

    def start_miner(self):
        with self.lock:
            if self.mining():
                return
            if not self.configured:
                return
            self.proc = MinerProcess(self.config, self.state)
            self.proc.start()

    def stop_miner(self):
        with self.lock:
            proc, self.proc = self.proc, None
            if proc:
                proc.stop()

    def restart_miner(self):
        with self.lock:
            proc, self.proc = self.proc, None
            if proc:
                proc.stop()
            if self.configured:
                self.proc = MinerProcess(self.config, self.state)
                self.proc.start()

    def set_cooling(self, level):
        level = int(level)
        if not 0 <= level <= 10:
            return False
        threads = None
        with self.lock:
            self.config.set("cooling_level", level)
            config_save(self.config)
            if self.mining():
                intended = effective_threads(self.config, level=level)
                self.proc.save_level(level)
                self.proc.stop()
                self.proc = None
                time.sleep(0.4)
                self.proc = MinerProcess(self.config, self.state)
                self.proc.start(threads=intended)
                threads = intended
            elif level:
                self.start_miner()
                threads = effective_threads(self.config, level=level)
        self.state.threads = threads if threads else self.state.threads
        return True

    def set_settings(self, wallet, pool, worker, password=None, threads=None,
                     update_interval=None, network_refresh=None, show_network=None):
        wallet = (wallet or "").strip().replace(" ", "")
        pool = (pool or "").strip()
        worker = (worker or "").strip()
        if wallet and not validator_options(wallet):
            return False, "wallet address does not look valid"
        if pool and "://" not in pool:
            return False, "pool URL must include scheme (stratum+tcp://...)"
        for name, value, default_min in (
            ("threads", threads, 1),
            ("update_interval", update_interval, 1),
            ("network_refresh", network_refresh, 1),
        ):
            if value is None:
                continue
            try:
                value = int(value)
                if value < default_min:
                    raise ValueError
            except (ValueError, TypeError):
                return False, f"{name} must be a number >= {default_min}"
            if name == "threads":
                threads = value
            elif name == "update_interval":
                update_interval = value
            else:
                network_refresh = value
        if show_network is not None:
            if isinstance(show_network, str):
                show_network = show_network.strip().lower() in ("1", "true", "yes", "on")
            show_network = bool(show_network)
        with self.lock:
            if wallet:
                self.config.set("wallet_address", wallet)
            if worker:
                self.config.set("worker_name", worker)
            if pool:
                self.config.set("pool", pool)
            if password is not None:
                self.config.set("pool_password", (password or "").strip())
            if threads is not None:
                self.config.set("threads", threads)
            if update_interval is not None:
                self.config.set("update_interval", update_interval)
            if network_refresh is not None:
                self.config.set("network_refresh", network_refresh)
            if show_network is not None:
                self.config.set("show_network", show_network)
            saved = config_save(self.config)
        if self.configured:
            self.restart_miner()
        if not saved:
            return False, "could not write config to disk - address will NOT persist"
        return True, ""

    def widget_data(self):
        items = [
            {"title": "Hashrate", "text": "-"},
            {"title": "Accepted", "text": "-"},
            {"title": "Submitted", "text": "-"},
            {"title": "Blocks", "text": "-"},
        ]
        if self.configured:
            rate = human_rate(self.state.hashrate) if self.state.hashrate else "-"
            parts = rate.split(" ", 1) if isinstance(rate, str) else ["-"]
            items[0] = {"title": "Hashrate", "text": parts[0], "subtext": parts[1] if len(parts) > 1 else ""}
            items[1] = {"title": "Accepted", "text": str(self.state.accepted)}
            items[2] = {"title": "Submitted", "text": str(self.state.attempts)}
            items[3] = {"title": "Blocks", "text": str(self.state.blocks_found)}
        return {"type": "four-stats", "refresh": "5s", "items": items}

    def status_data(self):
        now = time.time()
        if now - self._last_sample >= 2:
            self._last_sample = now
            self.rate_hist.append(round(self.state.hashrate, 3))
            if len(self.rate_hist) > 150:
                del self.rate_hist[:len(self.rate_hist) - 150]
        wallet = self.config.get("wallet_address")
        wmask = (wallet[:12] + "..." + wallet[-6:]) if len(wallet) > 20 else wallet
        rate = human_rate(self.state.hashrate) if self.state.hashrate else "-"
        alive = self.mining()
        error = ""
        if not alive and self.configured and self.proc is not None:
            error = (self.proc.proc and self.proc.proc.poll()) and (self.state.error or "miner process stopped") or ""
        if (self.proc is not None and self.proc.proc and self.proc.proc.poll() is None):
            error = self.state.error
        net_ok = (self.net is not None and self.net.ok
                  and self.net.difficulty is not None and self.net.difficulty > 0)
        difficulty = self.net.difficulty if net_ok else None
        grate = human_hash(self.net.network_rate) if (net_ok and self.net.network_rate) else None
        hps = self.state.hashrate * 1000
        est = None
        if net_ok and hps > 0:
            est = human_duration(difficulty * (2 ** 32) / hps)
        return {
            "configured": self.configured,
            "mining": alive,
            "error": error,
            "hashrate": rate,
            "hashrate_kh": round(self.state.hashrate, 2),
            "difficulty": human_hash(difficulty) if difficulty else None,
            "height": self.net.height if (self.net is not None and self.net.ok) else None,
            "grate": grate,
            "est": est,
            "accepted": self.state.accepted,
            "submitted": self.state.attempts,
            "rejected": self.state.rejected,
            "blocks": self.state.blocks_found,
            "last_diff": self.state.last_diff_share,
            "threads": self.state.threads,
            "threads_set": int(self.config.get("threads") or 1),
            "threads_effective": effective_threads(self.config),
            "cooling_level": int(self.config.get("cooling_level") or 0),
            "update_interval": int(self.config.get("update_interval") or 5),
            "network_refresh": int(self.config.get("network_refresh") or 60),
            "show_network": bool(self.config.get("show_network")),
            "pool": self.config.get("pool"),
            "pool_password": self.config.get("pool_password") or "",
            "wallet": wmask,
            "worker": self.config.get("worker_name"),
            "uptime": round(time.time() - self.state.start_time),
            "version": __version__,
            "rate_hist": list(self.rate_hist),
        }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bitrom Miner</title>
<style>
:root{--bg:#0b0e0f;--panel:#12181a;--line:#1f2a2e;--fg:#d7e0dc;--dim:#63706c;--br:#28c98f;--warn:#e0a33c;--err:#e2553f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "JetBrains Mono",ui-monospace,Menlo,monospace;padding:24px}
.wrap{max-width:880px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:18px}
h1{font-size:18px;margin:0;color:var(--br);letter-spacing:.5px}
h1 span{color:var(--dim);font-weight:normal}
#conn{color:var(--dim)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1px}
.card .v{font-size:22px;margin-top:4px}
.card .u{color:var(--dim);font-size:12px}
#rate{font-size:34px;color:var(--br)}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:18px}
h2{font-size:13px;margin:0 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
label{display:block;margin:8px 0 4px;color:var(--dim);font-size:12px}
input{width:100%;background:#0d1214;border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:8px 10px;font:inherit}
input[type=number]{width:100%}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0 14px}
.chk{display:flex;align-items:center;gap:8px;margin-top:14px;color:var(--fg);font-size:13px}
.chk input[type=checkbox]{width:auto;accent-color:var(--br)}
button{margin-top:12px;background:var(--br);color:#04160d;border:0;border-radius:6px;padding:9px 16px;font:inherit;font-weight:bold;cursor:pointer}
button:disabled{opacity:.45;cursor:default}
.range{display:flex;align-items:center;gap:14px}
input[type=range]{flex:1;accent-color:var(--br)}
#lvl{min-width:150px;color:var(--fg);text-align:right}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--panel);border:1px solid var(--br);border-radius:8px;padding:8px 16px;opacity:0;transition:opacity .25s}
#toast.err{border-color:var(--err);color:var(--err)}
#toast.on{opacity:1}
#rategraph{width:100%}
#rategraph svg{display:block;width:100%;height:200px}
#rategraph polyline{stroke-linejoin:round;stroke-linecap:round}
.poly{fill:rgba(40,201,143,.08)}
#rate-max{font-weight:normal;text-transform:none;letter-spacing:0}
#tip{margin-left:auto;background:var(--panel);border:1px solid var(--line);color:var(--br);border-radius:8px;padding:7px 12px;font-size:12px;text-decoration:none;cursor:pointer;white-space:nowrap}
#tip:hover{border-color:var(--br)}
#conn{margin:0 14px}
#tipbox{padding:12px 14px;margin:0 0 18px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
.trow{display:flex;gap:10px;align-items:center}
#tipaddr{font-size:12px;color:var(--dim);word-break:break-all;flex:1}
#tipbox button{padding:5px 12px;margin:0;font-size:12px}
.topen{display:inline-block;margin-top:8px;font-size:12px;color:var(--br)}
.setup{padding:30px;text-align:center;color:var(--dim);border:1px dashed var(--line);border-radius:10px}
.setup b{color:var(--fg)}
</style>
</head>
<body>
<div class="wrap">
<header><h1>BITROM <span>miner v{{VER}}</span></h1><div id="conn">&mdash;</div><a id="tip" href="#">Buy me a coffee</a></header>

<div id="tipbox" hidden>
  <div class="trow"><span id="tipaddr"></span><button id="tipcopy">Copy</button></div>
  <a id="topen" class="topen" href="#" target="_blank" rel="noopener">Open in wallet</a>
</div>

<div id="setup" class="setup" hidden>No mining wallet configured yet.<br>Fill in the <b>Settings</b> below and press <b>Save</b> to start mining.</div>

<div class="cards">
  <div class="card"><div class="k">Hashrate</div><div id="rate">-</div></div>
  <div class="card"><div class="k">Accepted</div><div class="v" id="accepted">-</div></div>
  <div class="card"><div class="k">Submitted</div><div class="v" id="submitted">-</div></div>
  <div class="card"><div class="k">Rejected</div><div class="v" id="rejected">-</div></div>
  <div class="card"><div class="k">Blocks</div><div class="v" id="blocks">-</div></div>
  <div class="card"><div class="k">Last share diff</div><div class="v" id="diff">-</div></div>
  <div class="card"><div class="k">Uptime</div><div class="v" id="up">-</div></div>
<div class="card"><div class="k">Difficulty</div><div class="v" id="netdiff">-</div></div>
<div class="card"><div class="k">Block height</div><div class="v" id="height">-</div></div>
<div class="card"><div class="k">Global rate</div><div class="v" id="grate">-</div></div>
<div class="card"><div class="k">Est. block in</div><div class="v" id="est">-</div></div>
</div>

<section>
<h2>Hashrate <span id="rate-max" class="dim"></span></h2>
<div id="rategraph"></div>
</section>

<section>
<h2>Cooling / power</h2>
<div class="range">
  <input type="range" id="cool" min="0" max="10" value="0">
  <span id="lvl">off</span>
</div>
<button id="applyCool">Apply</button>
</section>

<section>
<h2>Settings</h2>
<label for="wallet">Bitcoin address</label>
<input id="wallet" placeholder="bc1q..." autocomplete="off" spellcheck="false">
<label for="pool">Solo pool URL</label>
<input id="pool" placeholder="stratum+tcp://public-pool.io:3333" autocomplete="off">
<label for="pass">Pool password (optional, e.g. <code>x</code>)</label>
<input id="pass" placeholder="x" autocomplete="new-password" spellcheck="false">
<label for="worker">Worker name</label>
<input id="worker" placeholder="cpu01" autocomplete="off" spellcheck="false">
<div class="two">
<div><label for="threads">Threads (blank = auto)</label>
<input id="threads" type="number" min="1" placeholder="auto" autocomplete="off"></div>
<div><label for="ui">Update interval (s)</label>
<input id="ui" type="number" min="1" placeholder="5" autocomplete="off"></div>
<div><label for="ni">Network refresh (s)</label>
<input id="ni" type="number" min="1" placeholder="60" autocomplete="off"></div>
</div>
<label class="chk"><input id="net" type="checkbox"> Show network activity</label>
<button id="save">Save and restart miner</button>
</section>

<div id="toast"></div>
</div>
<script>
const $=id=>document.getElementById(id);
let current={};
async function get(url,body){
  const r=await fetch(url,{method:body?'POST':'GET',
    headers:body?{'Content-Type':'application/json'}:{},
    body:body?JSON.stringify(body):undefined});
  let j=null;try{j=await r.json()}catch(e){}
  return{r,j};
}
function toast(msg,err){const t=$('toast');t.textContent=msg;t.className='on'+(err?' err':'');clearTimeout(t._h);t._h=setTimeout(()=>t.className='',2600)}
function fmtRate(kh){
  let hps=kh*1000,u=['H/s','kH/s','MH/s','GH/s','TH/s','PH/s'],i=0;
  while(hps>=1000&&i<u.length-1){hps/=1000;i++;}
  return hps.toFixed(2)+' '+u[i];
}
function drawRate(hist){
  const el=$('rategraph'),mx=$('rate-max');
  if(!hist||hist.length<2){el.innerHTML='';if(mx)mx.textContent='';return;}
  const W=900,H=200,P=6;
  let max=0;for(const v of hist)if(v>max)max=v;
  if(!max){if(mx)mx.textContent='';return;}
  mx.textContent='peak '+fmtRate(max);
  const n=hist.length;
  const pts=hist.map((v,i)=>(P+(W-2*P)*i/(n-1)).toFixed(1)+','+(H-P-(H-2*P)*(v/max)).toFixed(1)).join(' ');
  const fill='0,'+(H-P)+' '+pts+' '+(W-P)+','+(H-P);
  el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none"><polygon points="'+fill+'" class="poly"/><polyline points="'+pts+'" fill="none" stroke="var(--br)" stroke-width="2"/></svg>';
}
const TIP_ADDR='bc1qkcs788qndhyxyudt5aurmrdvynj3wle6kr25jm';
$('tip').onclick=(e)=>{e.preventDefault();const b=$('tipbox');b.hidden=!b.hidden;if(!b.hidden){$('tipaddr').textContent=TIP_ADDR;$('topen').href='bitcoin:'+TIP_ADDR;}};
$('tipcopy').onclick=async()=>{try{await navigator.clipboard.writeText(TIP_ADDR);toast('address copied')}catch(e){toast('could not copy',true)}};
async function poll(){
  const{j}=await get('/api/status'); if(!j)return;
  current=j;
  $('rate').textContent=j.hashrate;
  $('accepted').textContent=j.accepted;
  $('submitted').textContent=j.submitted;
  $('rejected').textContent=j.rejected;
  $('blocks').textContent=j.blocks;
  $('diff').textContent=j.last_diff?('diff '+j.last_diff):'-';
  const m=Math.floor(j.uptime/60),s=j.uptime%60;
  $('up').textContent=m+'m '+s+'s';
  $('netdiff').textContent=j.difficulty??'-';
  $('height').textContent=j.height==null?'-':j.height.toLocaleString();
  $('grate').textContent=j.grate??'-';
  $('est').textContent=j.est??'-';
  $('conn').textContent=(j.pool||'')+'  '+j.wallet;
  $('setup').hidden=j.configured;
  drawRate(j.rate_hist);
  if(j.error)toast(j.error,true);
}
function lvlLabel(l){
  if(l==null)l=parseInt($('cool').value,10)||0;
  const t=current.threads_effective||'-';
  return l===0?'off':('level '+l+' -> '+t+' threads');
}
$('cool').addEventListener('input',()=>$('lvl').textContent=lvlLabel());
$('applyCool').onclick=async()=>{
  const l=parseInt($('cool').value,10);
  const{r,j}=await get('/api/cooling',{level:l});
  if(r.ok){toast('cooling applied');$('lvl').textContent=lvlLabel(l)}else toast((j&&j.error)||'failed',true);
};
$('save').onclick=async()=>{
  const{r,j}=await get('/api/settings',{wallet:$('wallet').value.trim(),pool:$('pool').value.trim(),password:$('pass').value.trim(),worker:$('worker').value.trim(),threads:$('threads').value||null,update_interval:$('ui').value||null,network_refresh:$('ni').value||null,show_network:$('net').checked});
  if(r.ok){toast('saved - miner restarted');poll()}else toast((j&&j.error)||'failed',true);
};
(async()=>{const{j}=await get('/api/status');if(!j)return;$('cool').value=j.cooling_level;$('lvl').textContent=lvlLabel(j.cooling_level);
$('wallet').value=j.wallet===''?'':j.wallet;$('pool').value=j.pool||'';$('pass').value=j.pool_password||'';$('worker').value=j.worker||'';
$('threads').value=j.threads_set||'';$('ui').value=j.update_interval||'';$('ni').value=j.network_refresh||'';$('net').checked=!!j.show_network;})();
setInterval(poll,2000);poll();
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, obj, code=200):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        ctl = self.server.controller
        if self.path in ("/", "/index.html"):
            page = PAGE.replace("{{VER}}", __version__).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if self.path == "/api/status":
            self._json(ctl.status_data())
            return
        if self.path == "/api/widget":
            self._json(ctl.widget_data())
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        ctl = self.server.controller
        if self.path == "/api/cooling":
            body = self._body()
            try:
                ok = ctl.set_cooling(body.get("level", 0))
            except (ValueError, TypeError):
                ok = False
            if ok:
                self._json({"ok": True})
            else:
                self._json({"error": "level must be 0-10"}, 400)
            return
        if self.path == "/api/settings":
            body = self._body()
            ok, err = ctl.set_settings(
                body.get("wallet"), body.get("pool"), body.get("worker"),
                password=body.get("password"),
                threads=body.get("threads"),
                update_interval=body.get("update_interval"),
                network_refresh=body.get("network_refresh"),
                show_network=body.get("show_network"),
            )
            if ok:
                self._json({"ok": True})
            else:
                self._json({"error": err or "invalid settings"}, 400)
            return
        self._json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


def start_server(controller, host="0.0.0.0", port=8080):
    server = ThreadingHTTPServer((host, port), _Handler)
    server.controller = controller
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def keep_alive(controller, interval=10):
    import signal as sig

    stopped = {"flag": False}
    last_restart = 0.0

    def _stop(_s, _f):
        stopped["flag"] = True

    sig.signal(sig.SIGTERM, _stop)
    sig.signal(sig.SIGINT, _stop)

    level = int(controller.config.get("cooling_level") or 0)
    print(f"\n=== bitrom miner v1 (web headless) ===")
    print(f"web      http://0.0.0.0:8080")
    print(f"pool     {controller.config.get('pool')}")
    print(f"cooling  {level if level else 0} "
          f"({'off' if level == 0 else 'level ' + str(level)})")
    controller.start_miner()
    print("[miner] web dashboard serving; Ctrl-C or systemctl stop to finish")
    while not stopped["flag"]:
        if not controller.configured:
            if not controller.mining():
                print(f"[web] waiting for wallet configuration at http://0.0.0.0:8080", flush=True)
        elif not controller.mining():
            now = time.time()
            msg = controller.state.error or "unknown"
            if now - last_restart >= 5:
                print(f"[error] miner process died: {msg} - restarting", flush=True)
                controller.restart_miner()
                last_restart = now
        else:
            s = controller.status_data()
            print(f"[miner] {s['hashrate']:<12} accepted {s['accepted']}"
                  f"  submitted {s['submitted']}  rejected {s['rejected']}"
                  f"  blocks {s['blocks']}  last_diff {s['last_diff'] or '-'}", flush=True)
        for _ in range(10):
            if stopped["flag"]:
                break
            time.sleep(max(1.0, interval / 10.0))
    print("[miner] stopping...", flush=True)
    controller.stop_miner()
    print("[miner] stopped cleanly", flush=True)