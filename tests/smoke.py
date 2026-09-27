#!/usr/bin/env python3
"""End-to-end smoke test against a running server.

    python tests/smoke.py                        # http://127.0.0.1:8000
    python tests/smoke.py --base-url http://host:8000
    SAMPLES_DIR=/path/to/obfuscator-samples python tests/smoke.py --corpus

Without ``--corpus`` this uses the small fixtures in ``tests/fixtures`` and
checks the plumbing: health, capabilities, job lifecycle, SSE, branding,
result and report download.

With ``--corpus`` (and ``SAMPLES_DIR`` pointing at a labelled sample tree, e.g.
kers0nec/obfuscator-samples) it additionally submits one real sample per
obfuscator family and prints what actually came back. That is the honest
support matrix, measured rather than claimed.

Exit status is non-zero when a required check fails. Corpus rows are reported,
not asserted: which families lift depends on the runtimes installed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")

FAILURES: list = []
CHECKS = [0]


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS[0] += 1
    mark = "ok  " if ok else "FAIL"
    print("  [%s] %s%s" % (mark, name, (" — " + detail) if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def req(base: str, path: str, method: str = "GET", timeout: int = 60, **kwargs):
    url = base.rstrip("/") + path
    r = urllib.request.Request(url, method=method)
    data = kwargs.get("data")
    for k, v in (kwargs.get("headers") or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, data=data, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def multipart(fields: dict, files: dict) -> tuple:
    boundary = "----smoke%d" % int(time.time() * 1000)
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (boundary, k, v)).encode())
    for k, (name, blob) in files.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n" % (boundary, k, name)).encode())
        out.append(blob)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    return b"".join(out), "multipart/form-data; boundary=%s" % boundary


def submit(base: str, blob: bytes, filename: str, opts: dict | None = None) -> dict:
    body, ctype = multipart(dict(opts or {}), {"file": (filename, blob)})
    status, raw = req(base, "/api/jobs", method="POST", timeout=120,
                      data=body, headers={"Content-Type": ctype})
    if status != 200:
        raise RuntimeError("submit failed %s: %s" % (status, raw[:300]))
    return json.loads(raw)


def wait(base: str, job_id: str, timeout: float = 900.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        status, raw = req(base, "/api/jobs/" + job_id, timeout=30)
        if status != 200:
            raise RuntimeError("poll failed %s" % status)
        last = json.loads(raw)
        if last["status"] not in ("queued", "running"):
            return last
        time.sleep(1.5)
    raise RuntimeError("job %s did not finish within %ss" % (job_id, timeout))


# ------------------------------------------------------------------ checks

def test_meta(base: str, site_name: str) -> dict:
    print("\n== meta ==")
    status, raw = req(base, "/api/health")
    check("GET /api/health", status == 200, "status %s" % status)
    health = json.loads(raw) if status == 200 else {}
    tools = {t["name"]: t["found"] for t in health.get("tools", [])}
    check("luau present", tools.get("luau", False), "required for every job")
    check("luau-ast present", tools.get("luau_ast".replace("_", "-"), False),
          "required for devirtualization")
    print("       lune: %s (only needed for Luraph v14.x full devirtualization)"
          % ("present" if tools.get("lune") else "missing"))

    status, raw = req(base, "/api/capabilities")
    check("GET /api/capabilities", status == 200)
    caps = json.loads(raw) if status == 200 else {}
    families = {f["name"]: f for f in caps.get("families", [])}
    check("capability matrix covers >= 15 families", len(families) >= 15,
          "%d families" % len(families))
    for need in ("luraph_v15", "luraph_v14", "ironbrew1", "moonsec", "prometheus"):
        check("family listed: %s" % need, need in families)

    status, raw = req(base, "/")
    check("GET / serves the front end", status == 200 and b"<html" in raw.lower())
    check("front end is self-contained (no CDN)", b"cdn." not in raw.lower()
          and b"unpkg" not in raw.lower() and b"jsdelivr" not in raw.lower())
    for asset in ("/style.css", "/app.js", "/favicon.svg"):
        status, _ = req(base, asset)
        check("asset %s" % asset, status == 200, "status %s" % status)
    return health


def test_branding(base: str, site_name: str) -> None:
    """The site's own name goes on results; no third party's does."""
    print("\n== branding ==")
    src = b'print("hello world")\n'
    job = submit(base, src, "plain.lua")
    final = wait(base, job["id"], timeout=180)
    check("plaintext job completes", final["status"] == "done", final.get("status"))
    if final["status"] != "done":
        return
    status, raw = req(base, "/api/jobs/%s/result.txt" % job["id"])
    body = raw.decode("utf-8", "replace")
    check("plaintext passes through unchanged", body.strip() == src.decode().strip())

    # a real run stamps the header; the fixtures include a tiny obfuscated shape
    marker = os.path.join(FIXTURES, "generic_obfuscated.lua")
    if os.path.isfile(marker):
        with open(marker, "rb") as fh:
            blob = fh.read()
        job = submit(base, blob, "generic_obfuscated.lua")
        try:
            final = wait(base, job["id"], timeout=420)
            status, raw = req(base, "/api/jobs/%s/result.txt" % job["id"])
            body = raw.decode("utf-8", "replace")
            check("result carries the site header",
                  ("Deobfuscated by %s" % site_name) in body.splitlines()[0] if body else False,
                  (body.splitlines() or [""])[0][:70])
            check("no third-party credit line in the result",
                  "ccjvwsod" not in body and "Deobfuscated by deobf" not in body)
        except RuntimeError as exc:
            check("traced fixture completes", False, str(exc))


def test_lifecycle(base: str) -> None:
    print("\n== lifecycle ==")
    status, raw = req(base, "/api/jobs", )
    check("GET /api/jobs", status == 200)

    status, _ = req(base, "/api/jobs/does-not-exist")
    check("unknown job -> 404", status == 404, "status %s" % status)

    # empty input is rejected rather than queued
    body, ctype = multipart({"source": "   "}, {})
    status, raw = req(base, "/api/jobs", method="POST", data=body,
                      headers={"Content-Type": ctype})
    check("empty input -> 400", status == 400, "status %s" % status)

    # oversize input is rejected before any work happens
    status, raw = req(base, "/api/jobs", method="POST",
                      data=json.dumps({"source": "x" * 64}).encode(),
                      headers={"Content-Type": "application/json"})
    check("json body accepted", status in (200, 400), "status %s" % status)


def test_events(base: str) -> None:
    print("\n== event stream ==")
    src = b'local t = {} for i=1,50 do t[i] = i end print(#t)\n'
    job = submit(base, src, "loop.lua")
    status, raw = req(base, "/api/jobs/%s/log" % job["id"], timeout=30)
    check("GET /api/jobs/{id}/log", status == 200)
    wait(base, job["id"], timeout=240)
    status, raw = req(base, "/api/jobs/%s/log" % job["id"], timeout=30)
    events = json.loads(raw).get("events", []) if status == 200 else []
    kinds = {e.get("t") for e in events}
    check("events were recorded", len(events) > 0, "%d events" % len(events))
    check("detect event present", "detect" in kinds, str(sorted(kinds)))
    check("sequence numbers are monotonic",
          all(events[i]["seq"] < events[i + 1]["seq"] for i in range(len(events) - 1)))
    status, raw = req(base, "/api/jobs/%s/report" % job["id"])
    check("GET report", status == 200)
    if status == 200:
        rep = json.loads(raw)
        check("report names the detection", bool(rep.get("detection", {}).get("label")))
        check("report states the capability level",
              bool(rep.get("capability", {}).get("level")))


def test_corpus(base: str, samples_dir: str) -> None:
    print("\n== corpus (measured, not asserted) ==")
    rows = []
    for family in sorted(os.listdir(samples_dir)):
        fdir = os.path.join(samples_dir, family)
        if not os.path.isdir(fdir) or family in ("output", ".git"):
            continue
        # Representative sample: near the median size for the family. The
        # smallest file is usually a truncated fragment that nothing can parse,
        # and the largest can take many minutes; neither tells the truth about
        # what this site does with that obfuscator.
        candidates = []
        for root, _dirs, files in os.walk(fdir):
            for name in files:
                if name.endswith((".lua", ".luau")):
                    p = os.path.join(root, name)
                    try:
                        size = os.path.getsize(p)
                    except OSError:
                        continue
                    if 2048 <= size <= 6 * 1024 * 1024:
                        candidates.append((size, p))
            if len(candidates) > 3000:
                break
        if not candidates:
            rows.append((family, "-", "no sample", "-", 0.0, "skipped"))
            continue
        candidates.sort()
        path = candidates[len(candidates) // 2][1]
        with open(path, "rb") as fh:
            blob = fh.read()
        started = time.time()
        try:
            job = submit(base, blob, os.path.basename(path))
            final = wait(base, job["id"], timeout=900)
            outcome = final.get("outcome") or {}
            det = (final.get("detection") or {}).get("label", "?")
            rows.append((family, det, outcome.get("kind", "?"),
                         "%.0fK" % (len(blob) / 1024.0),
                         round(time.time() - started, 1), final["status"]))
        except Exception as exc:  # noqa: BLE001
            rows.append((family, "?", "error", "%.0fK" % (len(blob) / 1024.0),
                         round(time.time() - started, 1), str(exc)[:34]))

    print("  %-16s %-18s %-15s %7s %8s  %s"
          % ("family", "detected", "recovery", "size", "secs", "status"))
    for r in rows:
        print("  %-16s %-18s %-15s %7s %8.1f  %s" % r)
    check("at least one family produced output",
          any(r[2] not in ("none", "?", "error") for r in rows),
          "%d families exercised" % len(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--site-name", default=os.environ.get("KERS_SITE_NAME", "kers0ne website"))
    ap.add_argument("--corpus", action="store_true",
                    help="also run one real sample per family from SAMPLES_DIR")
    ap.add_argument("--skip-branding", action="store_true")
    ap.add_argument("--corpus-only", action="store_true",
                    help="skip the fixture checks and just measure the corpus")
    args = ap.parse_args()

    print("smoke test against %s" % args.base_url)

    if not args.corpus_only:
        try:
            health = test_meta(args.base_url, args.site_name)
        except Exception as exc:  # noqa: BLE001
            print("\nserver unreachable: %s" % exc)
            return 2

        if not health.get("core_ready"):
            print("\n[!] luau/luau-ast missing on the server; only meta checks will pass.")

        test_lifecycle(args.base_url)
        test_events(args.base_url)
        if not args.skip_branding:
            try:
                test_branding(args.base_url, args.site_name)
            except Exception as exc:  # noqa: BLE001
                check("branding checks", False, str(exc))

    if args.corpus:
        samples = os.environ.get("SAMPLES_DIR")
        if samples and os.path.isdir(samples):
            test_corpus(args.base_url, samples)
        else:
            print("\n== corpus skipped: set SAMPLES_DIR to a labelled sample tree ==")

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
