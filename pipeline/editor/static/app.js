const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let currentJob = null;
let proposedDescriptions = null;
let bundleState = null;

function setStatus(message, state) {
  const bar = $("#status-bar");
  $("#status-line").textContent = message;
  bar.hidden = false;
  bar.classList.toggle("done", state === "done");
  bar.classList.toggle("error", state === "error");
  bar.classList.toggle("awaiting", state === "awaiting");
}

// --- lightbox ---
document.addEventListener("click", (e) => {
  const img = e.target.closest(".asset-card img");
  if (img) {
    $("#lightbox-img").src = img.src;
    $("#lightbox").hidden = false;
  } else if (e.target.closest("#lightbox")) {
    $("#lightbox").hidden = true;
    $("#lightbox-img").src = "";
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#lightbox").hidden) {
    $("#lightbox").hidden = true;
    $("#lightbox-img").src = "";
  }
});

// --- restart ---
$("#restart-btn").onclick = async () => {
  if (!confirm("Restart the editor server?")) return;
  $("#restart-btn").disabled = true;
  $("#restart-btn").textContent = "Restarting...";
  const restartedAt = Date.now();
  try { await fetch("/api/restart", { method: "POST" }); } catch {}
  // Poll /api/health until the new server responds with a start_time after
  // the restart was requested, then reload. Gives up after 30s.
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 300));
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) {
        const j = await r.json();
        if ((j.start_time || 0) * 1000 >= restartedAt - 500) {
          location.reload();
          return;
        }
      }
    } catch {}
  }
  $("#restart-btn").textContent = "Restart failed — reload manually";
};

// --- bundle bar ---
$("#refresh-bundle").onclick = refreshBundle;
$("#bundle-dir").addEventListener("change", refreshBundle);
window.addEventListener("DOMContentLoaded", () => {
  if ($("#bundle-dir").value.trim()) refreshBundle();
  initBackends();
});

async function initBackends() {
  const sel = $("#image-gen");
  const qualityLabel = $("#image-quality-label");
  const qualitySel = $("#image-quality");
  let info = { backends: ["placeholder", "nanobanana", "openai"],
               qualities: ["low", "medium", "high"],
               quality_default: "medium",
               quality_backends: ["openai"] };
  try {
    const r = await fetch("/api/backends");
    if (r.ok) info = await r.json();
  } catch {}
  sel.innerHTML = "";
  info.backends.forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  });
  const saved = localStorage.getItem("image_gen") || "";
  const preferred = info.backends.includes(saved) ? saved
                  : (info.backends.includes("openai") ? "openai" : info.backends[0]);
  sel.value = preferred;

  qualitySel.innerHTML = "";
  info.qualities.forEach((q) => {
    const o = document.createElement("option");
    o.value = q;
    o.textContent = q;
    if (q === info.quality_default) o.selected = true;
    qualitySel.appendChild(o);
  });
  const savedQuality = localStorage.getItem("image_quality");
  if (savedQuality && info.qualities.includes(savedQuality)) qualitySel.value = savedQuality;

  const updateQualityVisibility = () => {
    qualityLabel.hidden = !info.quality_backends.includes(sel.value);
  };
  updateQualityVisibility();
  sel.addEventListener("change", () => {
    localStorage.setItem("image_gen", sel.value);
    updateQualityVisibility();
  });
  qualitySel.addEventListener("change", () => {
    localStorage.setItem("image_quality", qualitySel.value);
  });
}

async function refreshBundle() {
  const path = $("#bundle-dir").value.trim();
  if (!path) return;
  const r = await fetch("/api/bundle?path=" + encodeURIComponent(path));
  bundleState = await r.json();
  renderAssets();
}

// --- build tab ---
$("#src-file").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("#src-text").value = await f.text();
  const stem = f.name.replace(/\.[^.]+$/, "");
  if (!$("#bundle-dir").value.trim() && stem) {
    $("#bundle-dir").value = "out/" + stem;
  }
};

$("#start-btn").onclick = async () => {
  let text = $("#src-text").value;
  if (!text.trim()) {
    const f = $("#src-file").files[0];
    if (f) {
      text = await f.text();
      $("#src-text").value = text;
    }
  }
  const out_dir = $("#bundle-dir").value.trim();
  const image_gen = $("#image-gen").value;
  const image_quality = $("#image-quality").value;
  const no_cache = $("#no-cache").checked;
  const skip_confirm = $("#skip-confirm").checked;
  if (!text.trim()) { alert("Source text is required"); return; }
  if (!out_dir) { alert("Bundle dir is required"); return; }
  $("#steps").innerHTML = "";
  setStatus("starting...", "running");
  const r = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, out_dir, image_gen, image_quality, no_cache, skip_confirm }),
  });
  const { job_id } = await r.json();
  currentJob = job_id;
  streamEvents(job_id);
};

function streamEvents(jobId) {
  const src = new EventSource("/api/events/" + jobId);
  src.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    handleEvent(evt);
  };
  src.addEventListener("end", () => src.close());
  src.onerror = () => src.close();
}

function step(id, title) {
  let el = document.getElementById("step-" + id);
  if (!el) {
    el = document.createElement("div");
    el.id = "step-" + id;
    el.className = "step";
    el.innerHTML = `<h3>${title}</h3><div class="body"></div>`;
    $("#steps").appendChild(el);
  }
  return el;
}

$("#clear-debug").onclick = () => { $("#debug-log").textContent = ""; };

function appendDebug(line) {
  const el = $("#debug-log");
  el.textContent += line + "\n";
  if ($("#debug-autoscroll").checked) el.scrollTop = el.scrollHeight;
}

function handleEvent({ type, payload }) {
  if (type === "debug") {
    appendDebug(payload.line);
    return;
  }
  if (type === "status") {
    appendDebug("[status] " + payload.message);
    setStatus(payload.message, "running");
    return;
  }
  if (type === "split_done") {
    const el = step("split", "Chapters");
    el.classList.add("done");
    el.querySelector(".body").innerHTML = payload.chapters.map(
      (c) => `<div>${c.id} — ${c.title} (${c.segments} segments)</div>`
    ).join("");
    return;
  }
  if (type === "timelines_done") {
    const el = step("timelines", "Timelines generated");
    el.classList.add("done");
    el.querySelector(".body").innerHTML = payload.chapters.map(
      (c) => `<div>${c.id}: ${c.commands} commands</div>`
    ).join("");
    return;
  }
  if (type === "manifests") {
    const el = step("manifests", "Asset manifest");
    el.classList.add("done");
    el.querySelector(".body").innerHTML = `
      <div>Major characters: ${payload.major_chars.join(", ") || "(none)"}</div>
      <div>Minor: ${payload.minor_chars.join(", ") || "(none)"}</div>
      <div>Backgrounds: ${payload.bg_ids.join(", ") || "(none)"}</div>`;
    return;
  }
  if (type === "descriptions_proposed") {
    proposedDescriptions = payload;
    const el = step("descriptions", "Descriptions");
    el.classList.add("awaiting");
    setStatus("Awaiting description confirmation", "awaiting");
    const chars = Object.entries(payload.characters || {});
    const bgs = Object.entries(payload.backgrounds || {});
    const names = payload.display_names || {};
    const charIds = new Set(chars.map(([cid]) => cid));
    const nameOnlyIds = Object.keys(names).filter((cid) => !charIds.has(cid));
    const styleText = payload.visual_style || "";
    el.querySelector(".body").innerHTML = `
      <div>Edit descriptions below, then click Confirm.</div>
      <div style="margin-top:8px">
        <label>Visual style (applies to all images; leave empty and fill via Baseline &gt; Propose later if unsure)
          <textarea id="visual-style-input" class="autosize" placeholder="e.g. soft watercolor anime style with warm muted tones">${escapeHtml(styleText)}</textarea>
        </label>
      </div>
      ${chars.map(([cid, desc]) => `
        <div style="margin-top:8px">
          <label>char <code>${cid}</code> display name
            <input type="text" data-name="${cid}" value="${escapeHtml(names[cid] || cid)}" />
          </label>
          <label>description
            <textarea data-char="${cid}" class="autosize">${escapeHtml(desc)}</textarea>
          </label>
        </div>`).join("")}
      ${nameOnlyIds.map((cid) => `
        <div style="margin-top:8px">
          <label>minor <code>${cid}</code> display name
            <input type="text" data-name="${cid}" value="${escapeHtml(names[cid] || cid)}" />
          </label>
        </div>`).join("")}
      ${bgs.map(([bid, desc]) => `
        <div style="margin-top:8px">
          <label>bg <code>${bid}</code>
            <textarea data-bg="${bid}" class="autosize">${escapeHtml(desc)}</textarea>
          </label>
        </div>`).join("")}
      <button class="primary" id="confirm-desc">Confirm descriptions</button>`;
    autosizeAll(el);
    $("#confirm-desc").onclick = () => {
      const edits = { characters: {}, backgrounds: {}, display_names: {} };
      const styleInput = document.getElementById("visual-style-input");
      if (styleInput) edits.visual_style = styleInput.value;
      el.querySelectorAll("[data-char]").forEach((t) => edits.characters[t.dataset.char] = t.value);
      el.querySelectorAll("[data-bg]").forEach((t) => edits.backgrounds[t.dataset.bg] = t.value);
      el.querySelectorAll("[data-name]").forEach((t) => edits.display_names[t.dataset.name] = t.value);
      fetch("/api/confirm/" + currentJob, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step: "descriptions", edits }),
      });
      el.classList.remove("awaiting");
      el.classList.add("done");
      setStatus("Descriptions confirmed, continuing...", "running");
      el.querySelector(".body").innerHTML = "Descriptions saved.";
    };
    return;
  }
  if (type === "awaiting_confirm") {
    if (payload.step === "baseline" || payload.step === "basic_chars") {
      const isBaseline = payload.step === "baseline";
      const el = step(payload.step, isBaseline ? "Baseline art style" : "Basic characters");
      el.classList.add("awaiting");
      setStatus(isBaseline ? "Awaiting baseline confirmation" : "Awaiting basic chars confirmation", "awaiting");
      const caption = isBaseline
        ? "Review the baseline in the Assets panel below, then confirm or regenerate."
        : "Review basic characters in the Assets panel below, then confirm.";
      el.querySelector(".body").innerHTML = `
        <div>${caption}</div>
        <div class="action-row" style="margin-top:8px">
          <button class="primary" data-confirm>${isBaseline ? "Looks good" : "Confirm basic chars"}</button>
        </div>`;
      el.querySelector("[data-confirm]").onclick = () => {
        fetch("/api/confirm/" + currentJob, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: payload.step }),
        });
        el.classList.remove("awaiting"); el.classList.add("done");
        setStatus(isBaseline ? "Baseline confirmed, continuing..." : "Basic chars confirmed, continuing...", "running");
        el.querySelector(".body").innerHTML = `<div>${isBaseline ? "Baseline confirmed." : "Basic characters confirmed."}</div>`;
      };
      refreshBundle();
      return;
    }
  }
  if (type === "asset_updated") {
    document.querySelectorAll("img").forEach((img) => {
      if (img.src.includes(payload.path.split(/[\\/]/).pop())) {
        img.src = img.src.replace(/&t=\d+/, "&t=" + (payload.mtime * 1000 | 0));
      }
    });
    return;
  }
  if (type === "asset_written") {
    const el = step("assets", "Assets generated");
    const body = el.querySelector(".body");
    const line = document.createElement("div");
    const label = payload.kind === "char_expr" ? `${payload.id}/${payload.expr}` :
                  payload.kind === "char_neutral" ? `${payload.id}/neutral` :
                  payload.kind === "bg" ? `bg ${payload.id}` :
                  payload.kind;
    line.textContent = `✓ ${label}`;
    body.appendChild(line);
    refreshBundle();
    return;
  }
  if (type === "done") {
    setStatus("Done. Bundle at: " + payload.out_dir, "done");
    const el = step("done", "Done");
    el.classList.add("done");
    refreshBundle();
    return;
  }
  if (type === "step_error") {
    setStatus("ERROR: " + payload.message, "error");
    const el = step("error", "Error");
    el.classList.add("error");
    el.querySelector(".body").innerHTML = `<pre>${escapeHtml(payload.traceback || payload.message)}</pre>`;
    return;
  }
}

// --- assets tab ---
function renderAssets() {
  const view = $("#assets-view");
  if (!bundleState || !bundleState.exists) {
    view.innerHTML = `<div class="panel"><p>No bundle at this path yet. Run a build or point to an existing bundle.</p></div>`;
    return;
  }
  const roster = bundleState.cast?.cast || {};
  const bgs = bundleState.backgrounds?.backgrounds || {};
  const assets = bundleState.assets || {};
  const bundlePath = bundleState.path;

  let html = `<div class="panel" style="flex-basis:100%">`;

  // Baseline
  const visualStyle = bundleState.backgrounds?.visual_style || "";
  if (assets.baseline) {
    html += `<div class="group-title">Baseline</div>
      <div class="asset-grid">
        <div class="asset-card">
          <img src="/api/asset?bundle=${enc(bundlePath)}&rel=${enc(assets.baseline.path)}&t=${assets.baseline.mtime}" />
          <div class="id">baseline</div>
          <textarea id="baseline-style" class="autosize" placeholder="visual style (applies to all images)">${escapeHtml(visualStyle)}</textarea>
          <div class="actions">
            <button id="propose-style">Propose</button>
            <button data-action="regen" data-kind="baseline">Regen</button>
            <label style="display:inline"><input type="checkbox" data-cascade="baseline" /> cascade</label>
            <button data-action="upload" data-kind="baseline">Upload</button>
          </div>
        </div>
      </div>`;
  }

  // Characters
  html += `<div class="group-title">Characters</div><div class="asset-grid">`;
  for (const [cid, imgs] of Object.entries(assets.char || {})) {
    const desc = roster[cid]?.visual_description || "";
    const name = roster[cid]?.display_name || cid;
    const count = roster[cid]?.appearance_count || 0;
    for (const img of imgs) {
      html += `<div class="asset-card" data-cid="${cid}" data-expr="${img.expr}">
        <img src="/api/asset?bundle=${enc(bundlePath)}&rel=${enc(img.path)}&t=${img.mtime}" />
        <div class="id">${cid}/${img.expr} — ${escapeHtml(name)} (${count}×)</div>
        ${img.expr === "neutral" ? `
          <textarea data-desc="${cid}" class="autosize" placeholder="description">${escapeHtml(desc)}</textarea>` : ""}
        <div class="actions">
          <button data-action="regen" data-kind="${img.expr === 'neutral' ? 'char_neutral' : 'char_expr'}" data-id="${cid}" data-expr="${img.expr}">Regen</button>
          ${img.expr === "neutral" ? `<label style="display:inline"><input type="checkbox" data-cascade="${cid}" /> cascade</label>` : ""}
          <button data-action="upload" data-kind="${img.expr === 'neutral' ? 'char_neutral' : 'char_expr'}" data-id="${cid}" data-expr="${img.expr}">Upload</button>
        </div>
      </div>`;
    }
  }
  html += `</div>`;

  // Backgrounds
  html += `<div class="group-title">Backgrounds</div><div class="asset-grid">`;
  for (const bg of assets.bg || []) {
    const desc = bgs[bg.id]?.visual_description || "";
    html += `<div class="asset-card" data-bgid="${bg.id}">
      <img src="/api/asset?bundle=${enc(bundlePath)}&rel=${enc(bg.path)}&t=${bg.mtime}" />
      <div class="id">${bg.id}</div>
      <textarea data-bgdesc="${bg.id}" class="autosize" placeholder="description">${escapeHtml(desc)}</textarea>
      <div class="actions">
        <button data-action="regen" data-kind="bg" data-id="${bg.id}">Regen</button>
        <button data-action="upload" data-kind="bg" data-id="${bg.id}">Upload</button>
      </div>
    </div>`;
  }
  html += `</div></div>`;

  view.innerHTML = html;
  autosizeAll(view);

  view.querySelectorAll('button[data-action="regen"]').forEach((b) => {
    b.onclick = async () => {
      const kind = b.dataset.kind;
      const id = b.dataset.id;
      const expr = b.dataset.expr;
      const card = b.closest(".asset-card");
      let description;
      if (kind === "char_neutral" || kind === "char_expr") {
        const ta = view.querySelector(`textarea[data-desc="${id}"]`);
        if (ta) description = ta.value;
      } else if (kind === "bg") {
        const ta = card.querySelector(`textarea[data-bgdesc="${id}"]`);
        if (ta) description = ta.value;
      }
      const cascadeBox = kind === "baseline"
        ? view.querySelector('[data-cascade="baseline"]')
        : (kind === "char_neutral" ? view.querySelector(`[data-cascade="${id}"]`) : null);
      const cascade = cascadeBox ? cascadeBox.checked : false;
      let visual_style;
      if (kind === "baseline") {
        const styleTA = document.getElementById("baseline-style");
        if (styleTA) visual_style = styleTA.value;
      }
      const label = kind === "baseline" ? "baseline" :
                    kind === "bg" ? `bg ${id}` : `${id}/${expr}`;
      b.disabled = true;
      b.textContent = "...";
      setStatus(`Regenerating ${label}${cascade ? " + cascade" : ""}...`, "running");
      try {
        const r = await fetch("/api/regenerate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bundle: bundlePath, kind, id, expr, description, visual_style, cascade }),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          setStatus(`Regen failed: ${err.detail || r.statusText}`, "error");
        } else {
          setStatus(`Regenerated ${label}${cascade ? " + cascade" : ""}`, "done");
        }
      } catch (e) {
        setStatus(`Regen error: ${e.message}`, "error");
      }
      await refreshBundle();
    };
  });

  const proposeBtn = view.querySelector('#propose-style');
  if (proposeBtn) {
    proposeBtn.onclick = async () => {
      const ta = document.getElementById("baseline-style");
      proposeBtn.disabled = true;
      const prev = proposeBtn.textContent;
      proposeBtn.textContent = "...";
      setStatus("Proposing visual style via Gemini...", "running");
      try {
        const r = await fetch("/api/propose_visual_style", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bundle: bundlePath }),
        });
        const j = await r.json();
        if (!r.ok) {
          setStatus(`Propose failed: ${j.detail || r.statusText}`, "error");
        } else if (ta) {
          ta.value = j.visual_style || "";
          ta.dispatchEvent(new Event("input"));
          setStatus("Proposed style — edit and click Regen.", "done");
        }
      } catch (e) {
        setStatus(`Propose error: ${e.message}`, "error");
      }
      proposeBtn.disabled = false;
      proposeBtn.textContent = prev;
    };
  }

  view.querySelectorAll('button[data-action="upload"]').forEach((b) => {
    b.onclick = () => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/png";
      input.onchange = async () => {
        const f = input.files[0];
        if (!f) return;
        const fd = new FormData();
        fd.append("bundle", bundlePath);
        fd.append("kind", b.dataset.kind);
        fd.append("id", b.dataset.id || "");
        fd.append("expr", b.dataset.expr || "");
        fd.append("file", f);
        await fetch("/api/upload", { method: "POST", body: fd });
        await refreshBundle();
      };
      input.click();
    };
  });
}

// --- play tab ---
$("#play-btn").onclick = async () => {
  const bundle = $("#bundle-dir").value.trim();
  if (!bundle) { alert("Bundle dir is required"); return; }
  $("#play-status").textContent = "launching...";
  try {
    const r = await fetch("/api/play", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle }),
    });
    const j = await r.json();
    if (!r.ok) { $("#play-status").textContent = "ERROR: " + (j.detail || "unknown"); return; }
    $("#play-status").textContent = "Godot launched (pid " + j.pid + ")";
  } catch (e) {
    $("#play-status").textContent = "ERROR: " + e.message;
  }
};

// --- utils ---
function autosizeAll(root) {
  root.querySelectorAll("textarea.autosize").forEach((t) => {
    const fit = () => {
      t.style.height = "auto";
      t.style.height = t.scrollHeight + "px";
    };
    t.addEventListener("input", fit);
    fit();
  });
}
function enc(s) { return encodeURIComponent(s); }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
