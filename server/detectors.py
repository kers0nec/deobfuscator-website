"""Obfuscator fingerprints for every family in the sample corpus.

Two jobs live here:

*  ``identify`` answers "what is this?" for all fifteen families, including the
   ones no bundled engine can devirtualize. The UI shows that label next to the
   result so a user knows whether they got real source back or a behaviour
   trace, and why.
*  ``capability`` says what this site can actually do with that family, and
   names the engine that will be tried.

Fingerprints are ordered: the first match above ``MIN_CONFIDENCE`` wins. A
detector returns ``(confidence, detail)``; detail is the literal evidence (the
banner line, the marker) so the report can quote it back.

Nothing here executes the input. Detection is pure string/regex work over the
first slice of the file, which keeps it safe and fast enough to run before we
decide whether to spend minutes on a sandbox.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# Detection reads at most this many bytes. Banners are at the top; the deeper
# markers (LPH, ldexp chains) are searched in a larger window below.
HEAD_BYTES = 64 * 1024
DEEP_BYTES = 512 * 1024

MIN_CONFIDENCE = 0.5

# The luau-vmp engine lifts these Luraph v14 loader layouts. Other v14.x builds
# are recognized and unpacked but come back as `unsupported-lph`, and saying
# otherwise in the UI would be a promise the pipeline cannot keep.
LURAPH_V14_SUPPORTED = ("14.7", "14.8", "14.9")

# What a given capability level means for the user.
LEVELS = {
    "devirtualize": "full devirtualization: the VM bytecode is lifted back to Luau source",
    "devirtualize-runtime": "full devirtualization, but the pipeline needs an extra runtime (see report)",
    "trace": "behaviour trace only: the script is run in a sandboxed Luau VM and what it does is "
             "reconstructed as Luau. Branches that never ran are missing.",
    "unpack": "static unpack: the protected payload / VM interpreter is extracted but not lifted",
    "none": "no automated recovery for this family yet",
}


@dataclass
class Family:
    """One obfuscator family the site knows by name."""

    name: str
    label: str
    level: str
    engine: str = ""
    notes: str = ""
    url: str = ""
    versions: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# families

FAMILIES: Dict[str, Family] = {
    "luraph_v15": Family(
        name="luraph_v15", label="Luraph v15", level="devirtualize", engine="cadmio",
        url="https://lura.ph/", notes="VM bytecode lifted to Luau, including branches that never ran.",
    ),
    "luraph_v14": Family(
        name="luraph_v14", label="Luraph v14.7 / 14.8 / 14.9", level="devirtualize-runtime",
        engine="luauvmp", url="https://lura.ph/",
        notes="These three builds use the two-stream legacy loader, which is unpacked "
              "statically and then devirtualized through a restricted sandbox capture "
              "that needs the Lune runtime. Other v14.x builds are recognized and "
              "diagnosed, but their loader layout is not supported, so they fall back "
              "to a behaviour trace.",
    ),
    "ironbrew1": Family(
        name="ironbrew1", label="IronBrew 1", level="devirtualize", engine="cadmio",
        notes="VM bytecode lifted to Luau. Samples that error inside the harness fall back to a trace.",
    ),
    "ironbrew2": Family(
        name="ironbrew2", label="IronBrew 2", level="trace",
        notes="Bytecode VM; no lifter bundled. The trace recovers the strings and calls it makes.",
    ),
    "ironbrew3": Family(
        name="ironbrew3", label="IronBrew 3", level="trace",
    ),
    "moonveil": Family(
        name="moonveil", label="MoonVeil", level="trace", url="https://moonveil.cc",
        notes="A VM-spec profile for MoonVeil 1.4.5 ships with the luau-vmp engine, but it only "
              "matched 0 opcodes across the corpus we tested, so this site reports a trace.",
    ),
    "moonsec": Family(
        name="moonsec", label="MoonSec", level="trace",
        notes="Control-flow flattening, not a bytecode VM. Traces come back readable.",
    ),
    "psu": Family(
        name="psu", label="PSU", level="trace", url="https://www.psu.dev/",
    ),
    "prometheus": Family(
        name="prometheus", label="Prometheus", level="trace",
        notes="Open-source obfuscator; presets range from light string encoding to a bytecode VM.",
    ),
    "77fuscator": Family(
        name="77fuscator", label="77fuscator", level="trace",
    ),
    "boronide": Family(
        name="boronide", label="Boronide", level="trace",
        notes="herrtt's obfuscator.",
    ),
    "hercules": Family(
        name="hercules", label="Hercules", level="trace", url="https://hercules-obfuscator.xyz",
    ),
    "luaobfuscator": Family(
        name="luaobfuscator", label="LuaObfuscator", level="trace",
    ),
    "synapsexen": Family(
        name="synapsexen", label="Synapse Xen", level="trace",
    ),
    "wynfuscate": Family(
        name="wynfuscate", label="wYnFuscate", level="trace", url="https://wynfuscate.com",
    ),
    "lps": Family(
        name="lps", label="LPS", level="trace",
    ),
    "unknown": Family(
        name="unknown", label="unknown obfuscator", level="trace",
        notes="No banner matched. The generic sandbox trace still runs.",
    ),
    "plaintext": Family(
        name="plaintext", label="not obfuscated", level="none",
        notes="This does not look obfuscated; the source is returned as-is.",
    ),
}


@dataclass
class Detection:
    name: str
    label: str
    confidence: float
    detail: str
    level: str
    engine: str
    notes: str
    version: Optional[str] = None
    version_supported: Optional[bool] = None

    def as_dict(self) -> dict:
        fam = FAMILIES.get(self.name)
        return {
            "name": self.name,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "detail": self.detail,
            "level": self.level,
            "level_description": LEVELS.get(self.level, ""),
            "engine": self.engine,
            "notes": self.notes,
            "version": self.version,
            "version_supported": self.version_supported,
            "url": fam.url if fam else "",
        }


Detector = Callable[[str, str], Tuple[float, str, Optional[str]]]
DETECTORS: List[Tuple[str, Detector]] = []


def detector(name: str):
    def wrap(fn: Detector) -> Detector:
        DETECTORS.append((name, fn))
        return fn
    return wrap


def _has(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


# --------------------------------------------------------------------------
# individual fingerprints

@detector("luraph_v15")
def _luraph(head: str, deep: str):
    m = re.search(r"This file was protected using Luraph Obfuscator v(\d+)(?:\.(\d+))?", head)
    if m:
        major = m.group(1)
        ver = m.group(0).split(" v")[-1].split(" ")[0]
        if major == "15":
            return 1.0, m.group(0).strip(), ver
        # v14.x is a different loader shape handled by the other engine.
        if major == "14":
            return 0.0, "", None
        return 0.3, m.group(0).strip(), ver
    # banner stripped: v15's shape is `return setmetatable({[75]=bit32.rrotate, ...}, ...)`
    stripped = head.lstrip()[:2000]
    if stripped.startswith("return setmetatable({") and re.search(
            r"\[\d+\]=(bit32|buffer|string|table|math)\.\w+", stripped):
        return 0.8, "headerless v15 VM object shape", None
    if "LPH" in deep[:200000] and re.search(r"\[\d+\]=(bit32|buffer)\.\w+", deep[:20000]):
        return 0.7, "LPH markers + numeric library slots", None
    return 0.0, "", None


@detector("luraph_v14")
def _luraph14(head: str, deep: str):
    m = re.search(r"This file was protected using Luraph Obfuscator v(14[\d.]*)", head)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    m = re.search(r"Luraph Obfuscator v(1[0-4][\d.]*)", head)
    if m:
        return 0.9, m.group(0).strip(), m.group(1)
    # headerless v14 loaders carry the two-stream "LPH!" hex payload.
    n = len(re.findall(r"LPH!", deep[:DEEP_BYTES]))
    if n >= 2:
        return 0.75, "%d raw LPH! streams" % n, None
    if _has(head, "lura.ph") and "setmetatable" in head:
        return 0.6, "lura.ph reference", None
    return 0.0, "", None


@detector("ironbrew1")
def _ironbrew1(head: str, deep: str):
    if re.search(r"IronBrew\s*1", head, re.I):
        return 1.0, "IronBrew 1 banner", None
    # ib1 shape: a `local function` VM with `while true do` dispatch over a
    # bytecode string and ldexp-based constant decoding.
    if re.search(r"\bldexp\b", deep[:80000]) and re.search(r"local\s+\w+\s*=\s*\(?\s*function\s*\(", head[:4000]):
        if "getfenv" in deep[:80000] and re.search(r"string\.(byte|sub|char)", head[:3000]):
            # LuaObfuscator's sequential `v<N>` aliases look like this from a
            # distance; its detector scores higher, but do not compete with it.
            if re.search(r"local\s+v\d+\s*=", head[:1500]):
                return 0.0, "", None
            return 0.55, "ib1-style VM dispatch + ldexp constants", None
    return 0.0, "", None


@detector("ironbrew2")
def _ironbrew2(head: str, deep: str):
    if _has(head, "ironbrew2") or _has(head, "ironbrew 2"):
        return 1.0, "IronBrew 2 banner", None
    # ib2 ships without a banner but has a very recognizable prologue: a run of
    # `local X=string.byte;` aliases then `getfenv or function()return _ENV end`.
    # LuaObfuscator.com aliases the standard library into sequential `v<N>`
    # locals and scores the same 0.85 on this shape. Ties resolve to whichever
    # detector registered first, so without this guard an ib2-looking
    # LuaObfuscator file gets the wrong label - same fix as _ironbrew1.
    if re.search(r"local\s+v\d+\s*=\s*(?:string|table|math|bit32|bit)\b", head[:1500]):
        return 0.0, "", None
    aliases = re.findall(r"local\s+\w+\s*=\s*(string|table|math|bit32)\.\w+", head[:1500])
    if len(aliases) >= 6 and re.search(r"getfenv\s+or\s+function\s*\(\s*\)\s*return\s+_ENV\s+end", head[:2000]):
        if "ldexp" in deep[:120000]:
            return 0.85, "ib2 prologue: %d library aliases + getfenv/_ENV shim + ldexp" % len(aliases), None
    return 0.0, "", None


@detector("ironbrew3")
def _ironbrew3(head: str, deep: str):
    m = re.search(r"ironbrew3(?::tm:)?,?\s*v?([\d.]+)?", head[:400], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    return 0.0, "", None


@detector("moonveil")
def _moonveil(head: str, deep: str):
    m = re.search(r"generated using MoonVeil\s*([\w.\-]+)?", head[:400], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    if _has(head, "moonveil.cc") or _has(head, "moonveil"):
        return 0.9, "MoonVeil reference", None
    return 0.0, "", None


@detector("moonsec")
def _moonsec(head: str, deep: str):
    m = re.search(r"Protected_by_MoonSec(V?\d*)", head[:400])
    if m:
        return 1.0, m.group(0), m.group(1) or None
    if re.search(r"_msec\s*=", head[:600]):
        return 0.95, "_msec dispatcher", None
    if _has(head, "moonsec"):
        return 0.85, "MoonSec reference", None
    return 0.0, "", None


@detector("psu")
def _psu(head: str, deep: str):
    m = re.search(r"obfuscated using PSU Obfuscator\s*([\w.]+)", deep[:DEEP_BYTES], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    if _has(deep[:80000], "psu.dev"):
        return 0.9, "psu.dev reference", None
    return 0.0, "", None


@detector("prometheus")
def _prometheus(head: str, deep: str):
    if _has(head, "prometheus"):
        return 1.0, "Prometheus banner", None
    # wearedevs.net's obfuscator is a Prometheus build with its own banner
    if _has(head, "wearedevs.net/obfuscator"):
        return 0.95, "wearedevs.net obfuscator banner (Prometheus build)", None
    # Prometheus emits decimal-escaped string tables: {"\122\081\119..."; ...}
    # Escapes are 2 or 3 digits (`\108` and `\055` both occur), separated by
    # commas *and* semicolons.
    m = re.search(r'\{\s*"\\\d{2,3}\\\d{2,3}\\\d{2,3}', head[:3000])
    if m:
        return 0.85, "Prometheus decimal-escape string table", None
    esc = re.findall(r"\\\d{2,3}", head[:3000])
    if len(esc) > 40:
        return 0.7, "%d decimal escapes in the first 3 KB" % len(esc), None
    return 0.0, "", None


@detector("77fuscator")
def _77fuscator(head: str, deep: str):
    m = re.search(r"77fuscator\s*([\d.]+)", head[:600], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    return 0.0, "", None


@detector("boronide")
def _boronide(head: str, deep: str):
    m = re.search(r"herrtt'?s obfuscator,?\s*v?([\d.]+)?", head[:400], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    if _has(head, "boronide"):
        return 0.95, "Boronide reference", None
    return 0.0, "", None


@detector("hercules")
def _hercules(head: str, deep: str):
    m = re.search(r"Obfuscated by Hercules\s*v?([\d.]+)?", head[:400], re.I)
    if m:
        return 1.0, m.group(0).strip(), m.group(1)
    if _has(head, "hercules-obfuscator"):
        return 0.9, "hercules-obfuscator reference", None
    return 0.0, "", None


@detector("luaobfuscator")
def _luaobfuscator(head: str, deep: str):
    if _has(head, "luaobfuscator") or _has(head, "lua.obfuscator"):
        return 1.0, "LuaObfuscator banner", None
    # LuaObfuscator.com renames every local to `v<N>` in declaration order and
    # aliases the standard library into them first:
    #   local v0=string.char;local v1=string.byte;local v2=string.sub;...
    # The banner is only on some presets, so the alias run is the real signal.
    seq = [int(n) for n in re.findall(r"local\s+v(\d+)\s*=", head[:3000])]
    if len(seq) >= 4:
        ordered = seq == sorted(seq) and len(set(seq)) == len(seq) and seq[0] in (0, 1)
        libs = len(re.findall(r"local\s+v\d+\s*=\s*(?:string|table|bit32|bit|math)\b",
                              head[:3000]))
        if ordered and libs >= 2:
            return 0.9, ("LuaObfuscator-style sequential v%d aliases, %d of them library "
                         "bindings" % (len(seq), libs)), None
    # ...and its helpers take v-numbered parameters: `local function v7(v24,v25)`
    if re.search(r"function\s+v\d+\s*\(\s*v\d+\s*,\s*v\d+", head[:6000]):
        return 0.8, "LuaObfuscator-style v-numbered helper signature", None
    if re.search(r"_G\.\w+\s*=\s*\{", head[:4000]) and len(seq) >= 3:
        return 0.6, "LuaObfuscator-style _G control table", None
    return 0.0, "", None


@detector("synapsexen")
def _synapsexen(head: str, deep: str):
    n = len(re.findall(r"\bSynapseXen_\w+", head[:4000]))
    if n >= 3:
        return 1.0, "%d SynapseXen_ prefixed locals" % n, None
    if _has(head, "synapse xen") or _has(head, "synapsexen"):
        return 0.9, "Synapse Xen reference", None
    return 0.0, "", None


@detector("wynfuscate")
def _wynfuscate(head: str, deep: str):
    m = re.search(r"Protected by wYnFuscate", head[:400], re.I)
    if m:
        v = re.search(r"wYnFuscate[:\s]*([\dQq]+)", head[:400], re.I)
        return 1.0, m.group(0), v.group(1) if v else None
    if _has(head, "wynfuscate"):
        return 0.9, "wYnFuscate reference", None
    return 0.0, "", None


@detector("lps")
def _lps(head: str, deep: str):
    if _has(head, "lps obfuscator") or _has(head, "lunar protection"):
        return 0.95, "LPS banner", None
    # LPS: `return(function(<many 1-3 char params>)local a,b,c=0;while(x)do` with
    # a state machine over hex/binary literals, often written `0X18_` / `0B1_`.
    m = re.match(r"\s*return\s*\(?\s*function\s*\(([^)]{60,}?)\)", head[:9000], re.S)
    if m:
        params = [p.strip() for p in m.group(1).split(",") if p.strip()]
        short = sum(1 for p in params if 1 <= len(p) <= 3 and p != "...")
        if len(params) >= 15 and short / max(1, len(params)) > 0.85:
            loop = re.search(r"while\s*\(\s*\w+\s*\)\s*do", head[:9000])
            radix = re.search(r"0[xXbB][0-9a-fA-F]+_", head[:9000])
            if loop or radix:
                return 0.75, ("LPS-style closure: %d short parameters, %s"
                              % (len(params), "state loop" if loop else "radix literals")), None
    return 0.0, "", None


# --------------------------------------------------------------------------
# plaintext check

# Machinery that makes a script dynamic: whatever else it looks like, there is
# something hidden in it that only running will reveal. Matched as patterns, not
# substrings, so `download(x)` does not read as `load(x)`.
_DYNAMIC_PATTERNS = (
    re.compile(r"\bloadstring\s*\("),
    re.compile(r"(?<![\w.:])load\s*\("),
    re.compile(r"\bgetfenv\b"),
    re.compile(r"\bsetfenv\b"),
    re.compile(r"\bldexp\b"),
    re.compile(r"\bloadstring\b"),
)

# Ordinary library calls that obfuscators lean on but plain scripts use too.
_SOFT_HINTS = ("string.byte", "string.char", "string.sub", "table.concat",
               "setmetatable", "bit32", "buffer.", "string.rep", "string.format")


def _looks_plaintext(text: str) -> bool:
    """True when the file is ordinary readable source with nothing hidden in it.

    The point of this check is to avoid putting a normal script through a
    sandbox and handing back a lossy trace of it. It is deliberately strict in
    the direction of "run it anyway": any dynamic-execution machinery at all
    disqualifies a file, because a readable-looking loader is still a loader.
    """
    body = text[:DEEP_BYTES]
    if len(body.strip()) < 16:
        return False

    # numeric string escapes: "\104\101\108..." walls are never hand-written
    if len(re.findall(r"\\\d{2,3}", body)) > 30:
        return False
    # a wall of one/two-character locals is a flattened dispatcher
    if len(re.findall(r"\blocal\s+[A-Za-z_]\w?\s*=", body[:20000])) > 60:
        return False
    lines = body.splitlines()[:2000]
    if lines and max(len(l) for l in lines) > 2000:
        return False
    # a long token run with no whitespace is an encoded blob, not source
    if re.search(r"[A-Za-z0-9+/=]{400,}", body):
        return False
    # any dynamic-execution machinery means it is not plaintext
    if any(p.search(body) for p in _DYNAMIC_PATTERNS):
        return False
    soft = sum(1 for h in _SOFT_HINTS if h in body)
    if soft > 4:
        return False
    # readable source is mostly letters, digits, spaces and a little punctuation
    sample = body[:20000]
    words = sum(c.isalnum() or c.isspace() for c in sample)
    return words / max(1, len(sample)) > 0.70


def identify(data: bytes) -> Detection:
    """Classify raw input bytes. Never raises for odd input."""
    try:
        text = data.decode("latin-1")
    except Exception:  # noqa: BLE001
        text = ""
    head = text[:HEAD_BYTES]
    deep = text[:DEEP_BYTES]

    best: Optional[Tuple[float, str, str, Optional[str]]] = None
    for name, fn in DETECTORS:
        try:
            conf, detail, version = fn(head, deep)
        except Exception:  # noqa: BLE001 - one broken detector must not stop the rest
            conf, detail, version = 0.0, "", None
        if best is None or conf > best[0]:
            best = (conf, detail, name, version)

    if best and best[0] >= MIN_CONFIDENCE:
        conf, detail, name, version = best
        fam = FAMILIES[name]
        level, notes, supported = fam.level, fam.notes, None
        label = fam.label
        if name == "luraph_v14":
            # Only 14.7/14.8/14.9 loaders lift. Anything else in the v14 line is
            # recognized and diagnosed, then falls through to the sandbox trace.
            major_minor = ".".join((version or "").split(".")[:2])
            if version:
                supported = major_minor in LURAPH_V14_SUPPORTED
                label = "Luraph v%s" % version
                if supported:
                    notes = ("v%s uses the two-stream legacy loader layout, which the "
                             "luau-vmp engine unpacks and lifts. Full devirtualization "
                             "needs the Lune runtime." % major_minor)
                else:
                    level = "trace"
                    notes = ("v%s is recognized, but its loader layout is not one the "
                             "luau-vmp engine can unpack (it reports `unsupported-lph`). "
                             "Supported v14 builds: %s. This job gets the static "
                             "diagnosis plus a behaviour trace."
                             % (major_minor, ", ".join(LURAPH_V14_SUPPORTED)))
            else:
                notes = ("Version could not be read from the banner. The luau-vmp engine "
                         "lifts %s loaders; anything else comes back as a behaviour "
                         "trace." % ", ".join(LURAPH_V14_SUPPORTED))
        return Detection(name=name, label=label, confidence=conf, detail=detail,
                         level=level, engine=fam.engine, notes=notes, version=version,
                         version_supported=supported)

    if _looks_plaintext(text):
        fam = FAMILIES["plaintext"]
        return Detection(name="plaintext", label=fam.label, confidence=0.9,
                         detail="no obfuscation markers found", level=fam.level,
                         engine="", notes=fam.notes)

    fam = FAMILIES["unknown"]
    conf = best[0] if best else 0.0
    return Detection(name="unknown", label=fam.label, confidence=round(conf, 3),
                     detail=(best[1] if best and best[1] else "no banner matched"),
                     level=fam.level, engine="cadmio", notes=fam.notes)


def all_detections(data: bytes) -> List[dict]:
    """Every family's score, for the report's evidence table."""
    try:
        text = data.decode("latin-1")
    except Exception:  # noqa: BLE001
        text = ""
    head, deep = text[:HEAD_BYTES], text[:DEEP_BYTES]
    rows = []
    for name, fn in DETECTORS:
        try:
            conf, detail, version = fn(head, deep)
        except Exception:  # noqa: BLE001
            conf, detail, version = 0.0, "", None
        rows.append({"name": name, "label": FAMILIES[name].label,
                     "confidence": round(conf, 3), "detail": detail, "version": version})
    rows.sort(key=lambda r: -r["confidence"])
    return rows


def capability_matrix() -> List[dict]:
    """The honest support table the UI renders."""
    order = ["luraph_v15", "luraph_v14", "ironbrew1", "ironbrew2", "ironbrew3", "moonveil",
             "moonsec", "psu", "prometheus", "77fuscator", "boronide", "hercules",
             "luaobfuscator", "synapsexen", "wynfuscate", "lps", "unknown"]
    return [
        {"name": n, "label": FAMILIES[n].label, "level": FAMILIES[n].level,
         "description": LEVELS[FAMILIES[n].level], "engine": FAMILIES[n].engine,
         "notes": FAMILIES[n].notes, "url": FAMILIES[n].url}
        for n in order
    ]
