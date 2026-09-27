"""Job-store storage tests.

The disk guard exists because hosted platforms hand each container a small
ephemeral volume: filling it breaks the whole container, not just one job. These
tests pin the two ways it can go wrong - refusing work that would have fit, and
failing to refuse work that would have filled the disk.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import jobs  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A job store on a temp dir, with a generous headroom config by default."""
    monkeypatch.setattr(jobs, "MIN_FREE_MB", 256)
    monkeypatch.setattr(jobs, "RETENTION_SECONDS", 6 * 3600)
    monkeypatch.setattr(jobs, "KEEP_FINISHED", 50)
    s = jobs.JobStore(root=str(tmp_path / "jobs"), workers=1)
    return s


# ------------------------------------------------------------------ headroom

def test_headroom_is_the_configured_value_on_a_big_volume(store, monkeypatch):
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    assert jobs.headroom_mb(store.root) == 256.0


def test_headroom_clamps_to_a_quarter_of_a_small_volume(store, monkeypatch):
    """The regression this guards: MIN_FREE_MB=1024 on a volume with ~962 MiB
    free made the service refuse every upload, forever."""
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=1000, free=962))
    assert jobs.headroom_mb(store.root) == 250.0
    assert jobs.headroom_mb(store.root) < 962          # work can still be accepted


def test_headroom_has_a_floor(store, monkeypatch):
    """A tiny volume must not clamp headroom to zero, which would disable the
    guard exactly where it is most needed."""
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda p: _usage(total=64, free=8))
    assert jobs.headroom_mb(store.root) == 32.0


def test_headroom_zero_disables_the_guard(store, monkeypatch):
    monkeypatch.setattr(jobs, "MIN_FREE_MB", 0)
    monkeypatch.setattr(jobs.shutil, "disk_usage", lambda p: _usage(total=100, free=1))
    assert jobs.headroom_mb(store.root) == 0.0


def test_headroom_survives_an_unreadable_volume(store, monkeypatch):
    def boom(p):
        raise OSError("no such volume")
    monkeypatch.setattr(jobs.shutil, "disk_usage", boom)
    assert jobs.headroom_mb(store.root) == 256.0
    assert jobs.free_mb(store.root) is None


class _Usage:
    """Stand-in for shutil.disk_usage's named tuple; sizes are given in MiB."""

    def __init__(self, total, free):
        self.total = total * 1024 * 1024
        self.free = free * 1024 * 1024
        self.used = self.total - self.free


def _usage(total, free):
    return _Usage(total, free)


# ------------------------------------------------------------- accepting work

def test_job_accepted_with_plenty_of_space(store, monkeypatch):
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    job = store.create(b"print('hi')\n", "a.lua")
    assert job.id
    assert os.path.isfile(os.path.join(job.dir, "input.lua"))


def test_job_refused_when_the_disk_is_full(store, monkeypatch):
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=10))
    with pytest.raises(jobs.StorageFull):
        store.create(b"print('hi')\n", "a.lua")


def test_refusal_prunes_before_giving_up(store, monkeypatch):
    """A full disk with old finished jobs on it should try to self-heal before
    saying no, so that the next upload succeeds instead of the service staying
    wedged until the periodic prune happens to run."""
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    old = store.create(b"print('old')\n", "old.lua")
    store._finish(old, jobs.STATUS_DONE)
    assert store.get(old.id) is not None

    pruned = []
    real_rmtree = jobs.shutil.rmtree

    def spy(path, *a, **k):
        pruned.append(path)
        return real_rmtree(path, *a, **k)

    # Now the volume is full. The store cannot make room (the fake disk never
    # gains space), so it must still refuse - but only after trying.
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=10))
    monkeypatch.setattr(jobs.shutil, "rmtree", spy)
    with pytest.raises(jobs.StorageFull):
        store.create(b"print('new')\n", "new.lua")
    assert pruned, "the finished job should have been pruned while making room"
    assert store.get(old.id) is None


def test_running_jobs_are_never_pruned_for_space(store, monkeypatch):
    """Killing a running job to reclaim disk would corrupt its own output."""
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    running = store.create(b"print('busy')\n", "busy.lua")
    running.status = jobs.STATUS_RUNNING

    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=1))
    store._prune()
    assert store.get(running.id) is not None
    assert os.path.isdir(running.dir)


def test_finished_jobs_are_pruned_for_space(store, monkeypatch):
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    done = store.create(b"print('done')\n", "done.lua")
    store._finish(done, jobs.STATUS_DONE)
    assert os.path.isdir(done.dir)

    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=1))
    store._prune()
    assert store.get(done.id) is None
    assert not os.path.isdir(done.dir)


def test_prune_stops_once_there_is_room(store, monkeypatch):
    """It should reclaim the minimum, not wipe the whole history."""
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=90_000))
    created = []
    for i in range(5):
        j = store.create(("print(%d)\n" % i).encode(), "j%d.lua" % i)
        store._finish(j, jobs.STATUS_DONE)
        created.append(j)

    free = {"mb": 1}
    monkeypatch.setattr(jobs.shutil, "disk_usage",
                        lambda p: _usage(total=100_000, free=free["mb"]))
    real_rmtree = jobs.shutil.rmtree

    def rmtree_and_free(path, *a, **k):
        free["mb"] += 200            # each pruned job returns space
        return real_rmtree(path, *a, **k)

    monkeypatch.setattr(jobs.shutil, "rmtree", rmtree_and_free)
    store._prune()
    assert store.get(created[-1].id) is not None, "the newest job should survive"


# ------------------------------------------------------------- input limits

def test_oversized_input_is_refused_before_touching_disk(store, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_INPUT_BYTES", 16)
    with pytest.raises(ValueError):
        store.create(b"x" * 64, "big.lua")


def test_empty_input_is_refused(store):
    with pytest.raises(ValueError):
        store.create(b"   \n", "empty.lua")
