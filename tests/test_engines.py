"""Pipeline-planning tests.

`plan_for` is the decision table that turns a detection into engine steps. It is
pure Python and needs no runtimes, so it is worth pinning exactly: a wrong plan
means a family silently gets a weaker recovery than it could have had.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import detectors  # noqa: E402
from server.engines import plan_for  # noqa: E402


def _engines(name, opts=None):
    return [step.engine for step in plan_for(name, opts or {})]


TRACE_ONLY = ("psu", "moonsec", "moonveil", "prometheus", "77fuscator", "boronide",
              "hercules", "luaobfuscator", "synapsexen", "wynfuscate", "lps",
              "ironbrew2", "ironbrew3", "unknown")


def test_luraph_v15_runs_one_sandbox_pass():
    assert _engines("luraph_v15") == ["cadmio"]


def test_luraph_v15_devirt_is_the_default():
    steps = plan_for("luraph_v15", {})
    assert "--no-devirt" not in steps[0].args
    assert "luraph_v15" in steps[0].args


def test_luraph_v15_can_be_asked_for_trace_only():
    steps = plan_for("luraph_v15", {"devirt": False})
    assert "--no-devirt" in steps[0].args


def test_luraph_v14_tries_the_lift_then_the_sandbox():
    """luauvmp fails cleanly on a build it cannot unpack, so the trace after it
    still has to run - that fallback is what stops an unsupported v14 returning
    nothing at all."""
    assert _engines("luraph_v14") == ["luauvmp", "cadmio"]


def test_luraph_v14_does_not_run_the_same_engine_twice():
    assert _engines("luraph_v14") == ["luauvmp", "cadmio"]
    assert len(_engines("luraph_v14")) == 2


def test_ironbrew1_goes_through_the_sandbox_devirtualizer():
    assert _engines("ironbrew1") == ["cadmio"]
    assert "ironbrew1" in plan_for("ironbrew1", {})[0].args


@pytest.mark.parametrize("name", TRACE_ONLY)
def test_trace_only_families_get_one_sandbox_run(name):
    assert _engines(name) == ["cadmio"]
    assert "--no-devirt" in plan_for(name, {})[0].args


def test_plaintext_gets_no_engine_at_all():
    assert _engines("plaintext") == []


def test_every_family_has_a_plan():
    for name in detectors.FAMILIES:
        plan_for(name, {})          # must not raise


@pytest.mark.parametrize("opt,default", [("timeout", 240), ("budget", 30)])
def test_user_options_reach_the_engine(opt, default):
    steps = plan_for("psu", {opt: default + 1})
    assert str(default + 1) in steps[0].args


def test_step_timeout_outlives_the_script_timeout():
    """The step's own kill must come last, or the hard timeout fires before the
    engine has reported why it gave up."""
    for step in plan_for("luraph_v15", {"timeout": 240}):
        assert step.timeout > 240


def test_plan_is_deterministic():
    assert _engines("luraph_v14") == _engines("luraph_v14")
