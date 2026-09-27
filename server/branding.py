"""Site branding, applied at runtime.

The bundled engines stamp their own author's line into every result file:

*  Cadmio's ``Job.credit_header`` writes ``-- Deobfuscated by ccjvwsod on Discord``
*  its ``traceout.header`` writes ``-- Deobfuscated by deobf (dynamic trace)``

This site publishes results under its own name instead, so both seams are
rebound here at import time. Nothing in ``engine/`` is edited to do it: the
vendored sources stay byte-for-byte as upstream shipped them, which is what
their licenses ask for (see CREDITS.md). Rebinding a documented seam is also
exactly how the upstream CLI's own ``--no-credit`` flag suppresses the header.

Set ``KERS_SITE_NAME`` to change the name that appears in results.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

SITE_NAME_ENV = "KERS_SITE_NAME"
SITE_URL_ENV = "KERS_SITE_URL"
DEFAULT_SITE_NAME = "kers0ne website"

SITE_NAME = DEFAULT_SITE_NAME
SITE_URL = ""


def load_site() -> None:
    """(Re)read the site name and URL from the environment.

    Called at import; kept separate so the tests can point it at another name
    without reloading the module, and so a long-lived server can pick up a
    change if it ever needs to.
    """
    global SITE_NAME, SITE_URL
    SITE_NAME = os.environ.get(SITE_NAME_ENV, "").strip() or DEFAULT_SITE_NAME
    SITE_URL = os.environ.get(SITE_URL_ENV, "").strip()


load_site()

_patched = False
_originals: dict = {}


def credit_lines(obfuscator_label: str = "", kind: str = "devirtualized") -> str:
    """The header this site puts on a result file.

    ``kind`` is ``devirtualized`` when the VM bytecode was lifted back to
    source, ``trace`` when the output is a reconstructed behaviour trace. The
    distinction is repeated in the file itself on purpose: a trace is missing
    every branch the sandbox never reached, and a reader who mistakes it for
    the original source will draw the wrong conclusions from it.
    """
    lines = ["-- Deobfuscated by %s" % SITE_NAME]
    if SITE_URL:
        lines.append("-- %s" % SITE_URL)
    if obfuscator_label:
        lines.append("-- Detected obfuscation: %s" % obfuscator_label)
    if kind == "devirtualized":
        lines.append("-- Local names are inferred from use (the originals are not in the bytecode)")
    else:
        lines.append("-- Behaviour trace: reconstructed from what the script did in a sandbox.")
        lines.append("-- Branches that never ran are missing; conditions appear only as comments.")
    return "".join("-- %s\n" % l if not l.startswith("--") else l + "\n" for l in lines)


def trace_header(input_path: Any, notes: Any = ()) -> str:
    """The header that goes on top of a behaviour trace.

    Keeps upstream's ``-- source:`` and ``-- NOTE:`` lines, which carry real
    information, and only swaps the attribution line.
    """
    lines = [
        "-- Deobfuscated by %s (dynamic trace)" % SITE_NAME,
        "-- source: %s" % os.path.basename(str(input_path)),
        "-- NOTE: reconstructed from observed behaviour; branches that were not taken",
        "--       during the trace are missing and conditions are only noted in comments.",
    ]
    if SITE_URL:
        lines.insert(1, "-- %s" % SITE_URL)
    lines.extend("-- %s" % n for n in notes)
    return "".join(l + "\n" for l in lines)


def install(cadmio_deobf_dir: str) -> Optional[Any]:
    """Rebind the two header seams in the vendored Cadmio pipeline.

    Returns the ``obfuscators`` module so the caller can keep using it, or None
    if the engine could not be imported. Safe to call more than once.
    """
    global _patched
    if cadmio_deobf_dir not in sys.path:
        sys.path.insert(0, cadmio_deobf_dir)

    try:
        import obfuscators  # noqa: PLC0415 - vendored engine, path set above
        from obfuscators import base as cadmio_base  # noqa: PLC0415
        import traceout  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print("[branding] engine import failed: %s" % exc, file=sys.stderr)
        return None

    if _patched:
        return obfuscators

    _originals["credit_header"] = cadmio_base.Job.credit_header
    _originals["trace_header"] = traceout.header

    def credit_header(self):
        """Site-branded replacement for ``Job.credit_header``."""
        # --no-credit still means "no header at all"; honour it.
        if getattr(getattr(self, "args", None), "no_credit", False):
            return ""
        label = getattr(self, "obfuscator", "") or ""
        return credit_lines(label, kind="devirtualized")

    cadmio_base.Job.credit_header = credit_header
    traceout.header = trace_header

    # luraph_v15/ironbrew1 drivers compose `job.credit_header() + trace.header(...)`;
    # both are now branded, so no further patching is needed there.
    _patched = True
    return obfuscators


def restore() -> None:
    """Put the upstream headers back (used by the tests)."""
    global _patched
    if not _patched:
        return
    try:
        from obfuscators import base as cadmio_base  # noqa: PLC0415
        import traceout  # noqa: PLC0415
        cadmio_base.Job.credit_header = _originals["credit_header"]
        traceout.header = _originals["trace_header"]
    except Exception:  # noqa: BLE001
        pass
    _patched = False
