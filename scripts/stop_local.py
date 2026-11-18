"""Stop a SteadyOps stack started with scripts/start_local.py (and its children).

Only Python processes whose command line runs THIS repository's
scripts/start_local.py are touched.
"""
from __future__ import annotations

import os
from pathlib import Path

import psutil

TARGET = (Path(__file__).resolve().parent / "start_local.py").as_posix().lower()


def main() -> None:
    me = os.getpid()
    found = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        name = (p.info["name"] or "").lower()
        args = [a.replace("\\", "/").lower() for a in (p.info["cmdline"] or [])]
        if p.pid == me or not name.startswith("python"):
            continue
        if not any(a == TARGET or (a.endswith("scripts/start_local.py") and Path(a).name == "start_local.py") for a in args):
            continue
        found += 1
        kids = p.children(recursive=True)
        print(f"stopping start_local.py (pid {p.pid}) and {len(kids)} child process(es)")
        for k in kids:
            try:
                k.terminate()
            except psutil.Error:
                pass
        p.terminate()
        psutil.wait_procs(kids + [p], timeout=10)
    if not found:
        print("no running SteadyOps stack found")


if __name__ == "__main__":
    main()
