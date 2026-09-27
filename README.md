# kers0ne deobfuscator

A web service that takes a protected Roblox Luau script and gives back what can
be recovered from it: **devirtualized source** where an engine can lift the VM
bytecode, a **sandbox behaviour trace** where it cannot, and a report that says
plainly which of the two you got.

Upload a file or paste source → the obfuscator is identified → the right engine
runs in a sandbox → results stream back live, with every intermediate artifact
downloadable.

```
protected script
  → fingerprint all 15 known families          (server/detectors.py, no execution)
  → pick the engine that can actually help      (server/engines.py)
  → run it as a child process, own process group, hard timeout
  → stream NDJSON events to the browser over SSE
  → result + report.json + artifacts
```

---

## What it recovers, honestly

No deobfuscator handles everything, and the families differ in kind, not just in
difficulty. This is measured, not claimed — `tests/smoke.py --corpus` reproduces
the table against a labelled sample corpus.

| Obfuscator | Recovery | What you get back |
|---|---|---|
| **Luraph v15** | ✅ devirtualize | Real Luau: control flow, locals, closures, and branches that never ran |
| **Luraph v14.7 / 14.8 / 14.9** | ✅ devirtualize *(needs Lune)* | Static unpack always works; full lift needs the Lune runtime |
| **IronBrew 1** | ✅ devirtualize | Real Luau when the harness completes; trace when the sample errors inside it |
| Luraph ≤ v14.6 | ⚠️ trace | Behaviour trace |
| IronBrew 2, IronBrew 3, MoonVeil, MoonSec, PSU, Prometheus, 77fuscator, Boronide, Hercules, LuaObfuscator, Synapse Xen, wYnFuscate, LPS | ⚠️ trace | Behaviour trace |
| anything unrecognized | ⚠️ trace | Behaviour trace |
| not obfuscated | — | your script back, unchanged |

**What "trace" means.** The script runs in a real Luau VM against a fake
Roblox/executor environment and everything it does is recorded and rendered back
as Luau. That is not the original source and it is not a VM lift: every branch
the sandbox never reached is missing, and conditions survive only as comments.
It is still frequently the useful answer — on a Luraph v14.7 loader the trace
reported the `game:HttpGet` URL it fetched and the `loadstring` payloads it
built, which is exactly what you want when you are trying to work out what a
script in your game is doing.

**What "devirtualize" means.** The VM's opcode semantics are recovered from the
interpreter embedded in the same file, then every prototype is lifted back to
Luau. Original local names, comments and the exact source-level spelling of
control flow are *not* recoverable — they are not in the bytecode. Names are
inferred from use, and the result says so in its header.

Result files are stamped:

```lua
-- Deobfuscated by kers0ne website
-- Detected obfuscation: Luraph v15
-- Local names are inferred from use (the originals are not in the bytecode)
```

A trace says `Deobfuscated by kers0ne website (dynamic trace)` and carries the
warning about missing branches. Change the name with `KERS_SITE_NAME`.

---

## Quick start

### Render

[`render.yaml`](render.yaml) is a complete Blueprint — Render reads it and
creates the service, builds the image, and assigns a public HTTPS domain with a
certificate, with nothing to configure:

1. Push this repo to GitHub.
2. Render dashboard → **New +** → **Blueprint** → pick the repo → **Apply**.
3. The site is live at `https://kers0ne-deobfuscator.onrender.com`.

Deploys are automatic on every push to the default branch. To serve from your
own domain, uncomment the `domains:` block in `render.yaml` and add
`CNAME deobf.example.com → kers0ne-deobfuscator.onrender.com`; Render provisions
the certificate itself. The `onrender.com` hostname keeps working either way.

**Read this before choosing a plan.** The blueprint pins `plan: free`
(0.1 CPU / 512 MB, $0) so a first deploy costs nothing. Render names plans by
size and *rejects* the dashboard's marketing names — `plan: starter` fails the
blueprint sync — and omitting `plan` silently defaults to the $7/mo tier, which
is why it is set explicitly.

| Plan | Compute | Cost | Verdict for this app |
|---|---|---|---|
| `free` | 0.1 CPU / 512 MB | $0 | Works, but slow: a v15 devirtualization that takes ~20 s on a laptop can take minutes. Spins down after 15 min idle (~1 min cold start). 750 instance-hours and 500 build minutes per month. |
| `0.5c-512mb` | 0.5 CPU / 512 MB | $7 | Buys CPU but not RAM. 512 MB is the binding constraint here. |
| `1c-2g` | 1 CPU / 2 GB | $25 | **What this workload actually wants.** |
| `2c-4g` | 2 CPU / 4 GB | $85 | Comfortable headroom for large samples and 2 workers. |

On `free`, the blueprint already widens the analysis timeouts and shrinks the
upload cap and worker count to fit. If you upgrade, raise `KERS_WORKERS`,
`KERS_MAX_INPUT_BYTES` and drop the timeouts back toward their defaults.

Two free-tier limits shape the design, both reflected in `render.yaml`:

- **Single instance only, and that is also a correctness requirement.** Job
  state lives in process memory and on the container's local disk, so with two
  instances a job created on one cannot be fetched from the other and the SSE
  log stream lands on whichever answers. `numInstances: 1` is pinned and
  autoscaling is left off. See *Scaling* below.
- **No persistent disk.** The filesystem is wiped on every deploy and restart,
  so retention drops to one hour and the disk-pressure guard
  (`KERS_MIN_FREE_MB`) prunes oldest-first and then returns **507** rather than
  letting a full volume break the container. `GET /api/health` reports
  `disk_free_mb` and `accepting_jobs` so this is diagnosable from outside.

The image compiles Luau on the first build (several minutes); BuildKit caches
that layer, so later deploys reuse it unless `tools/build_luau.py` changes.
Unlike a sandboxed build environment, Render can reach GitHub releases, so
`tools/install_lune.sh` succeeds at build time and **Luraph v14.7–14.9 get full
devirtualization**, not just unpack-and-trace.

### Docker

```bash
docker compose up --build      # http://localhost:8000
```

The image compiles the patched Luau in a build stage and tries to fetch Lune;
if Lune cannot be downloaded the build still succeeds and Luraph v14.x runs
unpack + trace instead of the full lift.

### Plain Python

```bash
bash setup.sh                  # venv + deps + builds luau/luau-ast (+ Lune)
. .venv/bin/activate
python run.py                  # http://0.0.0.0:8000
```

`setup.sh` needs `git`, `cmake` and a C++ compiler to build Luau. If your system
lacks cmake it will `pip install cmake ninja` for you.

### Runtimes

| Binary | Needed for | Get it with |
|---|---|---|
| `luau` + `luau-ast` | **every job** — the sandbox VM and the AST lifter | `python tools/build_luau.py` |
| `lune` | Luraph v14.x **full** devirtualization only | `bash tools/install_lune.sh` |

`GET /api/health` reports what this install has, and the UI shows it in the
header pill and the Runtime panel — so a deployment missing Lune says so instead
of quietly returning weaker results.

Luau is built from source with one patch: the vector type's metatable is left
writable instead of frozen, so the sandbox can install Roblox's `Vector3`
members (`v:Dot(w)`, `v.Magnitude`). Stock Luau rejects those, and protected
scripts use them constantly. See `tools/build_luau.py`.

Binaries are not committed; `engine/cadmio/deobf/bin/` and `bin/` are gitignored.

---

## Using it

Open the site, drop a `.lua`/`.luau` file in (or paste), press **Deobfuscate**.
The log pane streams what the pipeline is doing; the result pane fills in when
it finishes. Download the result, copy it, or pull individual artifacts
(`vm.lua`, `bytecode.bin`, `result.protos.json`, `result.devirt.luau`, …).

Options:

- **Devirtualize** — off means "trace only", which is much faster
- **Harness timeout** — hard limit per sandbox run
- **Script budget** — soft time budget handed to the traced script

### API

```bash
curl -F "file=@protected.lua" http://localhost:8000/api/jobs
# {"id":"20260927-ab12cd34ef56","detection":{"label":"Luraph v15",...},...}

curl -N http://localhost:8000/api/jobs/20260927-ab12cd34ef56/events   # SSE log
curl    http://localhost:8000/api/jobs/20260927-ab12cd34ef56/result.txt
curl    http://localhost:8000/api/jobs/20260927-ab12cd34ef56/report
```

| Route | |
|---|---|
| `GET /api/health` | which native runtimes are present |
| `GET /api/capabilities` | support matrix, per-install availability, limits |
| `POST /api/jobs` | `file=` upload or `source=` paste; `devirt`, `timeout`, `budget` |
| `GET /api/jobs` | recent jobs |
| `GET /api/jobs/{id}` | status + outcome |
| `GET /api/jobs/{id}/events` | SSE; `?since=N` resumes after a dropped connection |
| `GET /api/jobs/{id}/log` | the whole event log as JSON, for polling clients |
| `GET /api/jobs/{id}/result` · `/result.txt` | the recovered source |
| `GET /api/jobs/{id}/report` | full JSON report incl. per-family detection evidence |
| `GET /api/jobs/{id}/artifacts/{name}` | one intermediate file |
| `POST /api/jobs/{id}/cancel` · `DELETE /api/jobs/{id}` | stop / discard |

Interactive docs at `/api/docs`.

### Configuration

| Env | Default | |
|---|---|---|
| `KERS_HOST` / `PORT` | `0.0.0.0` / `8000` | bind address |
| `KERS_SITE_NAME` | `kers0ne website` | name stamped into results and shown in the UI |
| `KERS_SITE_URL` | *(empty)* | optional second header line |
| `KERS_WORKERS` | `2` | concurrent jobs |
| `KERS_MAX_QUEUE` | `64` | queued jobs before new ones are refused |
| `KERS_MAX_INPUT_BYTES` | 32 MiB | upload cap |
| `KERS_RETENTION_SECONDS` | `21600` | how long finished jobs are kept |
| `KERS_KEEP_FINISHED` | `50` | finished jobs kept regardless of age |
| `KERS_JOB_DIR` | `var/jobs` | job storage |
| `KERS_MIN_FREE_MB` | `256` | disk headroom; below it, prune then return 507. Clamped to 25% of the volume so a small host is never refused outright. `0` disables |
| `KERS_DEFAULT_TIMEOUT` | `240` | per-run harness limit when the job does not set one |
| `KERS_DEFAULT_BUDGET` | `30` | soft time budget handed to the traced script |
| `KERS_DEFAULT_DEVIRT_TIMEOUT` | `600` | limit for the Luraph v14.x lift stage |
| `KERS_PROXY_HEADERS` | `1` | trust `X-Forwarded-For`/`Proto` from the platform proxy |
| `LUAU_PATH` / `LUAU_AST_PATH` / `LUNE_PATH` | *(searched)* | explicit binary locations |

Raise the three `KERS_DEFAULT_*` limits on a slow host rather than letting jobs
abort: they are wall-clock budgets for the analysis, and a 0.1-CPU instance needs
several times the time a laptop does to walk the same VM.

## Scaling

This service is deliberately single-instance. Job state is a dict in the server
process plus a directory on the container's filesystem, and the SSE log stream
reads from both — so a second instance cannot see the first instance's jobs, and
a reconnecting browser may land on either.

That is the right shape for one box and the wrong shape for a fleet. To scale
horizontally you would need to move both halves out of process:

- **job metadata and event log** → Redis or Postgres (Render Key Value /
  Postgres). The SSE endpoint becomes a subscription instead of a poll loop.
- **job files** → object storage, or a persistent disk shared by one worker
  service, with the web tier stateless.

Then `numInstances` / `scaling` in `render.yaml` can go up. Note that a Render
persistent disk attaches to exactly one instance, so it removes the filesystem
problem without by itself allowing scale-out. Until then, the honest lever is a
bigger single instance plus `KERS_WORKERS`.

---

## Tests

```bash
pip install pytest
pytest -q                                   # units: detection, branding, planning
python tests/smoke.py                       # end-to-end against a running server

# measured support matrix over a labelled corpus (nothing is committed from it)
SAMPLES_DIR=/path/to/obfuscator-samples python tests/smoke.py --corpus --corpus-only
```

---

## Layout

```
run.py                  entrypoint: reports runtime state, then serves
render.yaml             Render Blueprint: service, plan, domain, env sizing
.dockerignore           keeps local job state and non-portable binaries out of images
server/
  app.py                FastAPI routes + static front end
  jobs.py               job store, worker pool, SSE event log, retention
  engines.py            which pipeline runs for which family, process-group isolation
  detectors.py          fingerprints for all 15 families + plaintext check
  branding.py           the site's output header, bound at runtime
  runtime.py            luau / luau-ast / lune provisioning and health
  worker_cadmio.py      subprocess driver: Cadmio pipeline (Apache-2.0)
  worker_luauvmp.py     subprocess driver: luau-vmp-deobf (MIT)
engine/
  cadmio/               vendored, Apache-2.0 — dynamic trace + v15/IB1 devirtualizer
  luauvmp/              vendored, MIT — Luraph v14.x staged devirtualizer
tools/
  build_luau.py         builds the patched luau + luau-ast from source
  install_lune.sh       fetches the pinned Lune release
web/                    front end: no build step, no CDN, no framework
tests/
```

---

## Security

Every job analyses **hostile input on purpose**. The design assumes the sample
is trying to escape:

- The analysis runs in a **child process in its own process group**, so a
  timeout kills the engine *and* the `luau`/`lune` grandchildren it spawned.
- The traced script runs inside the Luau VM against a **fake** Roblox/executor
  environment: no network, no filesystem, no `require`, no executor APIs. Calls
  like `game:HttpGet` are *recorded*, not performed.
- The Luraph v14.x capture stage runs in a restricted Lune environment and the
  recovered payload closure is **never called**.
- Uploads are size-capped, the queue is depth-capped, workers are few, and
  finished jobs are deleted after the retention window.
- The Docker image runs as a non-root user with `cap_drop: ALL` and
  `no-new-privileges`, under a memory and CPU ceiling.
- The optional `lua.expert` post-pass is disabled: it would upload compiled
  bytecode to a third party.

Even so, run this on a machine you are prepared to have touch untrusted code,
and keep it off any network that reaches things you care about.

## Legal

Only analyse scripts you own or have permission to inspect. The legitimate uses
are real — finding the backdoor in a free model, understanding what a script is
doing in your own game, recovering your own lost source — and this is built for
those. It is not for taking someone else's protected script and redistributing
it.

The engines here are other people's work, vendored under Apache-2.0 and MIT with
their licenses intact. See [CREDITS.md](CREDITS.md) for exactly what is used,
what was changed, and who to thank.
