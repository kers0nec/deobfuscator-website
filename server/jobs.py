"""Job store, worker pool and event stream.

Jobs live in a per-job directory under ``KERS_JOB_DIR`` (default
``var/jobs``):

    var/jobs/<id>/input.lua        the upload, byte-for-byte
    var/jobs/<id>/result.lua       the winning output
    var/jobs/<id>/artifacts/...    every intermediate the engines produced
    var/jobs/<id>/job.json         status + report
    var/jobs/<id>/events.jsonl     the full event log, for replay on reconnect

Events are appended with a monotonically increasing sequence number so an SSE
client that drops and reconnects with ``?since=N`` gets exactly what it missed
instead of a replay of the whole run.

The pool is deliberately small. A job executes hostile Luau in a VM and can be
CPU-bound for minutes; oversubscribing turns one large sample into a denial of
service against every other user. Queue depth and input size are bounded too.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import detectors, engines, runtime

DEFAULT_JOB_DIR = os.environ.get(
    "KERS_JOB_DIR", os.path.join(runtime.REPO_ROOT, "var", "jobs"))

MAX_INPUT_BYTES = int(os.environ.get("KERS_MAX_INPUT_BYTES", 32 * 1024 * 1024))
MAX_QUEUE = int(os.environ.get("KERS_MAX_QUEUE", 64))
WORKERS = int(os.environ.get("KERS_WORKERS", 2))
RETENTION_SECONDS = int(os.environ.get("KERS_RETENTION_SECONDS", 6 * 3600))
KEEP_FINISHED = int(os.environ.get("KERS_KEEP_FINISHED", 50))
# Hosted platforms (Render, Fly, Heroku) give each container a small ephemeral
# volume that is wiped on redeploy. A handful of 6 MB samples and their
# artifacts can fill it, and a full disk breaks the whole container rather than
# just this service - so refuse new work and prune hard before that happens.
# Set to 0 to disable the check.
MIN_FREE_MB = int(os.environ.get("KERS_MIN_FREE_MB", "256"))


class StorageFull(RuntimeError):
    """The job volume is too close to full to accept another upload."""


def free_mb(path: str):
    """Free space on the volume holding `path`, in MiB, or None if unknowable."""
    try:
        return shutil.disk_usage(path).free / (1024.0 * 1024.0)
    except OSError:
        return None


def headroom_mb(path: str) -> float:
    """How much free space to insist on before accepting more work.

    Clamped to a quarter of the volume, and never below 32 MiB. Without the
    clamp a configured headroom larger than the disk itself - easy to hit on a
    small container, where KERS_MIN_FREE_MB=1024 exceeds the whole volume -
    would make the service refuse every upload forever, which is a worse failure
    than the full disk it exists to prevent.
    """
    if MIN_FREE_MB <= 0:
        return 0.0
    try:
        total = shutil.disk_usage(path).total / (1024.0 * 1024.0)
    except OSError:
        return float(MIN_FREE_MB)
    return max(32.0, min(float(MIN_FREE_MB), total * 0.25))

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    dir: str
    filename: str
    size: int
    options: Dict[str, Any]
    status: str = STATUS_QUEUED
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    seq: int = 0
    events: List[dict] = field(default_factory=list)
    detection: Optional[dict] = None
    outcome: Optional[dict] = None
    error: Optional[str] = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def push(self, event_type: str, **fields) -> dict:
        """Append one event.

        ``event_type`` rather than ``kind`` on purpose: events carry a ``kind``
        field of their own (the result class), and a positional parameter with
        that name would collide with it.
        """
        self.seq += 1
        ev = {"seq": self.seq, "t": event_type, "ts": round(time.time(), 3)}
        ev.update(fields)
        self.events.append(ev)
        if len(self.events) > 20000:
            del self.events[: len(self.events) - 20000]
        try:
            with open(os.path.join(self.dir, "events.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, default=str) + "\n")
        except OSError:
            pass
        return ev

    def summary(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "status": self.status,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "seq": self.seq,
            "detection": self.detection,
            "error": self.error,
            "options": self.options,
            "result": (self.outcome or {}).get("result"),
        }


class JobStore:
    def __init__(self, root: str = DEFAULT_JOB_DIR, workers: int = WORKERS):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self._workers = max(1, workers)

    # ------------------------------------------------------------------ api

    def create(self, data: bytes, filename: str, options: Optional[dict] = None) -> Job:
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError("input is %d bytes; the limit is %d" % (len(data), MAX_INPUT_BYTES))
        if not data.strip():
            raise ValueError("input is empty")
        need = headroom_mb(self.root)
        if need:
            avail = free_mb(self.root)
            if avail is not None and avail < need:
                self._prune()                  # try to make room before giving up
                avail = free_mb(self.root)
            if avail is not None and avail < need:
                raise StorageFull(
                    "only %.0f MiB free on the job volume and %.0f MiB is reserved as headroom; "
                    "finished jobs are being pruned - retry shortly" % (avail, need))
        with self._lock:
            pending = sum(1 for j in self._jobs.values()
                          if j.status in (STATUS_QUEUED, STATUS_RUNNING))
            if pending >= MAX_QUEUE:
                raise ValueError("the queue is full (%d jobs); try again shortly" % MAX_QUEUE)
            job_id = time.strftime("%Y%m%d-") + secrets.token_hex(6)
            jobdir = os.path.join(self.root, job_id)
            os.makedirs(jobdir, exist_ok=True)
            safe_name = os.path.basename(filename or "input.lua").replace("\x00", "")[:180]
            input_path = os.path.join(jobdir, "input.lua")
            with open(input_path, "wb") as fh:
                fh.write(data)
            opts = dict(options or {})
            opts.setdefault("devirt", True)
            job = Job(id=job_id, dir=jobdir, filename=safe_name or "input.lua",
                      size=len(data), options=opts)
            self._jobs[job_id] = job
            self._persist(job)
        self._queue.put(job_id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> List[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j.created)
        return [j.summary() for j in jobs[:limit]]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED):
            return False
        job.cancel.set()
        return True

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if not job:
            return False
        job.cancel.set()
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def events_since(self, job: Job, since: int) -> List[dict]:
        with self._lock:
            return [e for e in job.events if e["seq"] > since]

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        with self._lock:
            if self._threads:
                return
            for i in range(self._workers):
                t = threading.Thread(target=self._worker, name="deobf-worker-%d" % i,
                                     daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                self._prune()
                continue
            job = self.get(job_id)
            if job is None:
                continue
            if job.cancel.is_set():
                self._finish(job, STATUS_CANCELLED)
                continue
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001 - a worker thread must never die
                try:
                    self._fail(job, "worker crashed: %s: %s" % (type(exc).__name__, exc))
                except Exception:  # noqa: BLE001
                    pass
            self._prune()

    def _run(self, job: Job) -> None:
        with self._lock:
            job.status = STATUS_RUNNING
            job.started = time.time()
            self._persist(job)
        job.push("status", status=STATUS_RUNNING)
        job.push("log", msg="runtime: %s" % _runtime_brief())

        try:
            with open(os.path.join(job.dir, "input.lua"), "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            self._fail(job, "cannot read the uploaded input: %s" % exc)
            return

        detection = detectors.identify(raw)
        job.detection = detection.as_dict()
        job.push("detect", detection=job.detection)
        job.push("log", msg="detected: %s (confidence %.2f) - %s"
                 % (detection.label, detection.confidence, detection.detail or "no detail"))
        job.push("log", msg="capability: %s" % detectors.LEVELS.get(detection.level, ""))

        if detection.name == "plaintext":
            # Nothing to undo. Hand the source back and say why.
            try:
                dest = os.path.join(job.dir, "result.lua")
                with open(dest, "wb") as fh:
                    fh.write(raw)
                job.outcome = {
                    "engine": "none", "kind": "none", "seconds": 0.0,
                    "result": {"name": "result.lua", "bytes": len(raw)},
                    "artifacts": [], "steps": [],
                    "note": "no obfuscation markers were found; the input is returned unchanged",
                }
                self._finish(job, STATUS_DONE)
            except OSError as exc:
                self._fail(job, str(exc))
            return

        def emit(ev: dict) -> None:
            kind = ev.get("t", "log")
            fields = {k: v for k, v in ev.items() if k != "t"}
            job.push(kind, **fields)

        try:
            outcome = engines.run(os.path.join(job.dir, "input.lua"), job.dir,
                                  detection.name, job.options, emit)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the worker
            self._fail(job, "%s: %s" % (type(exc).__name__, exc))
            return

        if job.cancel.is_set():
            self._finish(job, STATUS_CANCELLED)
            return

        engines.stage_result(outcome, job.dir)
        report_path = os.path.join(job.dir, "report.json")
        report = build_report(job, detection, outcome)
        try:
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, default=str)
        except OSError:
            pass

        job.outcome = {
            "engine": outcome.engine,
            "kind": outcome.kind,
            "seconds": outcome.seconds,
            "error": outcome.error,
            "steps": outcome.steps,
            "result": ({"name": outcome.result_name, "bytes": outcome.result_bytes}
                       if outcome.result_path else None),
            "artifacts": [{"name": a["name"], "bytes": a["bytes"]} for a in outcome.artifacts],
            "runtime": outcome.runtime,
        }
        if outcome.result_path:
            job.push("result", kind=outcome.kind, bytes=outcome.result_bytes,
                     engine=outcome.engine)
        if not outcome.result_path:
            self._fail(job, outcome.error or "no engine produced a result")
            return
        self._finish(job, STATUS_DONE)

    def _fail(self, job: Job, message: str) -> None:
        job.error = message
        job.push("error", msg=message)
        self._finish(job, STATUS_ERROR)

    def _finish(self, job: Job, status: str) -> None:
        with self._lock:
            job.status = status
            job.finished = time.time()
            self._persist(job)
        job.push("status", status=status,
                 seconds=round((job.finished or time.time()) - (job.started or job.created), 2))
        with self._cv:
            self._cv.notify_all()

    def _persist(self, job: Job) -> None:
        try:
            with open(os.path.join(job.dir, "job.json"), "w", encoding="utf-8") as fh:
                json.dump(job.summary(), fh, indent=2, default=str)
        except OSError:
            pass

    def _prune(self) -> None:
        """Drop finished jobs past the retention window or the keep-count, then
        keep dropping oldest-first while the volume is short of headroom."""
        now = time.time()
        with self._lock:
            finished = sorted((j for j in self._jobs.values()
                               if j.status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)),
                              key=lambda j: j.finished or j.created)
            victims = [j for j in finished if now - (j.finished or j.created) > RETENTION_SECONDS]
            taken = {id(j) for j in victims}
            overflow = len(finished) - len(victims) - KEEP_FINISHED
            if overflow > 0:
                victims += [j for j in finished if id(j) not in taken][:overflow]
            for job in victims:
                self._jobs.pop(job.id, None)
                shutil.rmtree(job.dir, ignore_errors=True)

            need = headroom_mb(self.root)
            if not need:
                return
            # Disk pressure overrides both limits: oldest finished job first,
            # until the volume has room again. Running jobs are never touched -
            # killing one to reclaim space would corrupt its own output.
            avail = free_mb(self.root)
            for job in finished:
                if avail is None or avail >= need:
                    return
                if self._jobs.pop(job.id, None) is not None:
                    shutil.rmtree(job.dir, ignore_errors=True)
                    avail = free_mb(self.root)

    def wait(self, job: Job, timeout: float = 30.0) -> bool:
        """Block until the job leaves the running state, or timeout."""
        deadline = time.time() + timeout
        with self._cv:
            while job.status in (STATUS_QUEUED, STATUS_RUNNING):
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)
            return True


def _runtime_brief() -> str:
    h = runtime.health()
    parts = ["luau+luau-ast %s" % ("ready" if h["core_ready"] else "MISSING")]
    parts.append("lune %s" % ("ready" if h["luraph_v14_full_ready"] else "missing (v14.x trace-only)"))
    return ", ".join(parts)


def build_report(job: Job, detection: detectors.Detection,
                 outcome: engines.JobOutcome) -> dict:
    """The downloadable JSON report: what it was, what we could do, and proof."""
    fam = detectors.FAMILIES.get(detection.name)
    return {
        "job": {
            "id": job.id, "filename": job.filename, "size": job.size,
            "created": job.created, "finished": job.finished,
            "seconds": outcome.seconds, "status": job.status,
        },
        "detection": detection.as_dict(),
        "detection_evidence": detectors.all_detections(
            open(os.path.join(job.dir, "input.lua"), "rb").read()),
        "capability": {
            "level": detection.level,
            "description": detectors.LEVELS.get(detection.level, ""),
            "engine": detection.engine,
            "notes": fam.notes if fam else "",
        },
        "outcome": {
            "engine": outcome.engine,
            "kind": outcome.kind,
            "kind_description": {
                "devirtualized": "VM bytecode lifted back to Luau source",
                "trace": "behaviour trace reconstructed from a sandboxed run",
                "unpack": "protected payload / VM interpreter extracted, not lifted",
                "none": "no automated recovery",
            }.get(outcome.kind, outcome.kind),
            "result_bytes": outcome.result_bytes,
            "error": outcome.error,
        },
        "steps": outcome.steps,
        "artifacts": [{"name": a["name"], "bytes": a["bytes"]} for a in outcome.artifacts],
        "runtime": outcome.runtime,
        "site": runtime.health().get("site_name", "kers0ne website"),
    }


STORE = JobStore()


def load_existing() -> None:
    """Re-attach to job directories left over from a previous run."""
    root = STORE.root
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        jobdir = os.path.join(root, name)
        meta = os.path.join(jobdir, "job.json")
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        status = data.get("status", STATUS_ERROR)
        if status in (STATUS_QUEUED, STATUS_RUNNING):
            status = STATUS_ERROR   # the process that ran it is gone
            data["error"] = data.get("error") or "server restarted while this job was running"
        job = Job(id=data.get("id", name), dir=jobdir,
                  filename=data.get("filename", "input.lua"),
                  size=int(data.get("size") or 0), options=data.get("options") or {},
                  status=status, created=float(data.get("created") or time.time()),
                  started=data.get("started"), finished=data.get("finished"),
                  detection=data.get("detection"), error=data.get("error"))
        events_path = os.path.join(jobdir, "events.jsonl")
        if os.path.isfile(events_path):
            try:
                with open(events_path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                job.events.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                job.seq = job.events[-1]["seq"] if job.events else 0
            except OSError:
                pass
        result = os.path.join(jobdir, "result.lua")
        if os.path.isfile(result):
            job.outcome = {"kind": "unknown", "engine": data.get("engine", ""),
                           "result": {"name": "result.lua", "bytes": os.path.getsize(result)},
                           "artifacts": [], "steps": []}
        with STORE._lock:  # noqa: SLF001 - internal bootstrap
            STORE._jobs[job.id] = job  # noqa: SLF001
