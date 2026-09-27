from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
ENGINE_ROOT = ROOT / "deobf_engine"
CLI = ENGINE_ROOT / "deobf" / "deob.py"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
CLI_TIMEOUT = 180
BUDGET_SECONDS = 35
WORKERS = threading.BoundedSemaphore(2)

HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b0e14">
<title>Kers0ne Deobfuscator Website</title>
<style>
:root{color-scheme:dark;--bg:#0b0e14;--panel:#111722;--panel2:#151e2b;--line:#273446;--text:#eef4fb;--muted:#94a5b8;--cyan:#59e1d2;--blue:#83a7ff;--warn:#f3c46f;--red:#ff8787}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1000px 540px at 85% -10%,#1a3550 0%,transparent 60%),radial-gradient(900px 520px at -10% 10%,#123032 0%,transparent 60%),var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
.shell{width:min(1100px,calc(100% - 36px));margin:0 auto;padding:35px 0 54px}.top{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:54px}.brand{display:flex;gap:12px;align-items:center;font-weight:750;letter-spacing:.01em}.mark{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#65efd9,#7897ff);display:grid;place-items:center;color:#071017;font-weight:900;box-shadow:0 6px 28px #4ae5d144}.pill{border:1px solid #35504f;color:#a4e9dd;background:#112321;padding:6px 11px;border-radius:100px;font-size:12px;letter-spacing:.04em}
.hero{max-width:790px;margin-bottom:28px}.eyebrow{color:var(--cyan);font-weight:750;text-transform:uppercase;letter-spacing:.16em;font-size:11px}.hero h1{font-size:clamp(36px,6vw,65px);line-height:1.02;letter-spacing:-.055em;margin:13px 0 16px}.hero h1 span{background:linear-gradient(95deg,#e9f4ff 12%,#80efe0 76%,#9bb5ff);color:transparent;background-clip:text}.hero p{color:var(--muted);font-size:17px;max-width:710px;margin:0}
.card{background:linear-gradient(145deg,#151e2bd9,#101620f2);border:1px solid var(--line);border-radius:20px;box-shadow:0 22px 80px #0005;overflow:hidden}.cardhead{display:flex;justify-content:space-between;align-items:center;padding:20px 23px;border-bottom:1px solid var(--line);gap:14px}.cardhead h2{font-size:15px;margin:0}.badge{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:#acd8d3;border:1px solid #30454a;border-radius:8px;padding:5px 8px}.body{padding:22px 23px 24px}.drop{border:1px dashed #496175;background:#0d141d;border-radius:15px;min-height:128px;display:flex;align-items:center;justify-content:center;text-align:center;padding:18px;transition:.18s}.drop.hover{border-color:var(--cyan);background:#102222}.drop strong{display:block;font-size:15px;margin-bottom:4px}.drop small{color:var(--muted)}.filelabel{display:inline-flex;margin-top:10px;cursor:pointer;color:#071017;background:var(--cyan);font-weight:750;padding:8px 14px;border-radius:9px}.filelabel:hover,.primary:hover{filter:brightness(1.06);transform:translateY(-1px)}input[type=file]{display:none}.filename{margin-top:10px;color:#a9c4d5;font-size:12px;min-height:18px}
.or{display:flex;gap:13px;align-items:center;color:#708397;font-size:12px;margin:18px 0}.or:before,.or:after{content:"";height:1px;background:var(--line);flex:1}textarea{width:100%;min-height:210px;resize:vertical;border:1px solid #28384a;background:#0b1119;color:#e6f2fb;border-radius:13px;padding:15px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;outline:none}textarea:focus{border-color:#51cbbb;box-shadow:0 0 0 3px #42d7c41c}textarea::placeholder{color:#596c7e}.controls{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:16px;flex-wrap:wrap}.version{color:var(--muted);font-size:13px}.version strong{color:#d9e7f2;font-weight:600}.primary{cursor:pointer;border:0;border-radius:11px;padding:12px 18px;background:linear-gradient(100deg,#60e2d2,#87a7ff);color:#09131a;font-size:14px;font-weight:800;box-shadow:0 8px 28px #4ee0d122;transition:.15s}.primary:disabled{opacity:.55;cursor:wait;transform:none}.result{display:none;margin-top:22px}.result.show{display:block}.resulthead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.status{font-weight:750}.status.partial{color:var(--warn)}.status.ok{color:var(--cyan)}.actions{display:flex;gap:8px}.secondary{border:1px solid #37495d;background:#141e2a;color:#d9e6f1;border-radius:8px;padding:7px 11px;cursor:pointer}.secondary:hover{border-color:#70d8ca}.out{white-space:pre;overflow:auto;max-height:520px;background:#080d13;border:1px solid #253347;border-radius:12px;padding:16px;color:#c9d8e8;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.note{color:#a6b5c5;font-size:13px;padding:12px 14px;border-left:2px solid var(--warn);background:#201c142e;border-radius:0 8px 8px 0;margin-top:13px}.error{display:none;margin-top:14px;color:#ffd1d1;background:#331b20;border:1px solid #67343a;border-radius:10px;padding:12px 14px}.error.show{display:block}.foot{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px}.fact{border:1px solid #263342;border-radius:13px;padding:15px;background:#1017219c}.fact b{display:block;font-size:13px;margin-bottom:4px}.fact span{color:var(--muted);font-size:12px}.disclaimer{color:#708295;font-size:12px;margin:23px 2px 0;max-width:850px}.disclaimer strong{color:#aebdcb}@media(max-width:650px){.shell{width:min(100% - 22px,1100px);padding-top:20px}.top{margin-bottom:36px}.cardhead,.body{padding-left:16px;padding-right:16px}.foot{grid-template-columns:1fr}.controls{align-items:stretch}.primary{width:100%}.hero p{font-size:15px}}
</style>
</head>
<body>
<div class="shell">
<header class="top"><div class="brand"><div class="mark">K</div><span>Kers0ne Deobfuscator Website</span></div><div class="pill">LURAPH · LUAU</div></header>
<section class="hero"><div class="eyebrow">Script analysis workspace</div><h1><span>Luraph, made readable.</span></h1><p>Submit a Lua/Luau script to try the available Luraph v14/v15 analysis pipeline. Review the output, then copy or download it.</p></section>
<main class="card"><div class="cardhead"><h2>Analyze a protected script</h2><div class="badge">LOCAL PROCESSING</div></div><div class="body">
<div class="drop" id="drop"><div><strong>Drop a .lua, .luau, or .txt file here</strong><small>Maximum input: 8 MB · file content is processed by this server</small><br><label class="filelabel" for="file">Choose file</label><input id="file" type="file" accept=".lua,.luau,.txt,text/plain"><div class="filename" id="filename"></div></div></div>
<div class="or">OR PASTE SOURCE</div><textarea id="source" spellcheck="false" placeholder="-- Paste Luraph-protected Lua/Luau source here"></textarea>
<div class="controls"><div class="version">Pipeline: <strong>Auto-select v14 / v15</strong> · execution budget is bounded</div><button id="run" class="primary">Analyze Luraph script <span aria-hidden="true">→</span></button></div>
<div id="error" class="error" role="alert"></div>
<section id="result" class="result"><div class="resulthead"><div class="status" id="status"></div><div class="actions"><button class="secondary" id="copy">Copy</button><button class="secondary" id="download">Download .lua</button></div></div><pre class="out" id="output"></pre><div class="note" id="note"></div></section>
</div></main>
<section class="foot"><div class="fact"><b>v14 + v15 selection</b><span>Version markers choose the matching registered plugin; unknown headers default to v15.</span></div><div class="fact"><b>Bounded runs</b><span>Limited input size, execution budget, wall time, and concurrent analyses.</span></div><div class="fact"><b>Download your result</b><span>Output is returned to this browser session; this demo does not store a job history.</span></div></section>
<p class="disclaimer"><strong>Recovery is best-effort, not universal.</strong> Runtime traces contain only executed behavior, so inactive branches and original structure may be missing. Script errors and trace fallbacks are labeled partial. No website can guarantee full recovery of every Luraph sample.</p>
</div>
<script>
const fileInput=document.getElementById('file'),source=document.getElementById('source'),drop=document.getElementById('drop'),run=document.getElementById('run'),err=document.getElementById('error'),result=document.getElementById('result'),out=document.getElementById('output'),statusEl=document.getElementById('status'),note=document.getElementById('note'),filename=document.getElementById('filename');
let chosenFile=null,currentOutput='';
function setFile(f){if(!f)return;if(!/\.(lua|luau|txt)$/i.test(f.name)){showError('Choose a .lua, .luau, or .txt source file.');return}if(f.size>8*1024*1024){showError('That file exceeds the 8 MB input limit.');return}chosenFile=f;filename.textContent=f.name+' · '+(f.size/1024).toFixed(1)+' KB';err.classList.remove('show')}
function showError(s){err.textContent=s;err.classList.add('show');result.classList.remove('show')}
fileInput.addEventListener('change',()=>setFile(fileInput.files[0]));
for(const ev of ['dragenter','dragover'])drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hover')});for(const ev of ['dragleave','drop'])drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hover')});drop.addEventListener('drop',e=>setFile(e.dataTransfer.files[0]));
run.addEventListener('click',async()=>{err.classList.remove('show');result.classList.remove('show');let text=source.value;if(chosenFile){try{text=await chosenFile.text()}catch{showError('Could not read that file in your browser.');return}}if(!text.trim()){showError('Choose a source file or paste Lua/Luau source first.');return}if(new TextEncoder().encode(text).length>8*1024*1024){showError('That source exceeds the 8 MB input limit.');return}run.disabled=true;run.textContent='Analyzing…';const started=Date.now();try{const r=await fetch('/api/deobfuscate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:text})});const data=await r.json();if(!r.ok||!data.ok)throw new Error(data.error||'Analysis failed.');currentOutput=data.output;out.textContent=currentOutput;statusEl.textContent=data.partial?'Partial trace · '+data.engine:'Pipeline output · '+data.engine;statusEl.className='status '+(data.partial?'partial':'ok');note.textContent=data.note||'Best-effort output; inspect it before relying on it. A completed pipeline run does not prove every path was recovered.';result.classList.add('show');result.scrollIntoView({behavior:'smooth',block:'nearest'})}catch(e){showError(e.message||'The analysis server could not process this script.')}finally{run.disabled=false;run.innerHTML='Analyze Luraph script <span aria-hidden="true">→</span>'}});
document.getElementById('copy').addEventListener('click',async()=>{if(!currentOutput)return;try{await navigator.clipboard.writeText(currentOutput);document.getElementById('copy').textContent='Copied'}catch{showError('Clipboard access was denied. You can select the output text and copy it manually.')}});
document.getElementById('download').addEventListener('click',()=>{if(!currentOutput)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([currentOutput],{type:'text/plain;charset=utf-8'}));a.download=(chosenFile?.name||'luraph_output.lua').replace(/\.(lua|luau|txt)$/i,'')+'.deobfuscated.lua';a.click();URL.revokeObjectURL(a.href)});
</script>
</body>
</html>'''


def ensure_engine_tools() -> None:
    for path in (
        ENGINE_ROOT / "deobf" / "bin" / "luau-ast",
        ENGINE_ROOT / "deobf" / "bin" / "luau-ast-linux-x86_64",
        ENGINE_ROOT / "deobf" / "bin" / "luau",
    ):
        if path.is_file() and os.name != "nt":
            try:
                path.chmod(path.stat().st_mode | 0o111)
            except OSError:
                pass


def safe_paths(text: str) -> str:
    return re.sub(
        r'''(?<![A-Za-z0-9])/(?:tmp|app|home|usr|var|opt|root)/[^\s"'`]+''',
        "[internal path hidden]",
        text,
    )


def detect_version(source: str) -> tuple[str, str]:
    header = source[:5000]
    match = re.search(r"Luraph\s+Obfuscator\s+v(\d+)", header, re.I)
    is_v14 = bool((match and match.group(1) == "14") or re.search(r"\bv14(?:\.\d+)?\b", header, re.I))
    return ("luraph_v14", "Luraph v14") if is_v14 else ("luraph_v15", "Luraph v15 (default)")


def run_cli(source: str) -> tuple[str, str, bool, str]:
    if not CLI.is_file():
        raise RuntimeError("The bundled analysis engine is missing.")
    plugin, label = detect_version(source)
    with tempfile.TemporaryDirectory(prefix="kers0ne_luraph_") as folder:
        work = pathlib.Path(folder)
        input_path = work / "input.lua"
        output_path = work / "output.lua"
        input_path.write_text(source, encoding="utf-8")
        cmd = [
            sys.executable, str(CLI), str(input_path), "-o", str(output_path),
            "--obfuscator", plugin, "--timeout", str(CLI_TIMEOUT), "--budget", str(BUDGET_SECONDS),
        ]
        proc = subprocess.run(
            cmd, cwd=str(ENGINE_ROOT), capture_output=True, text=True,
            timeout=CLI_TIMEOUT + 20, check=False,
        )
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        fallback = False
        if proc.returncode != 0 and not output_path.is_file() and "luau-ast" in log.lower():
            fallback_cmd = cmd + ["--no-hooks", "--no-devirt", "--no-tidy"]
            proc = subprocess.run(
                fallback_cmd, cwd=str(ENGINE_ROOT), capture_output=True, text=True,
                timeout=CLI_TIMEOUT + 20, check=False,
            )
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
            fallback = True
        if not output_path.is_file() or output_path.stat().st_size == 0:
            if "timeout" in log.lower():
                raise TimeoutError("Analysis exceeded the server time limit. Try a smaller input.")
            raise RuntimeError("The Luraph pipeline did not produce output. This sample may need a different or newer lifter.")
        if output_path.stat().st_size > MAX_OUTPUT_BYTES:
            raise RuntimeError("The generated output exceeded the 8 MB result limit.")
        output = output_path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")

    output = safe_paths(output)
    partial = fallback or bool(re.search(
        r"(?i)(runtime behavior trace|run status:\s*script error|Luraph v14 constants extraction)", output[:8000)
    ))
    if partial:
        if "Reconstructed by Kers0ne Deobfuscator Website" in output[:600]:
            output = output.replace("Reconstructed by Kers0ne Deobfuscator Website", "Partial trace by Kers0ne Deobfuscator Website", 1)
        elif "Partial trace by Kers0ne Deobfuscator Website" not in output[:600]:
            output = "-- Partial trace by Kers0ne Deobfuscator Website\n" + output
        note = "This result is a trace, partial dump, or fallback. It may omit unexecuted branches and is not complete source recovery."
    else:
        output = re.sub(r"(?m)^--\s*Reconstructed by 199ms\s*$", "-- Reconstructed by Kers0ne Deobfuscator Website", output, count=1)
        if "Reconstructed by Kers0ne Deobfuscator Website" not in output[:600]:
            output = "-- Reconstructed by Kers0ne Deobfuscator Website\n" + output
        note = "The selected pipeline completed. This is best-effort output, not a guarantee that every branch or original detail was recovered."
    return output, label, partial, note


class Handler(BaseHTTPRequestHandler):
    server_version = "Kers0neLuraph/1.0"

    def send_bytes(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, code: int, value: dict) -> None:
        self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_bytes(404, b"Not found", "text/plain; charset=utf-8")
            return
        self.send_bytes(200, HTML.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/api/deobfuscate":
            self.send_json(404, {"ok": False, "error": "Not found."})
            return
        if not WORKERS.acquire(blocking=False):
            self.send_json(429, {"ok": False, "error": "The analysis queue is full. Try again in a moment."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_INPUT_BYTES * 2 + 1024:
                self.send_json(413, {"ok": False, "error": "Request size is empty or exceeds the input limit."})
                return
            try:
                data = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"ok": False, "error": "Request must contain valid JSON."})
                return
            source = data.get("source") if isinstance(data, dict) else None
            if not isinstance(source, str) or not source.strip():
                self.send_json(400, {"ok": False, "error": "Paste a Lua/Luau script or choose a file."})
                return
            if len(source.encode("utf-8", "replace")) > MAX_INPUT_BYTES:
                self.send_json(413, {"ok": False, "error": "Input exceeds the 8 MB limit."})
                return
            try:
                output, engine, partial, note = run_cli(source)
                payload = {"ok": True, "output": output, "engine": engine, "partial": partial, "note": note}
                self.send_json(200, payload)
            except TimeoutError as exc:
                self.send_json(504, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                self.send_json(422, {"ok": False, "error": safe_paths(str(exc))})
            except subprocess.TimeoutExpired:
                self.send_json(504, {"ok": False, "error": "Analysis exceeded the wall-clock limit. Try a smaller input."})
            except Exception:
                self.send_json(500, {"ok": False, "error": "The analysis server encountered an internal error. Server details are hidden."})
        finally:
            WORKERS.release()

    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging user source or server filesystem details.
        print("[web] " + (fmt % args), flush=True)


def main() -> None:
    ensure_engine_tools()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Kers0ne Deobfuscator Website running on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
