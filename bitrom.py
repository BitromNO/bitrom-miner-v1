#!/usr/bin/env python3
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bitrom import __version__, config as config_mod
from bitrom import miner, network
from bitrom.ui import run

BANNER = r"""
      ____  _  ____  ____  __  __
     | __ )(_)/ ___||  _ \|  \/  |   Bitrom Miner
     |  _ \| | |_  | |_) | |\/| |    _____ _____ ____
     | |_) | |  _| |  _ <| |  | |   |_   _| ____|  _ \
     |____/|_|_|   |_| \_\_|  |_|     | | |  _| | |_) |
                                      | | | |___|  _ <
                                      |_| |_____|_| \_\
"""


def main():
    parser = argparse.ArgumentParser(
        prog="bitrom",
        description="Solo CPU Bitcoin miner with a NerdMiner v2 style dashboard.",
    )
    parser.add_argument("-w", "--wallet", help="Bitcoin address for solo pool")
    parser.add_argument("-p", "--pool", default=None, help="solo pool stratum url")
    parser.add_argument("-u", "--worker", default=None, help="worker name (appended to address)")
    parser.add_argument("-t", "--threads", type=int, default=None, help="number of miner threads")
    parser.add_argument("--quiet", action="store_true",
                        help="start in max cooling mode (level 10, fewest threads)")
    parser.add_argument("--level", type=int, default=None,
                        help="cooling level 1-10 (default 0 = full speed)")
    parser.add_argument("--headless", action="store_true",
                        help="run without dashboard (logs to stdout, for systemd)")
    parser.add_argument("--rebuild", action="store_true", help="force rebuild cpuminer")
    parser.add_argument("--no-network", action="store_true", help="skip network stats fetching")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"bitrom miner v{__version__}")
        return

    cfg = config_mod.load()
    for key, value in (("wallet_address", args.wallet), ("worker_name", args.worker),
                       ("pool", args.pool), ("threads", args.threads)):
        if value is not None:
            cfg.set(key, value)
    if args.quiet:
        cfg.set("cooling_level", 10)
    if args.level is not None:
        cfg.set("cooling_level", min(10, max(0, args.level)))

    if not cfg.get("wallet_address"):
        if args.headless:
            print("[error] no wallet configured yet.", file=sys.stderr)
            print("  Run 'python3 bitrom.py' once interactively to set it up, "
                  "then start the service.", file=sys.stderr)
            sys.exit(1)
        config_mod.prompt_first_run(cfg)

    os.system("clear")
    print(BANNER)
    print()

    binary = miner.ensure_backend(force=args.rebuild)

    state = miner.MinerState()
    proc = miner.MinerProcess(cfg, state)
    if args.no_network or args.headless:
        net = network.Network(interval=0)
        net.ok = False
        net.difficulty = None
    else:
        net = network.Network(interval=cfg.get("network_refresh"))
        net.start()

    try:
        proc.start()
    except FileNotFoundError:
        print("[error] could not launch miner binary:", binary)
        sys.exit(1)

    if args.headless:
        miner.run_headless(state, proc)
    else:
        run(state, net, cfg, proc)

    print("\n[stopping] shutting down miner...")
    proc.stop()
    net.stop()


if __name__ == "__main__":
    main()