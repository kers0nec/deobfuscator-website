"""Branding and pipeline-plan tests.

The branding half matters more than it looks: the vendored engines stamp their
own author's line into every result, and this site must publish under its own
name. These tests assert the replacement actually happens on the real vendored
module, not just on a copy of the string.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import branding  # noqa: E402

CADMIO_DIR = os.path.join(ROOT, "engine", "cadmio", "deobf")

THIRD_PARTY_CREDIT = "ccjvwsod"


@pytest.fixture(autouse=True)
def restore_env(monkeypatch):
    """Each test gets a known site name and must not leak it to the next."""
    monkeypatch.delenv(branding.SITE_NAME_ENV, raising=False)
    monkeypatch.delenv(branding.SITE_URL_ENV, raising=False)
    branding.load_site()
    yield


# ------------------------------------------------------------- the default

def test_default_site_name():
    assert branding.SITE_NAME == "kers0ne website"
    assert branding.SITE_URL == ""


def test_site_name_is_reconfigurable(monkeypatch):
    monkeypatch.setenv(branding.SITE_NAME_ENV, "someone else's site")
    branding.load_site()
    assert branding.SITE_NAME == "someone else's site"
    assert "someone else's site" in branding.credit_lines("PSU")


@pytest.mark.parametrize("value", ["   ", "", "\n\t "])
def test_blank_site_name_falls_back(monkeypatch, value):
    monkeypatch.setenv(branding.SITE_NAME_ENV, value)
    branding.load_site()
    assert branding.SITE_NAME == branding.DEFAULT_SITE_NAME


def test_site_url_is_trimmed_and_rejected_when_blank(monkeypatch):
    monkeypatch.setenv(branding.SITE_URL_ENV, "\n\t ")
    branding.load_site()
    assert branding.SITE_URL == ""


def test_site_url_becomes_a_second_line(monkeypatch):
    monkeypatch.setenv(branding.SITE_URL_ENV, "https://example.test")
    branding.load_site()
    lines = branding.credit_lines("PSU", kind="trace").splitlines()
    assert any("example.test" in l for l in lines)
    assert lines[0].startswith("-- Deobfuscated by")


# ---------------------------------------------------------------- headers

def test_credit_header_is_the_sites_own():
    header = branding.credit_lines("Luraph v15", kind="devirtualized")
    assert "kers0ne website" in header
    assert THIRD_PARTY_CREDIT not in header.lower()


def test_credit_header_names_the_family():
    assert "Luraph v15" in branding.credit_lines("Luraph v15")


def test_credit_header_without_a_family_has_no_broken_label():
    header = branding.credit_lines("")
    assert "None" not in header
    assert "kers0ne website" in header
    assert "Detected obfuscation" not in header


def test_devirtualized_header_warns_about_inferred_names():
    """The originals are genuinely not in the bytecode; the file has to say so."""
    assert "inferred" in branding.credit_lines("PSU", kind="devirtualized")


def test_trace_header_warns_about_missing_branches():
    """A trace silently omitting unreached branches is the dangerous failure
    mode, so the warning has to live in the artifact, not just the UI."""
    header = branding.credit_lines("PSU", kind="trace")
    assert "(dynamic trace)" in header or "trace" in header.lower()
    assert "missing" in header.lower()


@pytest.mark.parametrize("kind", ["devirtualized", "trace"])
def test_header_ends_in_a_newline(kind):
    """Headers are concatenated onto real source; without the trailing newline
    the first line of the recovered script gets commented out."""
    assert branding.credit_lines("PSU", kind=kind).endswith("\n")


def test_trace_header_records_the_source_filename():
    header = branding.trace_header("/somewhere/protected.lua")
    assert "protected.lua" in header
    assert "kers0ne website (dynamic trace)" in header


def test_trace_header_carries_engine_notes():
    header = branding.trace_header("a.lua", ("some engine note",))
    assert "-- some engine note" in header


# ------------------------------------------- the vendored engine is rebound

def test_install_replaces_the_vendored_trace_header():
    """This is the real requirement: the engine that actually writes the result
    file must emit this site's line."""
    sys.path.insert(0, CADMIO_DIR)
    branding.restore()
    import traceout
    assert traceout.header is not branding.trace_header
    before = traceout.header("/tmp/x.lua")
    assert "kers0ne website" not in before          # upstream's own line

    assert branding.install(CADMIO_DIR) is not None
    after = traceout.header("/tmp/x.lua")
    assert "Deobfuscated by kers0ne website (dynamic trace)" in after
    assert THIRD_PARTY_CREDIT not in after.lower()
    assert "deobf (dynamic trace)" not in after


def test_install_replaces_the_vendored_credit_header():
    sys.path.insert(0, CADMIO_DIR)
    branding.restore()
    assert branding.install(CADMIO_DIR) is not None
    from obfuscators import base as cadmio_base

    class _FakeJob:
        obfuscator = "Luraph v15"
        args = type("A", (), {"no_credit": False})()

    header = cadmio_base.Job.credit_header(_FakeJob())
    assert "kers0ne website" in header
    assert THIRD_PARTY_CREDIT not in header.lower()


def test_install_honours_no_credit():
    """`--no-credit` means no header at all; rebinding must not resurrect one."""
    sys.path.insert(0, CADMIO_DIR)
    assert branding.install(CADMIO_DIR) is not None
    from obfuscators import base as cadmio_base

    class _FakeJob:
        obfuscator = "Luraph v15"
        args = type("A", (), {"no_credit": True})()

    assert cadmio_base.Job.credit_header(_FakeJob()) == ""


def test_install_is_idempotent():
    sys.path.insert(0, CADMIO_DIR)
    assert branding.install(CADMIO_DIR) is not None
    assert branding.install(CADMIO_DIR) is not None
    import traceout
    assert "kers0ne website" in traceout.header("/tmp/x.lua")


def test_restore_puts_upstream_back():
    sys.path.insert(0, CADMIO_DIR)
    assert branding.install(CADMIO_DIR) is not None
    branding.restore()
    import traceout
    assert "kers0ne website" not in traceout.header("/tmp/x.lua")
    branding.install(CADMIO_DIR)          # leave the engine branded for later tests
