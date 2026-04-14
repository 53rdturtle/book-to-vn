const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let currentJob = null;
let proposedDescriptions = null;
let bundleState = null;

// --- tab switching ---
$$(".tab").forEach((b) => {
  b.onclick = () => {
    $$(".tab").forEach((x) => x.classList.remove("active"));
    $$(".tab-panel").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "assets") refreshBundle();
  };
});

// --- bundle bar ---
$("#refresh-bundle").onclick = refreshBundle;

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
  const no_cache = $("#no-cache").checked;
  const skip_confirm = $("#skip-confirm").checked;
  if (!text.trim()) { alert("Source text is required"); return; }
  if (!out_dir) { alert("Bundle dir is required"); return; }
  $("#steps").innerHTML = "";
  $("#status-line").textContent = "starting...";
  const r = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, out_dir, image_gen, no_cache, skip_confirm }),
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

function handleEvent({ type, payload }) {
  if (type === "status") {
    $("#status-line").textContent = payload.message;
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
    const chars = Object.entries(payload.characters || {});
    const bgs = Object.entries(payload.backgrounds || {});
    const names = payload.display_names || {};
    el.querySelector(".body").innerHTML = `
      <div>Edit descriptions below, then click Confirm.</div>
      ${chars.map(([cid, desc]) => `
        <div style="margin-top:8px">
          <label>char <code>${cid}</code> display name
            <input type="text" data-name="${cid}" value="${escapeHtml(names[cid] || cid)}" />
          </label>
          <label>description
            <textarea data-char="${cid}" class="autosize">${escapeHtml(desc)}</textarea>
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
      el.querySelector(".body").innerHTML = "Descriptions saved.";
    };
    return;
  }
  if (type === "awaiting_confirm") {
    if (payload.step === "baseline") {
      const el = step("baseline", "Baseline art style");
      el.classList.add("awaiting");
      el.querySelector(".body").innerHTML = `
        <img src="/api/asset?bundle=${enc($("#bundle-dir").value)}&rel=assets/char/_baseline/neutral.png&t=${Date.now()}"
             style="max-width:400px;display:block;" />
        <div style="margin-top:8px">
          <button class="primary" id="confirm-baseline">Looks good</button>
          <button id="regen-baseline">Regenerate</button>
        </div>`;
      $("#confirm-baseline").onclick = () => {
        fetch("/api/confirm/" + currentJob, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: "baseline" }),
        });
        el.classList.remove("awaiting"); el.classList.add("done");
      };
      $("#regen-baseline").onclick = async () => {
        await fetch("/api/regenerate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bundle: $("#bundle-dir").value, kind: "baseline" }),
        });
        el.querySelector("img").src =
          `/api/asset?bundle=${enc($("#bundle-dir").value)}&rel=assets/char/_baseline/neutral.png&t=${Date.now()}`;
      };
      return;
    }
    if (payload.step === "basic_chars") {
      const el = step("basic", "Basic characters");
      el.classList.add("awaiting");
      // grab the asset_written events accumulated under neutral; rebuild from bundle list
      fetch("/api/bundle?path=" + enc($("#bundle-dir").value)).then((r) => r.json()).then((st) => {
        const entries = Object.entries(st.assets?.char || {});
        el.querySelector(".body").innerHTML = `
          <div class="asset-grid">
            ${entries.map(([cid, imgs]) => {
              const neutral = imgs.find((i) => i.expr === "neutral");
              if (!neutral) return "";
              return `<div class="asset-card">
                <img src="/api/asset?bundle=${enc($("#bundle-dir").value)}&rel=${enc(neutral.path)}&t=${neutral.mtime}" />
                <div class="id">${cid}</div>
                <button data-regen="${cid}">Regenerate</button>
              </div>`;
            }).join("")}
          </div>
          <button class="primary" id="confirm-basic" style="margin-top:12px">Confirm basic chars</button>`;
        el.querySelectorAll("[data-regen]").forEach((b) => {
          b.onclick = async () => {
            await fetch("/api/regenerate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ bundle: $("#bundle-dir").value, kind: "char_neutral", id: b.dataset.regen }),
            });
            const img = b.parentElement.querySelector("img");
            img.src = img.src.replace(/&t=\d+/, "&t=" + Date.now());
          };
        });
        $("#confirm-basic").onclick = () => {
          fetch("/api/confirm/" + currentJob, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ step: "basic_chars" }),
          });
          el.classList.remove("awaiting"); el.classList.add("done");
        };
      });
      return;
    }
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
    return;
  }
  if (type === "done") {
    $("#status-line").textContent = "Done. Bundle at: " + payload.out_dir;
    const el = step("done", "Done");
    el.classList.add("done");
    return;
  }
  if (type === "step_error") {
    $("#status-line").textContent = "ERROR: " + payload.message;
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
  if (assets.baseline) {
    html += `<div class="group-title">Baseline</div>
      <div class="asset-grid">
        <div class="asset-card">
          <img src="/api/asset?bundle=${enc(bundlePath)}&rel=${enc(assets.baseline.path)}&t=${assets.baseline.mtime}" />
          <div class="id">baseline</div>
          <div class="actions">
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
      b.disabled = true;
      b.textContent = "...";
      await fetch("/api/regenerate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bundle: bundlePath, kind, id, expr, description, cascade }),
      });
      await refreshBundle();
    };
  });

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
