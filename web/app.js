/* kers0ne deobfuscator front end.
 *
 * No framework, no build step, no CDN: fetch + EventSource against same-origin
 * relative URLs, so it works behind any proxy or path prefix.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const els = {
    siteName: $("site-name"), footName: $("foot-name"), runtimePill: $("runtime-pill"),
    dropzone: $("dropzone"), file: $("file"), dzMeta: $("dz-meta"), source: $("source"),
    go: $("go"), formError: $("form-error"),
    optDevirt: $("opt-devirt"), optTimeout: $("opt-timeout"), optBudget: $("opt-budget"),
    job: $("job"), jobTitle: $("job-title"), jobSub: $("job-sub"),
    cancel: $("cancel"), copy: $("copy"), download: $("download"), again: $("again"),
    detection: $("detection"), output: $("output"), outputMeta: $("output-meta"),
    log: $("log"), autoscroll: $("autoscroll"), artifacts: $("artifacts"),
    resultTag: $("result-tag"), reportBox: $("report-box"), report: $("report"),
    matrix: $("matrix").querySelector("tbody"), levels: $("levels"), tools: $("tools"),
  };

  const state = { jobId: null, es: null, picked: null, resultText: "", seq: 0 };

  const KIND_LABEL = {
    devirtualized: "devirtualized",
    trace: "behaviour trace",
    unpack: "static unpack",
    none: "no recovery",
  };

  /* ---------------------------------------------------------------- utils */

  const fmtBytes = (n) => {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(2) + " MB";
  };

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  function fail(msg) {
    els.formError.hidden = false;
    els.formError.textContent = msg;
  }
  function clearFail() { els.formError.hidden = true; els.formError.textContent = ""; }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
    if (!res.ok) {
      const detail = data && (data.detail || data.error || data.message);
      throw new Error(detail || res.status + " " + res.statusText);
    }
    return data;
  }

  /* ------------------------------------------------------------ boot data */

  async function loadHealth() {
    try {
      const h = await api("/api/health");
      const name = h.site_name || "kers0ne website";
      els.siteName.textContent = name.split(" ")[0];
      els.footName.textContent = name;
      document.title = name + " — Luau deobfuscator";

      const luau = (h.tools || []).find((t) => t.name === "luau");
      const lune = (h.tools || []).find((t) => t.name === "lune");
      let cls = "pill-ok", txt = "runtime ready";
      if (!h.core_ready) { cls = "pill-bad"; txt = "luau missing"; }
      else if (!h.luraph_v14_full_ready) { cls = "pill-warn"; txt = "ready · no Lune (v14.x trace-only)"; }
      els.runtimePill.className = "pill " + cls;
      els.runtimePill.textContent = txt;
      els.runtimePill.title =
        "luau: " + (luau && luau.found ? luau.path : "missing") + "\n" +
        "lune: " + (lune && lune.found ? lune.path : "missing");

      renderTools(h);
    } catch (e) {
      els.runtimePill.className = "pill pill-bad";
      els.runtimePill.textContent = "server unreachable";
      els.runtimePill.title = String(e);
    }
  }

  function renderTools(h) {
    els.tools.innerHTML = (h.tools || []).map((t) => `
      <div class="tool ${t.found ? "ok" : "missing"}">
        <span class="dotmark"></span>
        <span class="tname">${esc(t.name)}</span>
        <span class="tfor">${t.found ? "found" : "MISSING"} — ${esc(t.required_for)}</span>
        ${t.path ? `<span class="tpath">${esc(t.path)}</span>` : ""}
        ${t.found ? "" : `<span class="thow">install: ${esc(t.how_to_install)}</span>`}
      </div>`).join("");
  }

  async function loadCapabilities() {
    try {
      const c = await api("/api/capabilities");
      const rows = (c.families || []).map((f) => {
        const levelTag = {
          devirtualize: '<span class="tag tag-devirtualized">devirtualize</span>',
          "devirtualize-runtime": f.available_here
            ? '<span class="tag tag-devirtualized">devirtualize</span>'
            : '<span class="tag tag-unpack">unpack + trace</span>',
          trace: '<span class="tag tag-trace">trace</span>',
          unpack: '<span class="tag tag-unpack">unpack</span>',
          none: '<span class="tag tag-none">—</span>',
        }[f.level] || esc(f.level);
        return `<tr>
          <td class="name">${esc(f.label)}</td>
          <td>${levelTag}</td>
          <td>${esc(f.description)}</td>
          <td class="notes">${esc(f.notes || "")}</td>
        </tr>`;
      });
      els.matrix.innerHTML = rows.join("") ||
        '<tr><td colspan="4" class="muted">nothing to show</td></tr>';

      els.levels.innerHTML = Object.entries(c.levels || {}).map(([k, v]) =>
        `<div class="level"><code>${esc(k)}</code><span>${esc(v)}</span></div>`).join("");

      const lim = c.limits || {};
      if (lim.max_input_bytes) {
        const note = document.createElement("p");
        note.className = "muted";
        note.style.fontSize = "12.5px";
        note.textContent = "Upload limit " + fmtBytes(lim.max_input_bytes) +
          " · " + lim.workers + " concurrent jobs · queue " + lim.max_queue +
          " · results kept " + Math.round((lim.retention_seconds || 0) / 3600) + "h";
        els.levels.appendChild(note);
      }
    } catch (e) {
      els.matrix.innerHTML = '<tr><td colspan="4" class="muted">could not load: ' +
        esc(e.message) + "</td></tr>";
    }
  }

  /* ------------------------------------------------------------- picking */

  function setPicked(file) {
    state.picked = file;
    if (file) {
      els.dzMeta.textContent = file.name + " · " + fmtBytes(file.size);
      els.source.value = "";
    } else {
      els.dzMeta.textContent = "";
    }
    clearFail();
  }

  els.dropzone.addEventListener("click", () => els.file.click());
  els.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); els.file.click(); }
  });
  els.file.addEventListener("change", () => {
    if (els.file.files && els.file.files[0]) setPicked(els.file.files[0]);
  });
  ["dragenter", "dragover"].forEach((ev) =>
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault(); els.dropzone.classList.add("over");
    }));
  ["dragleave", "drop"].forEach((ev) =>
    els.dropzone.addEventListener(ev, (e) => {
      e.preventDefault(); els.dropzone.classList.remove("over");
    }));
  els.dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) setPicked(f);
  });
  els.source.addEventListener("input", () => {
    if (els.source.value.trim()) { state.picked = null; els.dzMeta.textContent = ""; }
    clearFail();
  });

  /* -------------------------------------------------------------- submit */

  els.go.addEventListener("click", submit);
  els.source.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
  });

  async function submit() {
    clearFail();
    const text = els.source.value;
    if (!state.picked && !text.trim()) {
      fail("Choose a file or paste a script first.");
      return;
    }
    els.go.disabled = true;
    els.go.textContent = "Starting…";

    const fd = new FormData();
    if (state.picked) fd.append("file", state.picked, state.picked.name);
    else fd.append("source", text);
    fd.append("devirt", els.optDevirt.checked ? "1" : "0");
    fd.append("timeout", String(els.optTimeout.value || 240));
    fd.append("budget", String(els.optBudget.value || 30));

    try {
      const res = await api("/api/jobs", { method: "POST", body: fd });
      startJob(res.id, res);
    } catch (e) {
      fail(e.message);
      els.go.disabled = false;
      els.go.textContent = "Deobfuscate";
    }
  }

  /* ----------------------------------------------------------------- job */

  function startJob(id, initial) {
    closeStream();
    state.jobId = id;
    state.seq = 0;
    state.resultText = "";
    els.job.hidden = false;
    els.log.textContent = "";
    els.output.innerHTML = '<span class="muted">waiting…</span>';
    els.outputMeta.textContent = "";
    els.artifacts.innerHTML = "";
    els.resultTag.hidden = true;
    els.reportBox.hidden = true;
    els.report.textContent = "";
    els.jobTitle.textContent = "Working…";
    els.jobSub.textContent = (initial && initial.filename ? initial.filename + " · " : "") +
      "job " + id;
    els.cancel.hidden = false;
    els.copy.hidden = true;
    els.download.hidden = true;
    els.again.hidden = true;
    els.go.disabled = false;
    els.go.textContent = "Deobfuscate";

    if (initial && initial.detection) renderDetection(initial.detection);
    els.job.scrollIntoView({ behavior: "smooth", block: "start" });
    openStream(id);
  }

  function renderDetection(d) {
    if (!d) return;
    els.detection.hidden = false;
    els.detection.innerHTML = `
      <span class="big">${esc(d.label || "unknown")}${d.version ? " " + esc(d.version) : ""}</span>
      <span class="conf">confidence ${((d.confidence || 0) * 100).toFixed(0)}%</span>
      ${d.detail ? `<span class="detail">${esc(d.detail)}</span>` : ""}
      <span class="cap"><b>${esc((d.level_description || "").split(":")[0])}</b> —
        ${esc((d.level_description || "").split(":").slice(1).join(":").trim() || d.level || "")}
        ${d.notes ? `<br>${esc(d.notes)}` : ""}</span>`;
  }

  function logLine(text, cls) {
    const line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = text;
    els.log.appendChild(line);
    while (els.log.childNodes.length > 4000) els.log.removeChild(els.log.firstChild);
    if (els.autoscroll.checked) els.log.scrollTop = els.log.scrollHeight;
  }

  function openStream(id) {
    const url = "/api/jobs/" + encodeURIComponent(id) + "/events?since=" + state.seq;
    let es;
    try { es = new EventSource(url); } catch (e) { logLine("stream failed: " + e.message, "l-err"); return; }
    state.es = es;

    const handle = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      if (data.seq) state.seq = data.seq;
      onEvent(data);
    };
    ["log", "detect", "stage", "step", "result", "artifact", "status", "error", "end"]
      .forEach((t) => es.addEventListener(t, handle));
    es.onmessage = handle;
    es.onerror = () => {
      // EventSource reconnects on its own; only give up if the job is finished.
      if (es.readyState === EventSource.CLOSED) pollFinal(id);
    };
  }

  function closeStream() {
    if (state.es) { try { state.es.close(); } catch (_) {} state.es = null; }
  }

  function onEvent(ev) {
    switch (ev.t) {
      case "log":
        logLine(ev.msg || "", logClass(ev.msg || ""));
        break;
      case "detect":
        renderDetection(ev.detection);
        break;
      case "step":
        logLine("── " + (ev.label || ev.engine) + ": " + (ev.status || ""),
                ev.status === "ok" ? "l-ok" : ev.status === "running" ? "l-step" : "l-warn");
        break;
      case "stage":
        logLine("   stage " + (ev.name || "") + " → " + (ev.status || "") +
                (ev.reason ? " (" + ev.reason + ")" : ""), "l-step");
        break;
      case "result":
        logLine("✓ result: " + (ev.kind || "") + " · " + fmtBytes(ev.bytes), "l-ok");
        break;
      case "artifact":
        addArtifact(ev.name, ev.bytes);
        break;
      case "error":
        logLine("✗ " + (ev.msg || "error"), "l-err");
        break;
      case "status":
        if (ev.status === "running") { els.jobTitle.textContent = "Working…"; }
        break;
      case "end":
        closeStream();
        finish(ev.status, ev.error, ev.outcome);
        break;
    }
  }

  function logClass(msg) {
    if (/^\[.*!\]|error|failed|✗/i.test(msg)) return "l-err";
    if (/warn|not found|skipping|missing|no output/i.test(msg)) return "l-warn";
    if (/^\[\+\]|✓|wrote|result:/i.test(msg)) return "l-ok";
    if (/^step |^\[\w+\] (detect|diagnose|unpack|devirt)/i.test(msg)) return "l-step";
    return "";
  }

  function addArtifact(name, bytes) {
    if (!name || !state.jobId) return;
    if (els.artifacts.querySelector('[data-name="' + CSS.escape(name) + '"]')) return;
    const a = document.createElement("a");
    a.href = "/api/jobs/" + encodeURIComponent(state.jobId) + "/artifacts/" + encodeURIComponent(name);
    a.textContent = name + (bytes ? " (" + fmtBytes(bytes) + ")" : "");
    a.dataset.name = name;
    a.target = "_blank";
    a.rel = "noopener";
    els.artifacts.appendChild(a);
  }

  async function finish(status, error, outcome) {
    els.cancel.hidden = true;
    els.again.hidden = false;
    const id = state.jobId;

    if (status === "error" || status === "cancelled") {
      els.jobTitle.textContent = status === "cancelled" ? "Cancelled" : "Failed";
      els.jobSub.textContent = error || "no result";
      els.output.innerHTML = '<span class="muted">' + esc(error || "The job did not produce a result.") + "</span>";
      setResultTag("none");
      return;
    }

    els.jobTitle.textContent = "Done";
    let text = "";
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(id) + "/result.txt");
      if (res.ok) text = await res.text();
    } catch (_) {}

    if (!outcome) {
      try { const j = await api("/api/jobs/" + encodeURIComponent(id)); outcome = j.outcome; } catch (_) {}
    }
    const kind = (outcome && outcome.kind) || "none";
    setResultTag(kind);
    els.jobSub.textContent = KIND_LABEL[kind] + " · " + fmtBytes(text.length) +
      ((outcome && outcome.seconds) ? " · " + outcome.seconds.toFixed(1) + "s" : "");

    state.resultText = text;
    els.output.textContent = text || "(empty result)";
    els.outputMeta.textContent = text.split("\n").length + " lines · " + fmtBytes(text.length);

    els.copy.hidden = false;
    els.download.hidden = false;
    els.download.href = "/api/jobs/" + encodeURIComponent(id) + "/result?download=1";

    try {
      const rep = await api("/api/jobs/" + encodeURIComponent(id) + "/report");
      els.report.textContent = JSON.stringify(rep, null, 2);
      els.reportBox.hidden = false;
      (rep.artifacts || []).forEach((a) => addArtifact(a.name, a.bytes));
      if (rep.outcome && rep.outcome.error) logLine("note: " + rep.outcome.error, "l-warn");
    } catch (_) {}
  }

  function setResultTag(kind) {
    els.resultTag.hidden = false;
    els.resultTag.className = "tag tag-" + (kind || "none");
    els.resultTag.textContent = KIND_LABEL[kind] || kind || "none";
  }

  async function pollFinal(id) {
    try {
      const j = await api("/api/jobs/" + encodeURIComponent(id));
      if (j.status === "done" || j.status === "error" || j.status === "cancelled") {
        finish(j.status, j.error, j.outcome);
      } else {
        openStream(id);
      }
    } catch (_) {}
  }

  /* ------------------------------------------------------------- actions */

  els.cancel.addEventListener("click", async () => {
    if (!state.jobId) return;
    try { await api("/api/jobs/" + encodeURIComponent(state.jobId) + "/cancel", { method: "POST" }); }
    catch (e) { logLine("cancel failed: " + e.message, "l-err"); }
  });

  els.copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.resultText || els.output.textContent);
      els.copy.textContent = "Copied";
      setTimeout(() => { els.copy.textContent = "Copy"; }, 1400);
    } catch (_) {
      els.output.focus();
      document.execCommand && document.execCommand("copy");
    }
  });

  els.again.addEventListener("click", () => {
    closeStream();
    els.job.hidden = true;
    state.jobId = null;
    setPicked(null);
    els.file.value = "";
    els.source.value = "";
    els.detection.hidden = true;
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  /* ---------------------------------------------------------------- boot */

  loadHealth();
  loadCapabilities();
  setInterval(loadHealth, 30000);
})();
