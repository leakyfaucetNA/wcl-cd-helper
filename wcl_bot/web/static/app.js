// Frontend for WCL Healer CD Note Builder.
// Single-page, vanilla JS, three sections (filter / results / note).

const $ = (id) => document.getElementById(id);

// ---- API helpers --------------------------------------------------------
async function api(path, init) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return resp.json();
}

function status(msg, isError = false, timeoutMs = 4000) {
  const el = $("status");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.hidden = false;
  if (timeoutMs) setTimeout(() => (el.hidden = true), timeoutMs);
}

// ---- Spec grid (built from /api/healer_specs) ---------------------------
let SPECS = []; // [{wow_class, spec}]

async function loadHealerSpecs() {
  const data = await api("/api/healer_specs");
  SPECS = data.specs;
  indexHealerSpecs();
  populateManualClassSelect();
  renderSpecGrid();
  renderRoster();
}

// Per-log name overrides, keyed by WCL player name → display name.
// Reset whenever the user picks a different log.
const LOG_OVERRIDES = new Map();

// ---- Persistent roster (server-side, GET/PUT /api/roster) --------------
// Server is single-user / LAN-only, so it's safe to overwrite the whole
// document on each save. Saves are debounced to avoid hammering on
// per-keystroke input events.
let ROSTER = [];

async function loadRosterFromServer() {
  try {
    const data = await api("/api/roster");
    return Array.isArray(data.members) ? data.members : [];
  } catch (e) {
    status(`Couldn't load roster: ${e.message}`, true);
    return [];
  }
}

let _rosterSaveTimer = null;
function saveRoster() {
  clearTimeout(_rosterSaveTimer);
  _rosterSaveTimer = setTimeout(async () => {
    try {
      await api("/api/roster", {
        method: "PUT",
        body: JSON.stringify({ members: ROSTER }),
      });
    } catch (e) {
      status(`Roster save failed: ${e.message}`, true);
    }
  }, 400);
  // If the search panel is currently mirroring the roster, refresh
  // its spec selections too.
  if ($("sync-roster-input")?.checked) {
    syncSpecsFromRoster();
  }
}

function upsertRosterMember(member) {
  const key = (m) => `${m.name}|${m.wow_class}`;
  const existing = ROSTER.find((m) => key(m) === key(member));
  if (existing) {
    // Merge specs (de-duplicated).
    existing.specs = [...new Set([...existing.specs, ...member.specs])];
  } else {
    ROSTER.push(member);
  }
}

function renderRoster() {
  const list = $("roster-list");
  $("roster-meta").textContent = ROSTER.length
    ? `— ${ROSTER.length} member${ROSTER.length === 1 ? "" : "s"}`
    : "— empty";
  if (ROSTER.length === 0) {
    list.innerHTML = `<small class="muted">No healers yet. Look up a guild above or add manually.</small>`;
    return;
  }
  list.innerHTML = ROSTER.map((m, i) => {
    const validSpecs = (HEALER_SPECS_BY_CLASS[m.wow_class] || []);
    const specChecks = validSpecs.map((s) => {
      const checked = m.specs.includes(s) ? "checked" : "";
      return `<label class="roster-spec-check">
        <input type="checkbox" data-idx="${i}" data-spec="${s}" ${checked}> ${s}
      </label>`;
    }).join("");
    return `
      <div class="roster-row class-${m.wow_class}" data-idx="${i}">
        <input type="text" class="roster-name-input" data-idx="${i}" value="${m.name}">
        <select class="roster-class-input" data-idx="${i}">
          ${CLASSES_WITH_HEALERS.map((c) => `
            <option value="${c}" ${c === m.wow_class ? "selected" : ""}>${c}</option>
          `).join("")}
        </select>
        <div class="roster-spec-checks">${specChecks}</div>
        <button class="roster-remove-btn outline" data-idx="${i}" type="button">×</button>
      </div>
    `;
  }).join("");
  // Wire input/change events
  list.querySelectorAll(".roster-name-input").forEach((inp) => {
    inp.addEventListener("input", () => {
      ROSTER[+inp.dataset.idx].name = inp.value;
      saveRoster();
    });
  });
  list.querySelectorAll(".roster-class-input").forEach((sel) => {
    sel.addEventListener("change", () => {
      const i = +sel.dataset.idx;
      ROSTER[i].wow_class = sel.value;
      // Reset specs to a sensible default (clear; user re-ticks).
      ROSTER[i].specs = [];
      saveRoster();
      renderRoster();
    });
  });
  list.querySelectorAll(".roster-spec-check input").forEach((cb) => {
    cb.addEventListener("change", () => {
      const i = +cb.dataset.idx;
      const spec = cb.dataset.spec;
      const set = new Set(ROSTER[i].specs);
      cb.checked ? set.add(spec) : set.delete(spec);
      ROSTER[i].specs = [...set];
      saveRoster();
    });
  });
  list.querySelectorAll(".roster-remove-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      ROSTER.splice(+btn.dataset.idx, 1);
      saveRoster();
      renderRoster();
    });
  });
}

// Populated from /api/healer_specs at boot.
let HEALER_SPECS_BY_CLASS = {};   // {"Priest": ["Holy", "Discipline"], ...}
let CLASSES_WITH_HEALERS = [];    // ["Druid", "Evoker", "Monk", "Paladin", "Priest", "Shaman"]

function indexHealerSpecs() {
  HEALER_SPECS_BY_CLASS = {};
  for (const { wow_class, spec } of SPECS) {
    (HEALER_SPECS_BY_CLASS[wow_class] = HEALER_SPECS_BY_CLASS[wow_class] || []).push(spec);
  }
  CLASSES_WITH_HEALERS = Object.keys(HEALER_SPECS_BY_CLASS).sort();
}

function renderSpecGrid() {
  const list = $("spec-list");
  list.innerHTML = "";
  for (const { wow_class, spec } of SPECS) {
    const key = `${wow_class}_${spec}`.replace(/\W/g, "_");
    const cbId = `spec-cb-${key}`;
    const countId = `spec-count-${key}`;

    const row = document.createElement("div");
    row.className = `spec-row class-${wow_class} disabled`;
    row.dataset.class = wow_class;
    row.dataset.spec = spec;
    row.innerHTML = `
      <input type="checkbox" id="${cbId}" class="spec-checkbox">
      <label for="${cbId}" class="spec-label" title="${wow_class}">${spec}</label>
      <input type="number" id="${countId}" class="spec-count"
             value="1" min="1" max="8" disabled>
    `;
    list.appendChild(row);

    const cb = row.querySelector(".spec-checkbox");
    const count = row.querySelector(".spec-count");

    const sync = () => {
      const isOn = cb.checked;
      row.classList.toggle("disabled", !isOn);
      count.disabled = !isOn;
      updateSpecSummary();
    };
    cb.addEventListener("change", sync);
    count.addEventListener("change", sync);
    count.addEventListener("input", sync);
  }
  updateSpecSummary();
}

function selectedHealers() {
  return [...document.querySelectorAll(".spec-checkbox:checked")].map((cb) => {
    const row = cb.closest(".spec-row");
    const count = parseInt(row.querySelector(".spec-count").value, 10) || 1;
    return {
      wow_class: row.dataset.class,
      spec: row.dataset.spec,
      count,
    };
  });
}

// Overwrite the search-panel's spec selections from the current ROSTER.
// Each roster member contributes their FIRST listed spec to the comp; a
// hybrid Priest with [Holy, Discipline] counts as a Holy Priest unless
// you reorder them in the roster (drag/manual reorder isn't implemented;
// untick "Sync to roster" if you need a different layout).
function syncSpecsFromRoster() {
  document.querySelectorAll(".spec-row").forEach((row) => {
    const cb = row.querySelector(".spec-checkbox");
    const count = row.querySelector(".spec-count");
    cb.checked = false;
    count.value = 1;
    row.classList.add("disabled");
    count.disabled = true;
  });
  const counts = new Map(); // "class|spec" -> count
  for (const m of ROSTER) {
    const spec = m.specs?.[0];
    if (!spec) continue;
    const key = `${m.wow_class}|${spec}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  for (const [key, n] of counts) {
    const [cls, spec] = key.split("|");
    const row = [...document.querySelectorAll(".spec-row")].find(
      (r) => r.dataset.class === cls && r.dataset.spec === spec
    );
    if (!row) continue;
    row.querySelector(".spec-checkbox").checked = true;
    row.querySelector(".spec-count").value = n;
  }
  updateSpecSummary();
}

// While sync is on, lock the spec checkboxes + counts so the user can't
// edit them out of sync with the roster.
function setSpecGridLocked(locked) {
  document.querySelectorAll(".spec-checkbox, .spec-count").forEach((el) => {
    el.disabled = locked;
  });
  if (!locked) {
    // Re-enable based on whether each row's checkbox is checked
    document.querySelectorAll(".spec-row").forEach((row) => {
      const cb = row.querySelector(".spec-checkbox");
      row.querySelector(".spec-count").disabled = !cb.checked;
      row.classList.toggle("disabled", !cb.checked);
    });
  }
}

function updateSpecSummary() {
  const total = selectedHealers().reduce((n, h) => n + h.count, 0);
  $("spec-summary").textContent =
    total === 0
      ? "Pick at least one healer."
      : `${total} healer slot${total === 1 ? "" : "s"} selected.`;
}

// ---- Note remap (per-log) -----------------------------------------------
// Greedy auto-assign from roster: for each log healer in stable order, pick
// the first un-used roster member whose specs include the log healer's spec.
// Sets LOG_OVERRIDES (WCL name → display name) before rendering.
function autoAssignFromRoster(healers) {
  LOG_OVERRIDES.clear();
  const usedRoster = new Set();
  const sorted = [...healers].sort((a, b) => a.name.localeCompare(b.name));
  for (const h of sorted) {
    const match = ROSTER.find(
      (m) => m.wow_class === h.wow_class && m.specs.includes(h.spec) && !usedRoster.has(m.name)
    );
    if (match) {
      LOG_OVERRIDES.set(h.name, match.name);
      usedRoster.add(match.name);
    }
  }
}

// Class colors used to tint <option> elements in the dropdowns.
const CLASS_COLORS_HEX = {
  "Death Knight": "#c41e3a",
  "Demon Hunter": "#a330c9",
  "Druid": "#ff7c0a",
  "Evoker": "#33937f",
  "Hunter": "#aad372",
  "Mage": "#3fc7eb",
  "Monk": "#00ff98",
  "Paladin": "#f48cba",
  "Priest": "#f0ebe0",
  "Rogue": "#fff468",
  "Shaman": "#0070dd",
  "Warlock": "#8788ee",
  "Warrior": "#c69b6d",
};

// Roster matches available for this log healer. Only same-spec matches
// are surfaced — for classes with multiple healing specs (Priest), the
// wrong-spec roster members are intentionally hidden so they can't be
// mis-assigned.
function rosterOptionsFor(h) {
  return ROSTER.filter(
    (m) => m.wow_class === h.wow_class && m.specs.includes(h.spec)
  );
}

function renderNoteRemap(healers) {
  const container = $("note-remap");
  if (!healers || healers.length === 0) {
    container.innerHTML = `<small class="muted">No healers to remap.</small>`;
    return;
  }
  container.innerHTML = healers.map((h, i) => {
    const opts = rosterOptionsFor(h);
    const currentOverride = LOG_OVERRIDES.get(h.name);
    const color = CLASS_COLORS_HEX[h.wow_class] || "";
    const mkOpt = (m) => {
      const sel = currentOverride === m.name ? "selected" : "";
      return `<option value="roster:${m.name}" style="color: ${color}" ${sel}>${m.name}</option>`;
    };
    const keepSel = currentOverride === undefined || currentOverride === h.name ? "selected" : "";
    return `
      <div class="remap-cell class-${h.wow_class}" data-idx="${i}">
        <div class="remap-cell-head">
          <span class="remap-name">${h.name}</span>
          <small class="muted">(${h.spec})</small>
        </div>
        <div class="remap-cell-arrow">&darr;</div>
        <select class="remap-select" data-wcl-name="${h.name}">
          <option value="keep" ${keepSel}>(keep ${h.name})</option>
          ${opts.length ? `<optgroup label="Roster">${opts.map(mkOpt).join("")}</optgroup>` : ""}
          <option value="manual">Manual...</option>
        </select>
        <input type="text" class="remap-manual-input" data-wcl-name="${h.name}" hidden
               placeholder="Custom name" value="${currentOverride && !ROSTER.some((m) => m.name === currentOverride) ? currentOverride : ""}">
      </div>
    `;
  }).join("");

  container.querySelectorAll(".remap-select").forEach((sel) => {
    // If the current override doesn't match any select option, switch to manual mode.
    const wclName = sel.dataset.wclName;
    const cur = LOG_OVERRIDES.get(wclName);
    if (cur !== undefined && cur !== wclName && !ROSTER.some((m) => m.name === cur)) {
      sel.value = "manual";
      const inp = sel.parentElement.querySelector(".remap-manual-input");
      inp.hidden = false;
    }
    sel.addEventListener("change", () => onRemapChange(sel));
  });
  container.querySelectorAll(".remap-manual-input").forEach((inp) => {
    inp.addEventListener("input", () => {
      const v = inp.value.trim();
      if (v) LOG_OVERRIDES.set(inp.dataset.wclName, v);
      else LOG_OVERRIDES.delete(inp.dataset.wclName);
      debouncedRegenerateNote();
    });
  });
}

function onRemapChange(sel) {
  const wclName = sel.dataset.wclName;
  const manualInput = sel.parentElement.querySelector(".remap-manual-input");
  if (sel.value === "keep") {
    LOG_OVERRIDES.delete(wclName);
    manualInput.hidden = true;
    manualInput.value = "";
  } else if (sel.value === "manual") {
    manualInput.hidden = false;
    manualInput.focus();
    // Don't update overrides until the user types something.
  } else if (sel.value.startsWith("roster:")) {
    const rosterName = sel.value.slice("roster:".length);
    LOG_OVERRIDES.set(wclName, rosterName);
    manualInput.hidden = true;
    manualInput.value = "";
  }
  debouncedRegenerateNote();
}

let _regenTimer = null;
function debouncedRegenerateNote() {
  clearTimeout(_regenTimer);
  _regenTimer = setTimeout(() => {
    fetchAndRenderNote($("style-select").value);
  }, 250);
}

// ---- Tabs ---------------------------------------------------------------
function activateTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.hidden = p.dataset.tabPanel !== name;
  });
}

// ---- Guild lookup -------------------------------------------------------
async function doGuildLookup() {
  const name = $("guild-name-input").value.trim();
  const server = $("guild-server-input").value.trim();
  const region = $("guild-region-input").value;
  const limit = parseInt($("guild-limit-input").value, 10) || 3;
  if (!name || !server) {
    status("Need guild name and server.", true);
    return;
  }
  const btn = $("guild-fetch-btn");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  $("guild-status").textContent = "Fetching…";
  try {
    const url = `/api/guild?name=${encodeURIComponent(name)}&server=${encodeURIComponent(server)}&region=${region}&limit=${limit}`;
    const data = await api(url);
    $("guild-status").textContent =
      `Scanned ${data.reports_scanned} report(s) for ${data.guild_name} (${data.server_slug}-${data.server_region}). ` +
      `Adding ${data.members.length} healer(s) to roster.`;
    for (const m of data.members) {
      upsertRosterMember({
        name: m.name,
        wow_class: m.wow_class,
        specs: m.specs,
      });
    }
    saveRoster();
    renderRoster();
  } catch (e) {
    $("guild-status").textContent = `Lookup failed: ${e.message}`;
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
}

async function doLogImport() {
  const log = $("log-import-input").value.trim();
  if (!log) {
    status("Paste a WCL URL or report code.", true);
    return;
  }
  const btn = $("log-import-btn");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  $("log-import-status").textContent = "Importing…";
  try {
    const data = await api("/api/log_healers", {
      method: "POST",
      body: JSON.stringify({ log }),
    });
    const scope = data.fight_id != null ? `fight ${data.fight_id} of ${data.report_code}` : `${data.report_code} (all kills)`;
    $("log-import-status").textContent =
      `Imported ${data.healers.length} healer(s) from ${scope}.`;
    for (const m of data.healers) {
      upsertRosterMember({ name: m.name, wow_class: m.wow_class, specs: m.specs });
    }
    saveRoster();
    renderRoster();
  } catch (e) {
    $("log-import-status").textContent = `Import failed: ${e.message}`;
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
}

function doManualAdd() {
  const name = $("manual-name-input").value.trim();
  const wow_class = $("manual-class-input").value;
  if (!name || !wow_class) {
    status("Name and class required.", true);
    return;
  }
  const specs = [...document.querySelectorAll("#manual-specs-checks input:checked")]
    .map((cb) => cb.value);
  if (specs.length === 0) {
    status("Tick at least one spec.", true);
    return;
  }
  upsertRosterMember({ name, wow_class, specs });
  saveRoster();
  renderRoster();
  $("manual-name-input").value = "";
  renderManualSpecChecks();  // re-render to clear ticks
}

function populateManualClassSelect() {
  $("manual-class-input").innerHTML = CLASSES_WITH_HEALERS.map(
    (c) => `<option value="${c}">${c}</option>`
  ).join("");
  $("manual-class-input").addEventListener("change", renderManualSpecChecks);
  renderManualSpecChecks();
}

function renderManualSpecChecks() {
  const wow_class = $("manual-class-input").value;
  const valid = HEALER_SPECS_BY_CLASS[wow_class] || [];
  // Pre-check the only option if there's just one (Druid/Resto, MW, etc.);
  // for Priest the user must pick at least one.
  const autoCheck = valid.length === 1 ? "checked" : "";
  $("manual-specs-checks").innerHTML = valid
    .map(
      (s) => `
        <label class="spec-check">
          <input type="checkbox" value="${s}" ${autoCheck}> ${s}
        </label>`
    )
    .join("");
}

// ---- Zones / bosses -----------------------------------------------------
let ZONES = [];

async function loadZones() {
  const data = await api("/api/zones");
  ZONES = data.zones.sort((a, b) => Number(a.frozen) - Number(b.frozen));
  const zsel = $("zone-select");
  zsel.innerHTML = "";
  for (const z of ZONES) {
    const opt = document.createElement("option");
    opt.value = z.id;
    opt.textContent = `${z.name}${z.frozen ? " (frozen)" : ""}`;
    zsel.appendChild(opt);
  }
  // Default to the current-tier raid (highest-ID non-frozen zone that isn't
  // a Mythic+ or Delves zone). Falls back to first option if nothing matches.
  const raid = ZONES
    .filter((z) => !z.frozen && !/mythic\+|delves/i.test(z.name))
    .sort((a, b) => b.id - a.id)[0];
  if (raid) zsel.value = String(raid.id);
  zsel.addEventListener("change", populateBosses);
  populateBosses();
}

function populateBosses() {
  const zoneId = parseInt($("zone-select").value, 10);
  const zone = ZONES.find((z) => z.id === zoneId);
  const bsel = $("boss-select");
  bsel.innerHTML = "";
  if (!zone) return;
  for (const e of zone.encounters) {
    const opt = document.createElement("option");
    opt.value = e.id;
    opt.textContent = e.name;
    bsel.appendChild(opt);
  }
}

// ---- Discover -----------------------------------------------------------
function fmtMmss(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function outlierClass(n) {
  if (n <= 2) return "outliers-good";
  if (n <= 5) return "outliers-warn";
  return "outliers-bad";
}

function percentileClass(p) {
  if (p == null) return "";
  if (p >= 95) return "pct-95";
  if (p >= 75) return "pct-75";
  if (p >= 50) return "pct-50";
  if (p >= 25) return "pct-25";
  return "pct-0";
}

async function doDiscover(ev) {
  ev.preventDefault();
  const healers = selectedHealers();
  if (healers.length === 0) {
    status("Pick at least one healer.", true);
    return;
  }
  const payload = {
    encounter_id: parseInt($("boss-select").value, 10),
    difficulty: parseInt($("difficulty-select").value, 10),
    healers,  // {wow_class, spec, count} — backend doesn't need slot names
    metric: $("metric-select").value,
    region: $("region-select").value || null,
    pages: parseInt($("pages-input").value, 10),
    skip_top: parseInt($("skip-top-input").value, 10),
    drop_fastest_pct: parseFloat($("drop-pct-input").value),
    outlier_threshold_seconds: parseFloat($("outlier-input").value),
    include_extra_healers: $("include-extra-input").checked,
  };

  const btn = $("discover-btn");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  try {
    const data = await api("/api/discover", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const targetN = healers.reduce((n, h) => n + h.count, 0);
    renderResults(data, targetN);
  } catch (e) {
    status(`Discover failed: ${e.message}`, true);
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
}

function renderResults(data, targetN) {
  const panel = $("results-panel");
  const content = $("results-content");
  $("results-meta").textContent = `${data.matches.length} match${data.matches.length === 1 ? "" : "es"} · filter: ${data.server_filter}`;

  if (data.matches.length === 0) {
    content.innerHTML = `<p class="muted">No matching kills. Try a different boss, region, or relax the comp.</p>`;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  // Group by healer count.
  const groups = {};
  for (const m of data.matches) {
    (groups[m.healer_count] = groups[m.healer_count] || []).push(m);
  }
  const counts = Object.keys(groups).map(Number).sort((a, b) => a - b);
  const onlyOneGroup = counts.length === 1;

  content.innerHTML = counts.map((hc) => {
    const rows = groups[hc].map((m) => `
      <tr class="match-row" data-report="${m.report_code}" data-fight="${m.fight_id}" data-index="${m.index}">
        <td class="num">${m.index}</td>
        <td>${m.guild ?? "?"}</td>
        <td>${m.region ?? "?"}</td>
        <td class="num">${fmtMmss(m.duration_ms)}</td>
        <td class="num">${m.n_presses}</td>
        <td class="num">${m.n_unique_spells}</td>
        <td class="num">${(m.avg_shift_ms / 1000).toFixed(1)}s</td>
        <td class="num ${outlierClass(m.n_outliers)}">${m.n_outliers}</td>
        <td><a href="${m.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">WCL ↗</a></td>
        <td><button class="pick-btn outline" type="button">Generate note</button></td>
      </tr>
      <tr class="match-detail-row" data-for-index="${m.index}" hidden>
        <td colspan="10"><div class="match-detail">Loading…</div></td>
      </tr>
    `).join("");

    // Default-open if it's the only group, or if it matches the user's
    // target healer count. Extras (N+1, N+2…) collapse by default.
    const openByDefault = onlyOneGroup || hc === targetN;
    const exactMarker = hc === targetN ? " <span class=\"muted\">(your comp size)</span>" : "";
    return `
      <details class="result-group" ${openByDefault ? "open" : ""}>
        <summary class="group-header">
          <strong>${hc} healer${hc === 1 ? "" : "s"}</strong>
          <span class="muted">— ${groups[hc].length} match${groups[hc].length === 1 ? "" : "es"}</span>${exactMarker}
        </summary>
        <table class="results-table">
          <thead><tr>
            <th class="num">#</th><th>Guild</th><th>Region</th>
            <th class="num">Duration</th><th class="num">Presses</th>
            <th class="num">CDs</th><th class="num">Avg shift</th>
            <th class="num">Outliers</th><th></th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </details>
    `;
  }).join("");

  content.querySelectorAll(".match-row").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a") || e.target.closest("button")) return;
      toggleDetail(tr);
    });
  });
  content.querySelectorAll(".pick-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const tr = btn.closest(".match-row");
      loadNote(tr.dataset.report, parseInt(tr.dataset.fight, 10));
    });
  });

  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- Row expansion: healer percentiles ---------------------------------
async function toggleDetail(tr) {
  const idx = tr.dataset.index;
  const detail = document.querySelector(`.match-detail-row[data-for-index="${idx}"]`);
  if (!detail) return;
  if (!detail.hidden) {
    detail.hidden = true;
    return;
  }
  detail.hidden = false;
  const container = detail.querySelector(".match-detail");
  container.innerHTML = "Loading healer details…";
  try {
    const data = await api("/api/log_detail", {
      method: "POST",
      body: JSON.stringify({
        report_code: tr.dataset.report,
        fight_id: parseInt(tr.dataset.fight, 10),
      }),
    });
    renderLogDetail(container, data);
  } catch (e) {
    container.innerHTML = `<span class="muted">Couldn't load detail: ${e.message}</span>`;
  }
}

function renderLogDetail(container, data) {
  if (!data.healers || data.healers.length === 0) {
    container.innerHTML = `<span class="muted">No healer data.</span>`;
    return;
  }
  // Union of every metric field that appeared across this fight's healers.
  // Shown so we can identify which one corresponds to WCL's "Parse %".
  const fieldSet = new Set();
  data.healers.forEach((h) => Object.keys(h.metrics || {}).forEach((k) => fieldSet.add(k)));
  const fields = [...fieldSet].sort();

  const headerCols = fields.map((f) => `<th class="num">${f}</th>`).join("");
  const rows = data.healers.map((h) => {
    const m = h.metrics || {};
    const cells = fields.map((f) => {
      const v = m[f];
      if (v == null) return `<td class="num">—</td>`;
      const isPct = f.toLowerCase().includes("percent") || f.toLowerCase().includes("pct");
      const cls = isPct ? percentileClass(v) : "";
      return `<td class="num ${cls}">${Number.isFinite(v) ? v.toFixed(1) : v}</td>`;
    }).join("");
    return `
      <tr class="class-${h.wow_class}">
        <td>${h.name}</td>
        <td>${h.wow_class} / ${h.spec}</td>
        ${cells}
      </tr>
    `;
  }).join("");
  container.innerHTML = `
    <table class="detail-table">
      <thead><tr><th>Healer</th><th>Spec</th>${headerCols}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <small class="muted">All numeric fields from the rankings blob shown above so we can pinpoint Parse %. Once we confirm the field, the table will collapse to just that column.</small>
  `;
}

// ---- Note ---------------------------------------------------------------
let CURRENT_NOTE = null;   // {report_code, fight_id}
let CURRENT_HEALERS = null; // [{name, wow_class, spec, rank_percent}] from log_detail

async function loadNote(reportCode, fightId) {
  CURRENT_NOTE = { report_code: reportCode, fight_id: fightId };
  try {
    const detail = await api("/api/log_detail", {
      method: "POST",
      body: JSON.stringify(CURRENT_NOTE),
    });
    CURRENT_HEALERS = detail.healers;
  } catch (e) {
    status(`Couldn't fetch healers for remap: ${e.message}`, true);
    CURRENT_HEALERS = null;
  }
  // Greedy auto-assign from roster before rendering (overrides may exist
  // after this call; the remap UI shows them pre-selected).
  if (CURRENT_HEALERS) {
    autoAssignFromRoster(CURRENT_HEALERS);
  } else {
    LOG_OVERRIDES.clear();
  }
  renderNoteRemap(CURRENT_HEALERS);
  await fetchAndRenderNote($("style-select").value);
  const panel = $("note-panel");
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function fetchAndRenderNote(style) {
  if (!CURRENT_NOTE) return;
  $("note-output").value = "Loading…";
  const overrides = Object.fromEntries(LOG_OVERRIDES);
  try {
    const data = await api("/api/note", {
      method: "POST",
      body: JSON.stringify({ ...CURRENT_NOTE, style, name_overrides: overrides }),
    });
    $("note-meta").textContent =
      `${fmtMmss(data.duration_ms)} · ${data.report_code}#fight=${data.fight_id}`;
    $("note-output").value = data.note_text;
  } catch (e) {
    $("note-output").value = "";
    status(`Note failed: ${e.message}`, true);
  }
}

async function copyNote() {
  const text = $("note-output").value;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    status("Note copied to clipboard.");
  } catch (e) {
    $("note-output").select();
    status("Couldn't auto-copy; text is selected.", true);
  }
}

// ---- Boot ---------------------------------------------------------------
async function boot() {
  // Tabs
  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => activateTab(b.dataset.tab))
  );

  // Search tab
  $("filter-form").addEventListener("submit", doDiscover);
  $("sync-roster-input").addEventListener("change", (e) => {
    if (e.target.checked) {
      syncSpecsFromRoster();
      setSpecGridLocked(true);
    } else {
      setSpecGridLocked(false);
    }
  });
  $("style-select").addEventListener("change", (e) =>
    fetchAndRenderNote(e.target.value)
  );
  $("copy-btn").addEventListener("click", copyNote);
  $("back-btn").addEventListener("click", () => {
    $("note-panel").hidden = true;
    $("results-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // Settings tab
  $("guild-fetch-btn").addEventListener("click", doGuildLookup);
  $("guild-form").addEventListener("submit", (e) => {
    e.preventDefault();
    doGuildLookup();
  });
  $("log-import-btn").addEventListener("click", doLogImport);
  $("log-import-form").addEventListener("submit", (e) => {
    e.preventDefault();
    doLogImport();
  });
  $("roster-add-btn").addEventListener("click", doManualAdd);
  $("roster-add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    doManualAdd();
  });
  $("roster-clear-btn").addEventListener("click", () => {
    if (!confirm("Clear all roster members?")) return;
    ROSTER = [];
    saveRoster();
    renderRoster();
  });

  try {
    await loadHealerSpecs();        // populates SPECS + CLASSES_WITH_HEALERS
    await Promise.all([
      loadZones(),
      (async () => {
        ROSTER = await loadRosterFromServer();
        renderRoster();
      })(),
    ]);
  } catch (e) {
    status(`Init failed: ${e.message}`, true, 0);
  }
}

boot();
