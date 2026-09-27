"""Detection and capability tests.

These run without a server, without Luau and without the sample corpus: they
only exercise the pure-Python fingerprinting layer. Real end-to-end behaviour is
covered by ``tests/smoke.py``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import detectors  # noqa: E402


# ---------------------------------------------------------------- banners

BANNERS = [
    (b"-- This file was protected using Luraph Obfuscator v15.0 [https://lura.ph/]\n"
     b"return setmetatable({[104]=bit32.countlz}, {})\n", "luraph_v15", "15.0"),
    (b"-- This file was protected using Luraph Obfuscator v14.7 [https://lura.ph/]\n"
     b"return(function(...) end)(...)\n", "luraph_v14", "14.7"),
    (b"-- This file was protected using Luraph Obfuscator v14.6 [https://lura.ph/]\n"
     b"return({N=function(o,o,X) end})\n", "luraph_v14", "14.6"),
    (b"--ironbrew3:tm:, v0.235\ndo return(function(F,s,a) end)()end\n", "ironbrew3", "0.235"),
    (b"-- This script was generated using MoonVeil 2.0.15-beta [https://moonveil.cc]\n"
     b"return({C=function(a,c) end})\n", "moonveil", "2.0.15-beta"),
    (b"_, Protected_by_MoonSecV2, Discord = 'x'\n,nil,nil;(function() end)()\n",
     "moonsec", None),
    (b'return(function(e,...)local v="This file was obfuscated using PSU Obfuscator 4.0.A | '
     b'https://www.psu.dev/ & discord.gg/psu" end)(...)\n', "psu", "4.0.A"),
    (b"-- Protected by wYnFuscate: https://wynfuscate.com\nreturn(function(...) end)(...)\n",
     "wynfuscate", None),
    (b"--[Obfuscated by Hercules v1.6.2 | hercules-obfuscator.xyz/discord]\n"
     b"return (function(...) end)(...)\n", "hercules", "1.6.2"),
    (b"do local a=[[77fuscator 0.4.8 - discord.gg/kWy43Y9rR3]];"
     b"return(function(b) end)()end\n", "77fuscator", "0.4.8"),
    (b"--[[\n\therrtt's obfuscator, v0.2.4\n--]]\nlocal a = 1\n", "boronide", "0.2.4"),
    (b"local SynapseXen_a=select;local SynapseXen_b=string.byte;"
     b"local SynapseXen_c=string.sub;local SynapseXen_d=type\n", "synapsexen", None),
    (b"--[[ v1.0.0 https://wearedevs.net/obfuscator ]] return(function(...) end)(...)\n",
     "prometheus", None),
]


@pytest.mark.parametrize("blob,expected,version", BANNERS)
def test_banner_detection(blob, expected, version):
    got = detectors.identify(blob)
    assert got.name == expected, "%r -> %s (wanted %s)" % (blob[:48], got.name, expected)
    assert got.confidence >= detectors.MIN_CONFIDENCE
    if version is not None:
        assert got.version == version


def test_luraph_v15_headerless_shape():
    blob = (b"return setmetatable({[75]=bit32.rrotate,[104]=bit32.countlz,"
            b"[12]=string.byte}, {})\n")
    got = detectors.identify(blob)
    assert got.name == "luraph_v15"
    assert got.confidence >= 0.7


def test_prometheus_escape_table_without_banner():
    esc = "".join("\\%03d" % (40 + i) for i in range(60))
    blob = ('return(function(...)local g={"%s";"%s"} end)(...)\n' % (esc, esc)).encode()
    got = detectors.identify(blob)
    assert got.name == "prometheus"


def test_luaobfuscator_sequential_aliases():
    blob = (b"local v0=string.char;local v1=string.byte;local v2=string.sub;"
            b"local v3=bit32 or bit ;local v4=v3.bxor;local v5=table.concat;"
            b"local v6=table.insert;local function v7(v24,v25) local v26={} "
            b"for v41=1,#v24 do v26[v41]=v0(v4(v1(v2(v24,v41,v41)))) end "
            b"return v5(v26) end\n")
    got = detectors.identify(blob)
    assert got.name == "luaobfuscator"
    assert got.confidence >= 0.8


def test_luaobfuscator_banner_wins_over_shape():
    blob = (b"--[[\n  \\_Welcome to LuaObfuscator.com   (Alpha 0.10.9) ~  Much Love, Ferib \n]]--\n"
            b"local v0=string.char;local v1=string.byte;local v2=string.sub\n")
    got = detectors.identify(blob)
    assert got.name == "luaobfuscator"
    assert got.confidence == 1.0


def test_lps_state_machine():
    params = ",".join(["a%d" % i for i in range(40)])
    blob = ("return(function(%s,...)local bq,br,bs=0;while(y)do "
            "if(bq<=0X18_)then bq=bq+1 end end end)(...)\n" % params).encode()
    got = detectors.identify(blob)
    assert got.name == "lps"


def test_ironbrew2_prologue():
    aliases = ";".join("local %s%d=string.byte" % (chr(65 + i), i) for i in range(8))
    blob = (aliases + ";local Q=getfenv or function()return _ENV end;"
            "local M=math.ldexp\nreturn(function(...) end)(...)\n").encode()
    got = detectors.identify(blob)
    assert got.name == "ironbrew2"


def test_luaobfuscator_is_not_claimed_as_an_ironbrew():
    """The v<N> alias prologue looks like an IronBrew from a distance. Real
    LuaObfuscator samples alias far more than five distinct libraries, which is
    where the IronBrew detectors bow out."""
    blob = (b"local v0=string.char;local v1=string.byte;local v2=string.sub;"
            b"local v3=bit32 or bit ;local v4=v3.bxor;local v5=table.concat;"
            b"local v6=table.insert;local v7=getfenv or function() return _ENV end;"
            b"local v8=math.ldexp;local v9=string.format;local v10=setmetatable;"
            b"local v11=table.remove;local v12=tonumber\n"
            b"local function v13(v24,v25) return v24 end\n")
    got = detectors.identify(blob)
    assert got.name == "luaobfuscator"
    assert got.name not in ("ironbrew1", "ironbrew2", "ironbrew3")


# -------------------------------------------------------------- plaintext

PLAIN = b"""local Players = game:GetService("Players")
local player = Players.LocalPlayer

local function greet(who)
    return "hello, " .. who
end

print(greet(player.Name))
"""


def test_plaintext_is_returned_unchanged_not_traced():
    got = detectors.identify(PLAIN)
    assert got.name == "plaintext"
    assert got.level == "none"


def test_short_plaintext_is_still_plaintext():
    assert detectors.identify(b'print("hello world")\n').name == "plaintext"


@pytest.mark.parametrize("snippet", [
    b'local f = loadstring(game:HttpGet("https://x.test/a")) f()\n',
    b"local chunk = load(payload) chunk()\n",
    b"local e = getfenv(0) e.x = 1\n",
])
def test_dynamic_machinery_is_never_plaintext(snippet):
    """A readable-looking loader is still a loader: it has to be run."""
    got = detectors.identify(snippet)
    assert got.name != "plaintext"


def test_download_is_not_read_as_load():
    """`download(url)` must not trip the `load(` pattern."""
    blob = (b'local function download(url)\n  return game:HttpGet(url)\nend\n'
            b'print(download("https://x.test/a"))\n')
    assert detectors.identify(blob).name == "plaintext"


def test_encoded_blob_is_not_plaintext():
    blob = b'local s = "' + b"A" * 600 + b'"\nprint(#s)\n'
    assert detectors.identify(blob).name != "plaintext"


# ------------------------------------------------------- v14 version-aware

def test_v14_7_is_promised_a_lift():
    got = detectors.identify(
        b"-- This file was protected using Luraph Obfuscator v14.7 [https://lura.ph/]\n")
    assert got.name == "luraph_v14"
    assert got.version == "14.7"
    assert got.version_supported is True
    assert got.level == "devirtualize-runtime"


def test_v14_6_is_promised_only_a_trace():
    got = detectors.identify(
        b"-- This file was protected using Luraph Obfuscator v14.6 [https://lura.ph/]\n")
    assert got.name == "luraph_v14"
    assert got.version_supported is False
    assert got.level == "trace"
    assert "unsupported-lph" in got.notes


def test_supported_v14_versions_are_documented():
    assert detectors.LURAPH_V14_SUPPORTED == ("14.7", "14.8", "14.9")


# ------------------------------------------------------------------ matrix

def test_capability_matrix_covers_the_corpus():
    names = {f["name"] for f in detectors.capability_matrix()}
    for need in ("luraph_v15", "luraph_v14", "ironbrew1", "ironbrew2", "ironbrew3",
                 "moonveil", "moonsec", "psu", "prometheus", "77fuscator", "boronide",
                 "hercules", "luaobfuscator", "synapsexen", "wynfuscate", "lps"):
        assert need in names, "missing family %s" % need
    assert len(names) >= 15


def test_every_level_has_a_description():
    for fam in detectors.FAMILIES.values():
        assert fam.level in detectors.LEVELS, fam.name


def test_all_detections_reports_evidence():
    rows = detectors.all_detections(
        b"-- This file was protected using Luraph Obfuscator v15.0 [https://lura.ph/]\n")
    assert rows and rows[0]["name"] == "luraph_v15"
    assert rows[0]["detail"]


def test_identify_never_raises_on_junk():
    for junk in (b"", b"\x00\x01\x02\xff", b"x" * 100, os.urandom(4096)):
        got = detectors.identify(junk)
        assert got.name in detectors.FAMILIES
