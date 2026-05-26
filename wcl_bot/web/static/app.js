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

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
// Per-log class overrides (Manual mode only). WCL player name → target class.
// Used to rewrite the player's spells as the target class's equivalents.
const LOG_CLASS_OVERRIDES = new Map();
// Per-log ignored players: every cast by a name in this set is dropped from
// the note + timeline. Surfaced as the "Ignore" option in the raid-CD remap
// dropdown so a single DPS can be silenced without disabling their spell.
const LOG_IGNORED_PLAYERS = new Set();

// Persistent settings (server-side). Two-list model:
//   EXCLUDED_SPELL_IDS  — default-included spells (healer CDs) the user disabled
//   ENABLED_SPELL_IDS   — default-excluded spells (DPS raid CDs) the user opted into
let TRACKED_SPELLS = [];                  // [{spell_id, ..., group, default_excluded}]
const EXCLUDED_SPELL_IDS = new Set();
const ENABLED_SPELL_IDS = new Set();

async function loadSettingsFromServer() {
  try {
    const data = await api("/api/settings");
    EXCLUDED_SPELL_IDS.clear();
    ENABLED_SPELL_IDS.clear();
    for (const id of data.excluded_spell_ids || []) EXCLUDED_SPELL_IDS.add(id);
    for (const id of data.enabled_spell_ids || []) ENABLED_SPELL_IDS.add(id);
  } catch (e) {
    status(`Couldn't load settings: ${e.message}`, true);
  }
}

let _settingsSaveTimer = null;
function saveSettings() {
  clearTimeout(_settingsSaveTimer);
  _settingsSaveTimer = setTimeout(async () => {
    try {
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          excluded_spell_ids: [...EXCLUDED_SPELL_IDS],
          enabled_spell_ids: [...ENABLED_SPELL_IDS],
        }),
      });
    } catch (e) {
      status(`Settings save failed: ${e.message}`, true);
    }
  }, 400);
}

// Spell is currently INCLUDED in notes if:
//   - default-included (healer) AND not user-excluded, OR
//   - default-excluded (raid)   AND user-enabled.
function spellIncluded(s) {
  if (s.default_excluded) return ENABLED_SPELL_IDS.has(s.spell_id);
  return !EXCLUDED_SPELL_IDS.has(s.spell_id);
}

function setSpellIncluded(s, on) {
  if (s.default_excluded) {
    if (on) ENABLED_SPELL_IDS.add(s.spell_id);
    else ENABLED_SPELL_IDS.delete(s.spell_id);
  } else {
    if (on) EXCLUDED_SPELL_IDS.delete(s.spell_id);
    else EXCLUDED_SPELL_IDS.add(s.spell_id);
  }
}

// Concrete spell-IDs to omit from a note, computed from TRACKED_SPELLS +
// user state. Sent to /api/note on every fetch.
function effectiveExcludedSpellIds() {
  return TRACKED_SPELLS.filter((s) => !spellIncluded(s)).map((s) => s.spell_id);
}

async function loadTrackedSpells() {
  try {
    const data = await api("/api/tracked_spells");
    TRACKED_SPELLS = data.spells;
  } catch (e) {
    status(`Couldn't load tracked spells: ${e.message}`, true);
    TRACKED_SPELLS = [];
  }
}

// Currently active spell-filter class tab. Persists for the session;
// reset when the tab list is rebuilt and the previous selection is gone.
let _activeSpellClass = null;

function renderSpellFilter() {
  const tabsEl = $("spell-class-tabs");
  const container = $("spell-filter-list");
  if (TRACKED_SPELLS.length === 0) {
    tabsEl.innerHTML = "";
    container.innerHTML = `<small class="muted">No tracked spells loaded.</small>`;
    return;
  }
  // Every class with any tracked spell — healers always present; DPS-only
  // classes show up too so the user can opt their raid CDs into the note.
  const classes = [...new Set(TRACKED_SPELLS.map((s) => s.wow_class))].sort();
  if (classes.length === 0) {
    tabsEl.innerHTML = "";
    container.innerHTML = `<small class="muted">Nothing tracked right now.</small>`;
    return;
  }
  if (!classes.includes(_activeSpellClass)) {
    _activeSpellClass = classes[0];
  }

  tabsEl.innerHTML = classes.map((cls) => {
    const active = cls === _activeSpellClass ? "active" : "";
    return `<button class="class-tab-btn ${active} class-${cls.replace(/\s/g, "")}"
            type="button" data-class="${cls}">${cls}</button>`;
  }).join("");
  tabsEl.querySelectorAll(".class-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      _activeSpellClass = btn.dataset.class;
      renderSpellFilter();
    });
  });

  renderSpellFilterContent(_activeSpellClass);
}

function renderSpellFilterContent(wowClass) {
  const container = $("spell-filter-list");
  const spells = TRACKED_SPELLS.filter((s) => s.wow_class === wowClass);
  // Group by spec within this class. Healer entries have real specs;
  // class-wide raid entries use spec === "(any)".
  const groups = new Map();
  for (const s of spells) {
    if (!groups.has(s.spec)) groups.set(s.spec, []);
    groups.get(s.spec).push(s);
  }
  const renderRows = (list) =>
    list.map((s) => {
      const checked = spellIncluded(s) ? "checked" : "";
      return `
        <label class="spell-filter-row">
          <input type="checkbox" class="spell-filter-cb"
                 data-spell-id="${s.spell_id}" ${checked}>
          <span>${s.label}</span>
          <small class="muted">(${s.category})</small>
        </label>`;
    }).join("");
  container.innerHTML = [...groups.entries()].map(([spec, list]) => {
    const heading = spec === "(any)" ? "Class-wide raid CDs" : spec;
    return `
      <div class="spell-filter-group class-${wowClass.replace(/\s/g, "")}">
        <h6 class="spell-filter-group-head">${heading}</h6>
        <div class="spell-filter-group-rows">${renderRows(list)}</div>
      </div>`;
  }).join("");

  container.querySelectorAll(".spell-filter-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = parseInt(cb.dataset.spellId, 10);
      const spell = TRACKED_SPELLS.find((s) => s.spell_id === id);
      if (spell) setSpellIncluded(spell, cb.checked);
      saveSettings();
      if (CURRENT_NOTE) debouncedRegenerateNote();
    });
  });
}

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
const ALL_CLASSES = Object.keys(CLASS_COLORS_HEX).sort();

// Roster matches available for this player. Healers (with spec) get
// same-spec matches only so Priests don't get cross-mapped onto a Disc.
// Raid-CD players (no spec) match by class only.
function rosterOptionsFor(p) {
  if (p.spec) {
    return ROSTER.filter(
      (m) => m.wow_class === p.wow_class && m.specs.includes(p.spec)
    );
  }
  return ROSTER.filter((m) => m.wow_class === p.wow_class);
}

function _remapCellHtml(p, { allowIgnore = false } = {}) {
  const opts = rosterOptionsFor(p);
  const currentOverride = LOG_OVERRIDES.get(p.name);
  const currentClass = LOG_CLASS_OVERRIDES.get(p.name);
  const ignored = LOG_IGNORED_PLAYERS.has(p.name);
  const color = CLASS_COLORS_HEX[p.wow_class] || "";
  const mkOpt = (m) => {
    const sel = !ignored && currentOverride === m.name ? "selected" : "";
    return `<option value="roster:${m.name}" style="color: ${color}" ${sel}>${m.name}</option>`;
  };
  const keepSel = !ignored && (currentOverride === undefined || currentOverride === p.name) ? "selected" : "";
  // Healer rows offer healing classes for substitution (purpose-matched
  // healer spells); raid-CD rows offer every class (raid CDs cut across
  // both healer and non-healer classes).
  const classChoices = p.spec ? CLASSES_WITH_HEALERS : ALL_CLASSES;
  const classOpts = classChoices.map(
    (c) => `<option value="${c}" style="color: ${CLASS_COLORS_HEX[c] || ''}" ${currentClass === c ? "selected" : ""}>${c}</option>`
  ).join("");
  const classOverrideChecked = currentClass ? "checked" : "";
  const specLabel = p.spec ? `<small class="muted">(${p.spec})</small>` : "";
  const ignoreOpt = allowIgnore
    ? `<option value="ignore" ${ignored ? "selected" : ""}>Ignore (drop casts)</option>`
    : "";
  return `
    <div class="remap-cell class-${p.wow_class.replace(/\s+/g, '')}">
      <div class="remap-cell-head">
        <span class="remap-name">${p.name}</span>
        ${specLabel}
      </div>
      <div class="remap-cell-arrow">&darr;</div>
      <select class="remap-select" data-wcl-name="${p.name}">
        <option value="keep" ${keepSel}>(keep ${p.name})</option>
        ${opts.length ? `<optgroup label="Roster">${opts.map(mkOpt).join("")}</optgroup>` : ""}
        <option value="manual">Manual...</option>
        ${ignoreOpt}
      </select>
      <div class="remap-manual-block" data-wcl-name="${p.name}" hidden>
        <input type="text" class="remap-manual-input" data-wcl-name="${p.name}"
               placeholder="Custom name" value="${currentOverride && !ROSTER.some((m) => m.name === currentOverride) ? currentOverride : ""}">
        <label class="remap-class-toggle">
          <input type="checkbox" class="remap-class-cb" data-wcl-name="${p.name}" ${classOverrideChecked}>
          Class Override
        </label>
        <select class="remap-class-select" data-wcl-name="${p.name}" ${currentClass ? "" : "hidden"}>
          ${classOpts}
        </select>
      </div>
    </div>
  `;
}

function renderNoteRemap(healers, raidCdPlayers) {
  const container = $("note-remap");
  const hasHealers = healers && healers.length > 0;
  const hasRaid = raidCdPlayers && raidCdPlayers.length > 0;
  if (!hasHealers && !hasRaid) {
    container.innerHTML = `<small class="muted">No players to remap.</small>`;
    return;
  }
  const healerGrid = hasHealers
    ? `<div class="remap-grid">${healers.map((p) => _remapCellHtml(p)).join("")}</div>`
    : "";
  const raidGrid = hasRaid
    ? `<div class="remap-section-label">Raid CDs</div>
       <div class="remap-grid">${raidCdPlayers.map((p) => _remapCellHtml(p, { allowIgnore: true })).join("")}</div>`
    : "";
  container.innerHTML = healerGrid + raidGrid;

  container.querySelectorAll(".remap-select").forEach((sel) => {
    // If the current override doesn't match any select option, or if a class
    // override is set for this healer, switch to manual mode so the panel
    // shows. Ignored players already have their select pre-selected to
    // "ignore" by the template, so skip the manual-mode promotion for them.
    const wclName = sel.dataset.wclName;
    if (!LOG_IGNORED_PLAYERS.has(wclName)) {
      const cur = LOG_OVERRIDES.get(wclName);
      const hasClassOverride = LOG_CLASS_OVERRIDES.has(wclName);
      const customName = cur !== undefined && cur !== wclName && !ROSTER.some((m) => m.name === cur);
      if (customName || hasClassOverride) {
        sel.value = "manual";
        const block = sel.closest(".remap-cell").querySelector(".remap-manual-block");
        if (block) block.hidden = false;
      }
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
  container.querySelectorAll(".remap-class-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const wclName = cb.dataset.wclName;
      const classSel = cb.closest(".remap-manual-block").querySelector(".remap-class-select");
      if (cb.checked) {
        classSel.hidden = false;
        LOG_CLASS_OVERRIDES.set(wclName, classSel.value);
      } else {
        classSel.hidden = true;
        LOG_CLASS_OVERRIDES.delete(wclName);
      }
      debouncedRegenerateNote();
    });
  });
  container.querySelectorAll(".remap-class-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const wclName = sel.dataset.wclName;
      if (LOG_CLASS_OVERRIDES.has(wclName)) {
        LOG_CLASS_OVERRIDES.set(wclName, sel.value);
        debouncedRegenerateNote();
      }
    });
  });
}

function onRemapChange(sel) {
  const wclName = sel.dataset.wclName;
  const block = sel.closest(".remap-cell").querySelector(".remap-manual-block");
  const manualInput = block.querySelector(".remap-manual-input");
  const collapseBlock = () => {
    block.hidden = true;
    manualInput.value = "";
    const cb = block.querySelector(".remap-class-cb");
    const classSel = block.querySelector(".remap-class-select");
    if (cb) cb.checked = false;
    if (classSel) classSel.hidden = true;
  };
  // Anything other than "ignore" means the player should appear in the note
  // — clear any prior ignore flag.
  if (sel.value !== "ignore") LOG_IGNORED_PLAYERS.delete(wclName);
  if (sel.value === "keep") {
    LOG_OVERRIDES.delete(wclName);
    LOG_CLASS_OVERRIDES.delete(wclName);
    collapseBlock();
  } else if (sel.value === "manual") {
    block.hidden = false;
    manualInput.focus();
    // Don't update overrides until the user types something / toggles class.
  } else if (sel.value.startsWith("roster:")) {
    const rosterName = sel.value.slice("roster:".length);
    LOG_OVERRIDES.set(wclName, rosterName);
    LOG_CLASS_OVERRIDES.delete(wclName);
    collapseBlock();
  } else if (sel.value === "ignore") {
    LOG_IGNORED_PLAYERS.add(wclName);
    LOG_OVERRIDES.delete(wclName);
    LOG_CLASS_OVERRIDES.delete(wclName);
    collapseBlock();
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

// Same regex pair as the backend's _extract_report_code / _extract_fight_id.
function parseLogUrl(input) {
  const code =
    input.match(/\/reports\/([A-Za-z0-9]+)/)?.[1] ||
    input.match(/^([A-Za-z0-9]{8,32})$/)?.[1];
  const fight = parseInt(input.match(/[#?&]fight=(\d+)/)?.[1] || "", 10);
  return { code, fight: Number.isNaN(fight) ? null : fight };
}

async function doDirectLog() {
  const raw = $("direct-input").value.trim();
  if (!raw) {
    status("Paste a WCL log URL first.", true);
    return;
  }
  const { code, fight } = parseLogUrl(raw);
  if (!code) {
    $("direct-status").textContent = "Couldn't find a WCL report code in that URL.";
    return;
  }
  if (!fight) {
    $("direct-status").textContent =
      "URL is missing #fight=N — direct lookup needs a specific fight.";
    return;
  }
  const btn = $("direct-btn");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  $("direct-status").textContent = `Loading ${code}#fight=${fight}…`;
  try {
    await loadNote(code, fight);
    $("direct-status").textContent = `Loaded ${code}#fight=${fight}.`;
  } catch (e) {
    $("direct-status").textContent = `Failed: ${e.message}`;
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

function fmtHps(hps) {
  if (hps == null) return "—";
  if (hps >= 1_000_000) return (hps / 1_000_000).toFixed(2) + "M";
  if (hps >= 1_000)     return (hps / 1_000).toFixed(1) + "k";
  return hps.toFixed(0);
}

// Active-time % coloring: < 80% = red (probably a death), 80-95 = yellow,
// >= 95 = green.
function activeClass(pct) {
  if (pct == null) return "";
  if (pct < 80) return "active-low";
  if (pct < 95) return "active-mid";
  return "active-high";
}

function percentileClass(p) {
  if (p == null) return "";
  if (p >= 95) return "pct-95";
  if (p >= 75) return "pct-75";
  if (p >= 50) return "pct-50";
  if (p >= 25) return "pct-25";
  return "pct-0";
}

// Absolute-rank colour bands (smaller = better). Mirrors WCL's leaderboard
// tinting — rank 1 gets the brightest, then top-10, top-100, top-1000, rest.
function rankClass(rank) {
  if (rank == null) return "";
  if (rank === 1) return "pct-95";
  if (rank <= 10) return "pct-75";
  if (rank <= 100) return "pct-50";
  if (rank <= 1000) return "pct-25";
  return "pct-0";
}

// Wire sort behaviour onto a table's headers. Each <th> sorts the tbody by
// its column on click; numeric ordering uses data-sort if present, otherwise
// parseFloat of textContent (falls back to lexicographic). Headers tagged
// with data-no-sort (e.g. blank "actions" columns) are skipped. Pass
// `pairedRowSelector` to keep detail rows attached to their summary row
// (used by results-table where each match-row is followed by a hidden
// match-detail-row that must stay adjacent after sorting).
function makeSortable(table, { pairedRowSelector } = {}) {
  const headers = Array.from(table.tHead?.rows[0]?.cells || []);
  const tbody = table.tBodies[0];
  if (!tbody || headers.length === 0) return;
  let state = { col: -1, dir: 1 };
  const cellSortValue = (tr, col) => {
    const td = tr.cells[col];
    if (!td) return "";
    if (td.dataset.sort !== undefined) return td.dataset.sort;
    return td.textContent.trim();
  };
  headers.forEach((th, col) => {
    if (th.dataset.noSort !== undefined || !th.textContent.trim()) return;
    th.classList.add("sortable");
    th.addEventListener("click", () => {
      const dir = state.col === col ? -state.dir : 1;
      state = { col, dir };
      // Build [summary-row, optional detail-row] pairs so we don't tear them
      // apart when sorting.
      const allRows = Array.from(tbody.rows);
      const pairs = [];
      for (let i = 0; i < allRows.length; i++) {
        const r = allRows[i];
        if (pairedRowSelector && r.matches(pairedRowSelector)) continue;
        const next = allRows[i + 1];
        if (pairedRowSelector && next && next.matches(pairedRowSelector)) {
          pairs.push([r, next]); i++;
        } else {
          pairs.push([r]);
        }
      }
      pairs.sort(([a], [b]) => {
        const av = cellSortValue(a, col);
        const bv = cellSortValue(b, col);
        const an = parseFloat(av), bn = parseFloat(bv);
        const numeric = !isNaN(an) && !isNaN(bn);
        const cmp = numeric ? (an - bn) : String(av).localeCompare(String(bv));
        return cmp * dir;
      });
      headers.forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
      th.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
      const frag = document.createDocumentFragment();
      for (const pair of pairs) for (const r of pair) frag.appendChild(r);
      tbody.appendChild(frag);
    });
  });
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
    include_extra_healers: $("include-extra-input").checked,
    bypass_cache: $("bypass-cache-input").checked,
  };
  // Capture zone at submit time so the WCL-link URL stays in sync with the
  // results — even if the user changes the dropdown after clicking.
  const zoneId = parseInt($("zone-select").value, 10);

  const btn = $("discover-btn");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  try {
    const data = await api("/api/discover", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const targetN = healers.reduce((n, h) => n + h.count, 0);
    renderResults(data, targetN, {
      zoneId,
      encounterId: payload.encounter_id,
      difficulty: payload.difficulty,
    });
  } catch (e) {
    status(`Discover failed: ${e.message}`, true);
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
    // Fresh Call is per-click: uncheck after submit so it always has to
    // be opted into again.
    $("bypass-cache-input").checked = false;
  }
}

function renderResults(data, targetN, ctx = {}) {
  const panel = $("results-panel");
  const content = $("results-content");
  const meta = $("results-meta");
  const count = `${data.matches.length} match${data.matches.length === 1 ? "" : "es"}`;
  const wclUrl = ctx.zoneId && ctx.encounterId && ctx.difficulty
    ? `https://www.warcraftlogs.com/zone/rankings/${ctx.zoneId}#boss=${ctx.encounterId}&difficulty=${ctx.difficulty}&class=Healer&filter=${encodeURIComponent(data.server_filter)}`
    : null;
  // Escape for the visible filter string — the link wrapping makes raw
  // HTML interpolation unsafe even though server_filter is server-generated.
  const filterTxt = escapeHtml(data.server_filter);
  meta.innerHTML = wclUrl
    ? `${count} &middot; filter: <a href="${wclUrl}" target="_blank" rel="noopener">${filterTxt}</a>`
    : `${count} &middot; filter: ${filterTxt}`;

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
    const rows = groups[hc].map((m) => {
      const rank = m.guild_rank != null ? m.guild_rank : "—";
      const hps = m.total_hps != null ? fmtHps(m.total_hps) : "—";
      const rankPct = m.avg_rank_percent != null ? m.avg_rank_percent.toFixed(0) : "—";
      const avgAt  = m.avg_active_pct  != null ? m.avg_active_pct.toFixed(1) + "%" : "—";
      const minAt  = m.min_active_pct  != null ? m.min_active_pct.toFixed(1) + "%" : "—";
      // data-sort: cells where the *displayed* string doesn't sort right.
      // mm:ss → ms; formatted HPS (177.4k) → raw number; "—" → +Infinity so
      // missing values sink to the bottom on ascending sort.
      const sortRank = m.guild_rank != null ? m.guild_rank : Infinity;
      const sortHps = m.total_hps != null ? m.total_hps : -Infinity;
      const sortRankPct = m.avg_rank_percent != null ? m.avg_rank_percent : -Infinity;
      const sortAvgAt = m.avg_active_pct != null ? m.avg_active_pct : -Infinity;
      const sortMinAt = m.min_active_pct != null ? m.min_active_pct : -Infinity;
      return `
      <tr class="match-row" data-report="${m.report_code}" data-fight="${m.fight_id}" data-index="${m.index}">
        <td class="num ${rankClass(m.guild_rank)}" data-sort="${sortRank}">${rank}</td>
        <td>${m.guild ?? "?"}</td>
        <td>${m.region ?? "?"}</td>
        <td class="num" data-sort="${m.duration_ms}">${fmtMmss(m.duration_ms)}</td>
        <td class="num" data-sort="${sortHps}">${hps}</td>
        <td class="num ${percentileClass(m.avg_rank_percent)}" data-sort="${sortRankPct}">${rankPct}</td>
        <td class="num ${activeClass(m.avg_active_pct)}" data-sort="${sortAvgAt}">${avgAt}</td>
        <td class="num ${activeClass(m.min_active_pct)}" data-sort="${sortMinAt}">${minAt}</td>
        <td><a href="${m.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">WCL ↗</a></td>
        <td><button class="pick-btn outline" type="button">Generate note</button></td>
      </tr>
      <tr class="match-detail-row" data-for-index="${m.index}" hidden>
        <td colspan="10"><div class="match-detail">Loading…</div></td>
      </tr>`;
    }).join("");

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
            <th class="num">Rank</th>
            <th>Guild</th>
            <th>Region</th>
            <th class="num">Dur</th>
            <th class="num">Total HPS</th>
            <th class="num">Rank %</th>
            <th class="num">Avg Active</th>
            <th class="num">Min Active</th>
            <th data-no-sort></th><th data-no-sort></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </details>
    `;
  }).join("");

  content.querySelectorAll(".results-table").forEach((t) => {
    makeSortable(t, { pairedRowSelector: ".match-detail-row" });
  });

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
  const rows = data.healers.map((h) => {
    const color = CLASS_COLORS_HEX[h.wow_class] || "";
    const sortHps = h.hps != null ? h.hps : -Infinity;
    const sortParse = h.parse_percent != null ? h.parse_percent : -Infinity;
    const sortAct = h.active_time_pct != null ? h.active_time_pct : -Infinity;
    return `
    <tr class="class-${h.wow_class.replace(/\s+/g, '')}">
      <td><span style="color: ${color} !important; font-weight: 600;">${h.name}</span></td>
      <td>${h.spec}</td>
      <td class="num" data-sort="${sortHps}">${h.hps != null ? fmtHps(h.hps) : "—"}</td>
      <td class="num ${percentileClass(h.parse_percent)}" data-sort="${sortParse}">${h.parse_percent != null ? h.parse_percent.toFixed(0) : "—"}</td>
      <td class="num ${activeClass(h.active_time_pct)}" data-sort="${sortAct}">${h.active_time_pct != null ? h.active_time_pct.toFixed(1) + "%" : "—"}</td>
    </tr>`;
  }).join("");
  container.innerHTML = `
    <table class="detail-table">
      <thead><tr>
        <th>Healer</th><th>Spec</th>
        <th class="num">HPS</th>
        <th class="num">Parse %</th>
        <th class="num">Active Time</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  makeSortable(container.querySelector(".detail-table"));
}

// ---- Note ---------------------------------------------------------------
let CURRENT_NOTE = null;   // {report_code, fight_id}
let CURRENT_HEALERS = null; // [{name, wow_class, spec, rank_percent}] from log_detail
// Non-healer players whose raid CDs appear in the latest timeline. Populated
// from the /api/note response on each fetch. Used to drive a second remap
// section so DPS/tanks can be renamed / class-overridden too.
let CURRENT_RAID_CD_PLAYERS = [];

async function loadNote(reportCode, fightId) {
  CURRENT_NOTE = { report_code: reportCode, fight_id: fightId };
  // New log → fresh class overrides + ignore list (name overrides are
  // reset by autoAssignFromRoster below) + reset raid CD players so a
  // stale set isn't briefly visible while the new note loads.
  LOG_CLASS_OVERRIDES.clear();
  LOG_IGNORED_PLAYERS.clear();
  CURRENT_RAID_CD_PLAYERS = [];
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
  renderNoteRemap(CURRENT_HEALERS, CURRENT_RAID_CD_PLAYERS);
  await fetchAndRenderNote($("style-select").value);
  const panel = $("note-panel");
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function sameRaidCdSet(a, b) {
  if (a.length !== b.length) return false;
  const key = (p) => `${p.name} ${p.wow_class}`;
  const aKeys = new Set(a.map(key));
  return b.every((p) => aKeys.has(key(p)));
}

async function fetchAndRenderNote(style) {
  if (!CURRENT_NOTE) return;
  $("note-output").value = "Loading…";
  const overrides = Object.fromEntries(LOG_OVERRIDES);
  const classOverrides = Object.fromEntries(LOG_CLASS_OVERRIDES);
  const excluded = effectiveExcludedSpellIds();
  try {
    const data = await api("/api/note", {
      method: "POST",
      body: JSON.stringify({
        ...CURRENT_NOTE, style,
        name_overrides: overrides,
        class_overrides: classOverrides,
        excluded_spell_ids: excluded,
        ignored_player_names: Array.from(LOG_IGNORED_PLAYERS),
      }),
    });
    $("note-meta").textContent =
      `${fmtMmss(data.duration_ms)} · ${data.report_code}#fight=${data.fight_id}`;
    $("note-output").value = data.note_text;
    renderNotePreview(data.timeline);
    // Re-render the remap UI only if the raid-CD player set actually
    // changed — avoids blowing away input focus while the user is typing
    // (every keystroke debounces a note fetch).
    const newRaidCd = data.raid_cd_players || [];
    if (!sameRaidCdSet(CURRENT_RAID_CD_PLAYERS, newRaidCd)) {
      CURRENT_RAID_CD_PLAYERS = newRaidCd;
      renderNoteRemap(CURRENT_HEALERS, CURRENT_RAID_CD_PLAYERS);
    }
  } catch (e) {
    $("note-output").value = "";
    status(`Note failed: ${e.message}`, true);
  }
}

function renderNotePreview(timeline) {
  const container = $("note-preview");
  if (!timeline || timeline.length === 0) {
    container.innerHTML = `<small class="muted">No cooldowns to preview.</small>`;
    return;
  }
  container.innerHTML = timeline.map((ev) => {
    const color = CLASS_COLORS_HEX[ev.healer_class] || "#ffffff";
    return `
      <div class="preview-line">
        <span class="preview-time">${fmtMmss(ev.time_ms)}</span>
        <span class="preview-name" style="color: ${color}">${ev.healer_name}</span>
        <span class="preview-spell">${ev.spell_label}</span>
      </div>
    `;
  }).join("");
}

function activateNoteSubtab(name) {
  document.querySelectorAll(".subtab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.subtab === name);
  });
  document.querySelectorAll(".subtab-panel").forEach((p) => {
    p.hidden = p.dataset.subtabPanel !== name;
  });
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
  // Tabs (top-level Search / Settings)
  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => activateTab(b.dataset.tab))
  );
  // Sub-tabs inside the Note panel (Note / Preview)
  document.querySelectorAll(".subtab-btn").forEach((b) =>
    b.addEventListener("click", () => activateNoteSubtab(b.dataset.subtab))
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
  });

  // Direct Log tab
  $("direct-btn").addEventListener("click", doDirectLog);
  $("direct-form").addEventListener("submit", (e) => {
    e.preventDefault();
    doDirectLog();
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
      (async () => {
        await loadTrackedSpells();
        await loadSettingsFromServer();
        renderSpellFilter();
      })(),
    ]);
  } catch (e) {
    status(`Init failed: ${e.message}`, true, 0);
  }
}

boot();
