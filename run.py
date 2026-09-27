#!/usr/bin/env python3
"""Start the deobfuscator site.

    python run.py                        # 0.0.0.0:8000
    PORT=9000 python run.py
    KERS_SITE_NAME="my deobfuscator" python run.py

Environment
-----------
KERS_HOST / PORT          bind address (default 0.0.0.0:8000). Render injects
                          PORT; nothing here needs to know the value.
KERS_SITE_NAME            name stamped into results and shown in the UI
KERS_SITE_URL             optional URL line in the result header
KERS_WORKERS              concurrent jobs (default 2; use 1 on 512 MB hosts)
KERS_MAX_QUEUE            queued jobs before refusing new ones (default 64)
KERS_MAX_INPUT_BYTES      upload cap (default 32 MiB)
KERS_RETENTION_SECONDS    how long finished jobs are kept (default 6 h)
KERS_KEEP_FINISHED        finished jobs kept regardless of age (default 50)
KERS_JOB_DIR              job storage (default var/jobs)
KERS_MIN_FREE_MB          disk headroom before pruning and refusing work
                          (default 1024, 0 disables)
KERS_DEFAULT_TIMEOUT      per-run harness limit when a job sets none (240)
KERS_DEFAULT_BUDGET       soft time budget for the traced script (30)
KERS_DEFAULT_DEVIRT_TIMEOUT  limit for the Luraph v14.x lift stage (600)
KERS_PROXY_HEADERS        trust X-Forwarded-For/Proto from a platform proxy
                          (default 1; Render and friends terminate TLS for you)
LUAU_PATH / LUAU_AST_PATH / LUNE_PATH   explicit binary locations

Hosting on Render is configured by render.yaml at the repo root; see the
"Render" section of README.md for plan sizing and the single-instance rule.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    from server import runtime
    state = runtime.health()
    print("[*] %s" % state["site_name"])
    for tool in state["tools"]:
        print("    %-12s %s" % (tool["name"], "ok  " + (tool["path"] or "") if tool["found"]
                                else "MISSING  (" + tool["how_to_install"] + ")"))
    if not state["core_ready"]:
        print("[!] the sandbox VM is missing: every job will fail until luau is built.")
        print("    run:  python tools/build_luau.py")
    if not state["luraph_v14_full_ready"]:
        print("[i] Luraph v14.x will unpack + trace only. For full devirtualization:")
        print("    run:  bash tools/install_lune.sh")
    from server.app import main as serve
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
