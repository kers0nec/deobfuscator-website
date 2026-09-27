"""HTTP API and static front end.

Run it with ``python run.py`` (or ``uvicorn server.app:app``).

Routes
------
``GET  /``                         the front end
``GET  /api/health``               which native runtimes are present
``GET  /api/capabilities``         what each obfuscator family gets
``POST /api/jobs``                 upload a file or paste source -> job id
``GET  /api/jobs``                 recent jobs
``GET  /api/jobs/{id}``            job summary
``GET  /api/jobs/{id}/events``     SSE log stream (``?since=N`` to resume)
``GET  /api/jobs/{id}/result``     the recovered source
``GET  /api/jobs/{id}/report``     the full JSON report
``GET  /api/jobs/{id}/artifacts/{name}``  an intermediate file
``POST /api/jobs/{id}/cancel``     stop a running job
``DELETE /api/jobs/{id}``          drop a job and its files

The analysis itself never happens in this process: ``server.jobs`` hands it to a
worker that spawns the engines as children (see ``server.engines``).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import detectors, jobs, runtime

WEB_DIR = os.path.join(runtime.REPO_ROOT, "web")
ACCEPTED_SUFFIXES = (".lua", ".luau", ".txt", ".luac", ".bin", "")

app = FastAPI(
    title="Luau Deobfuscator",
    description="Deobfuscate protected Roblox Luau scripts: devirtualize what we can lift, "
                "sandbox-trace the rest.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.on_event("startup")
def _startup() -> None:
    jobs.load_existing()
    jobs.STORE.start()
    state = runtime.health()
    if not state["core_ready"]:
        # The site still starts: /api/health explains what is missing and the UI
        # shows it, which beats refusing to boot on a fresh checkout.
        print("[!] luau/luau-ast not found - run `python tools/build_luau.py`")
    if not state["luraph_v14_full_ready"]:
        print("[i] Lune not found - Luraph v14.x runs unpack + trace only "
              "(run `bash tools/install_lune.sh` for full devirtualization)")


@app.on_event("shutdown")
def _shutdown() -> None:
    jobs.STORE.stop()


# --------------------------------------------------------------------- meta

@app.get("/api/health")
def api_health() -> dict:
    state = runtime.health()
    # Hosted on an ephemeral volume (Render, Fly, ...) the disk is the thing
    # that quietly stops the service accepting work, so publish it: a 507 from
    # POST /api/jobs should be explainable without shell access.
    avail = jobs.free_mb(jobs.STORE.root)
    need = jobs.headroom_mb(jobs.STORE.root)
    state["disk_free_mb"] = None if avail is None else round(avail, 1)
    state["disk_headroom_mb"] = round(need, 1)
    state["accepting_jobs"] = avail is None or not need or avail >= need
    return state


@app.get("/api/capabilities")
def api_capabilities() -> dict:
    """The support matrix, plus what this particular install can actually do."""
    state = runtime.health()
    matrix = detectors.capability_matrix()
    for row in matrix:
        if row["name"] == "luraph_v14":
            row["available_here"] = state["luraph_v14_full_ready"]
            row["partial_here"] = state["luraph_v14_unpack_ready"]
            if not state["luraph_v14_full_ready"]:
                row["notes"] = ((row["notes"] + " ") if row["notes"] else "") + (
                    "On this server the Lune runtime is missing, so v14.x jobs get the static "
                    "unpack plus a behaviour trace instead of lifted source."
                    if state["luraph_v14_unpack_ready"] else
                    "On this server the v14.x dependencies are missing entirely.")
        elif row["level"] in ("devirtualize", "trace"):
            row["available_here"] = state["core_ready"]
        else:
            row["available_here"] = True
    return {
        "families": matrix,
        "levels": detectors.LEVELS,
        "runtime": state,
        "limits": {
            "max_input_bytes": jobs.MAX_INPUT_BYTES,
            "max_queue": jobs.MAX_QUEUE,
            "workers": jobs.WORKERS,
            "retention_seconds": jobs.RETENTION_SECONDS,
        },
    }


# --------------------------------------------------------------------- jobs

def _read_upload_name(name: Optional[str]) -> str:
    base = os.path.basename((name or "input.lua").replace("\x00", "")) or "input.lua"
    return base[:180]


@app.post("/api/jobs")
async def api_create_job(request: Request,
                         file: Optional[UploadFile] = File(default=None),
                         source: Optional[str] = Form(default=None),
                         filename: Optional[str] = Form(default=None),
                         devirt: Optional[str] = Form(default=None),
                         timeout: Optional[str] = Form(default=None),
                         budget: Optional[str] = Form(default=None)) -> dict:
    """Start a job from an uploaded file or pasted source."""
    if file is not None:
        data = await file.read()
        name = _read_upload_name(file.filename or filename)
    elif source is not None:
        data = source.encode("utf-8", errors="surrogateescape")
        name = _read_upload_name(filename)
    else:
        # allow a raw JSON/other content-type body too
        data = await request.body()
        name = _read_upload_name(filename)
        if not data:
            raise HTTPException(status_code=400, detail="send a file upload, a `source` field, "
                                                        "or a request body")

    if len(data) > jobs.MAX_INPUT_BYTES:
        raise HTTPException(status_code=413,
                            detail="input is %d bytes; the limit is %d bytes (%d MiB)"
                                   % (len(data), jobs.MAX_INPUT_BYTES,
                                      jobs.MAX_INPUT_BYTES // (1024 * 1024)))
    if not data.strip():
        raise HTTPException(status_code=400, detail="input is empty")

    options: dict = {}
    if devirt is not None:
        options["devirt"] = str(devirt).lower() not in ("0", "false", "no", "off")
    for key, raw in (("timeout", timeout), ("budget", budget)):
        if raw:
            try:
                options[key] = max(5, min(int(float(raw)), 3600))
            except ValueError:
                raise HTTPException(status_code=400, detail="%s must be a number" % key)

    try:
        job = jobs.STORE.create(data, name, options)
    except jobs.StorageFull as exc:
        # 507, not 400: nothing is wrong with the upload, the box is full.
        raise HTTPException(status_code=507, detail=str(exc),
                            headers={"Retry-After": "60"})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    detection = detectors.identify(data)
    return {"id": job.id, "status": job.status, "filename": job.filename,
            "size": job.size, "detection": detection.as_dict(),
            "events": "/api/jobs/%s/events" % job.id,
            "result": "/api/jobs/%s/result" % job.id}


@app.get("/api/jobs")
def api_list_jobs(limit: int = 50) -> dict:
    return {"jobs": jobs.STORE.list(limit=max(1, min(limit, 200)))}


def _need_job(job_id: str) -> jobs.Job:
    job = jobs.STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job (it may have been pruned)")
    return job


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict:
    job = _need_job(job_id)
    data = job.summary()
    data["outcome"] = job.outcome
    data["links"] = {
        "events": "/api/jobs/%s/events" % job.id,
        "result": "/api/jobs/%s/result" % job.id,
        "report": "/api/jobs/%s/report" % job.id,
        "artifacts": ["/api/jobs/%s/artifacts/%s" % (job.id, a["name"])
                      for a in (job.outcome or {}).get("artifacts", [])],
    }
    return data


@app.get("/api/jobs/{job_id}/events")
async def api_events(job_id: str, since: int = 0):
    """Server-sent events. Reconnect with ``?since=<last seq>`` to resume."""
    job = _need_job(job_id)

    async def stream():
        cursor = since
        idle = 0.0
        while True:
            batch = jobs.STORE.events_since(job, cursor)
            for ev in batch:
                cursor = ev["seq"]
                yield "id: %d\nevent: %s\ndata: %s\n\n" % (
                    ev["seq"], ev.get("t", "message"),
                    json.dumps(ev, default=str))
            if job.status in (jobs.STATUS_DONE, jobs.STATUS_ERROR, jobs.STATUS_CANCELLED):
                yield "event: end\ndata: %s\n\n" % json.dumps(
                    {"status": job.status, "seq": cursor,
                     "error": job.error, "outcome": job.outcome}, default=str)
                return
            await asyncio.sleep(0.25)
            idle += 0.25
            if idle >= 15.0:      # keep proxies from closing an idle stream
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.get("/api/jobs/{job_id}/log")
def api_log(job_id: str) -> dict:
    """The whole event log as JSON, for clients that would rather poll."""
    job = _need_job(job_id)
    return {"id": job.id, "status": job.status, "seq": job.seq,
            "events": job.events[-5000:]}


@app.get("/api/jobs/{job_id}/result")
def api_result(job_id: str, download: bool = False):
    job = _need_job(job_id)
    path = os.path.join(job.dir, "result.lua")
    if not os.path.isfile(path):
        if job.status in (jobs.STATUS_QUEUED, jobs.STATUS_RUNNING):
            raise HTTPException(status_code=409, detail="the job has not produced a result yet")
        raise HTTPException(status_code=404,
                            detail=job.error or "this job produced no result")
    name = "deobfuscated.lua" if download else "result.lua"
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=name,
                        headers={"Content-Disposition":
                                 ('attachment; filename="%s"' % name) if download else "inline"})


@app.get("/api/jobs/{job_id}/result.txt", response_class=PlainTextResponse)
def api_result_text(job_id: str) -> str:
    job = _need_job(job_id)
    path = os.path.join(job.dir, "result.lua")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=job.error or "no result")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


@app.get("/api/jobs/{job_id}/report")
def api_report(job_id: str):
    job = _need_job(job_id)
    path = os.path.join(job.dir, "report.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return JSONResponse(json.load(fh))
    # job still running (or from before a restart): rebuild what we have
    if job.detection is None:
        raise HTTPException(status_code=404, detail="no report yet")
    fam = detectors.FAMILIES.get(job.detection.get("name", ""))
    return JSONResponse({
        "job": job.summary(),
        "detection": job.detection,
        "capability": {"level": job.detection.get("level"),
                       "description": detectors.LEVELS.get(job.detection.get("level", ""), ""),
                       "notes": fam.notes if fam else ""},
        "outcome": job.outcome or {},
        "partial": True,
    })


@app.get("/api/jobs/{job_id}/artifacts/{name}")
def api_artifact(job_id: str, name: str):
    job = _need_job(job_id)
    safe = os.path.basename(name)
    if safe != name or name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="bad artifact name")
    path = os.path.join(job.dir, "artifacts", safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such artifact")
    return FileResponse(path, media_type="application/octet-stream", filename=safe)


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> dict:
    job = _need_job(job_id)
    ok = jobs.STORE.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="job is not running")
    return {"id": job.id, "status": "cancelling"}


@app.delete("/api/jobs/{job_id}")
def api_delete(job_id: str) -> dict:
    if not jobs.STORE.delete(job_id):
        raise HTTPException(status_code=404, detail="no such job")
    return {"deleted": job_id}


# ------------------------------------------------------------------- static

if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    import uvicorn
    host = os.environ.get("KERS_HOST", "0.0.0.0")
    # Render (and every other PaaS) injects PORT and terminates TLS at its own
    # proxy, so the container only ever sees plain HTTP from a link-local hop.
    port = int(os.environ.get("PORT", os.environ.get("KERS_PORT", "8000")))
    uvicorn.run(app, host=host, port=port,
                log_level=os.environ.get("KERS_LOG_LEVEL", "info"),
                # Honour X-Forwarded-For/Proto: without this every request looks
                # like it came from the proxy over http, which makes the access
                # log useless for spotting abuse of an endpoint that runs
                # untrusted code. "*" is right here because the container is
                # only reachable through the platform's own proxy.
                proxy_headers=os.environ.get("KERS_PROXY_HEADERS", "1") not in ("0", "false", "no"),
                forwarded_allow_ips=os.environ.get("KERS_FORWARDED_ALLOW_IPS", "*"))


if __name__ == "__main__":
    main()
