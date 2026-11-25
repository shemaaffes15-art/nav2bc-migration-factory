/* NAV2BC Migration Factory — interface (vanilla JS, aucune dépendance). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => (n ?? 0).toLocaleString("fr-FR");
const store = {
  get(k, d = null) { try { return JSON.parse(localStorage.getItem("n2b." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("n2b." + k, JSON.stringify(v)); } catch { /* stockage indisponible */ } },
};

const S = { projects: [], projectId: store.get("project"), user: store.get("user", ""), system: null, timers: [], stepId: null };

async function api(path, opts = {}) {
  const o = { ...opts, headers: { ...(opts.headers || {}) } };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(path, o);
  const t = await r.text();
  let d; try { d = t ? JSON.parse(t) : null; } catch { d = t; }
  if (!r.ok) {
    const msg = d && d.detail ? (Array.isArray(d.detail) ? d.detail.map(x => x.msg).join(" ; ") : d.detail) : r.statusText;
    throw new Error(msg);
  }
  return d;
}

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 6000 : 3000);
}
const fail = e => toast(e.message || String(e), true);

function modal(html, wide = false) {
  const m = $("#modal"), b = $(".mbox", m);
  b.className = "mbox" + (wide ? " wide" : ""); b.innerHTML = html; m.hidden = false;
  const f = $("input, textarea, select, button", b); if (f) f.focus();
  return b;
}
function closeModal() { $("#modal").hidden = true; }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.dataset.close !== undefined) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

function askUser(reason) {
  return new Promise(resolve => {
    const b = modal(`<h2>Validation nominative</h2><p class="muted">${esc(reason)}</p>
      <form class="form" id="uf"><label>Votre nom<input name="u" required value="${esc(S.user)}" autocomplete="name"></label>
      <div class="mact"><button type="button" class="btn" data-close>Annuler</button><button class="btn btnp">Continuer</button></div></form>`);
    $("#uf", b).onsubmit = e => { e.preventDefault(); const u = e.target.u.value.trim(); if (!u) return; S.user = u; store.set("user", u); renderUser(); closeModal(); resolve(u); };
    $("[data-close]", b).onclick = () => { closeModal(); resolve(null); };
  });
}
function renderUser() { $("#userBtn").textContent = S.user ? "👤 " + S.user : "👤 Indiquer mon nom"; }
$("#userBtn").onclick = () => askUser("Ce nom est enregistré avec chaque validation et chaque exécution.");

const STATUS = { todo: ["À faire", ""], running: ["En cours", "info"], done: ["Terminé", "ok"], failed: ["Échec", "err"] };
const statusTag = s => `<span class="tag ${STATUS[s]?.[1] || ""}">${STATUS[s]?.[0] || esc(s)}</span>`;
const EVID = { documented: ["Documenté", "ok"], to_verify: ["À vérifier", "warn"], factory_recommendation: ["Recommandé", "info"], factory: ["Recommandé", "info"] };
const evTag = e => `<span class="tag ${EVID[e]?.[1] || ""}">${EVID[e]?.[0] || esc(e)}</span>`;
const KIND = { powershell: "PowerShell", internal: "Automatique", manual: "Manuel" };
const ORIGIN = { custom: "Client", standard: "Standard", standard_modified: "Standard modifié", addon: "Add-on" };
const project = () => S.projects.find(p => p.id === S.projectId);

/* ------------------------------------------------------------------ routing */
const VIEWS = {};
function stopTimers() { S.timers.forEach(clearInterval); S.timers = []; }
async function route() {
  stopTimers();
  const [, name = "dashboard", arg] = location.hash.split("?")[0].split("/");
  $$("#nav .nv").forEach(a => a.classList.toggle("on", a.dataset.v === name));
  const v = $("#view");
  v.innerHTML = `<div class="muted">Chargement…</div>`;
  const needsProject = !["dashboard", "errors", "knowledge"].includes(name) && !(name === "project" && arg === "new");
  if (needsProject && !project()) {
    v.innerHTML = `<h1>Aucun projet ouvert</h1><div class="card empty">Créez un projet pour commencer.<br><a class="btn btnp" href="#/project/new">Nouveau projet</a></div>`;
    return;
  }
  try { await (VIEWS[name] || VIEWS.dashboard)(v, arg); } catch (e) { v.innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
  v.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

async function loadProjects() {
  S.projects = await api("/api/projects");
  if (!project() && S.projects.length) S.projectId = S.projects[0].id;
  if (!S.projects.length) S.projectId = null;
  store.set("project", S.projectId);
  const sel = $("#projectSelect");
  sel.innerHTML = S.projects.length ? S.projects.map(p => `<option value="${p.id}" ${p.id === S.projectId ? "selected" : ""}>${esc(p.name)}${p.client ? " — " + esc(p.client) : ""}</option>`).join("")
    : `<option value="">— aucun projet —</option>`;
  const p = project();
  $("#pathBadge").textContent = p ? `${p.source_version} → BC ${p.target_online ? "Online " : ""}${p.target_version}` : "";
}
$("#projectSelect").onchange = e => { S.projectId = +e.target.value; store.set("project", S.projectId); loadProjects().then(route); };

/* ------------------------------------------------------------------ dashboard */
VIEWS.dashboard = async v => {
  const d = await api("/api/dashboard");
  const done = d.projects.reduce((a, p) => a + p.progress.done, 0), total = d.projects.reduce((a, p) => a + p.progress.total, 0);
  v.innerHTML = `<div class="between"><h1>Tableau de bord</h1><a class="btn btnp" href="#/project/new">+ Nouveau projet</a></div>
    <div class="kpis">
      <div class="kpi"><div class="k">Projets</div><div class="v">${d.projects.length}</div></div>
      <div class="kpi"><div class="k">Étapes terminées</div><div class="v">${done}<span class="muted small"> / ${total}</span></div></div>
      <div class="kpi"><div class="k">Erreurs avec solution connue</div><div class="v">${d.known_solutions}</div><div class="d">réutilisées automatiquement</div></div>
      <div class="kpi"><div class="k">Fiches du référentiel</div><div class="v">${d.knowledge_entries}</div></div>
    </div>
    ${d.projects.length ? `<div class="grid2">${d.projects.map(p => {
      const pct = p.progress.total ? Math.round(100 * p.progress.done / p.progress.total) : 0;
      return `<div class="card pcard" data-id="${p.id}" tabindex="0" role="button" aria-label="Ouvrir ${esc(p.name)}">
        <div class="between"><div><h2 style="margin:0">${esc(p.name)}</h2><div class="muted small">${esc(p.client || "")}</div></div>
        <span class="tag teal">${esc(p.source_version)} → BC ${p.target_online ? "Online " : ""}${p.target_version}</span></div>
        <div class="bar" aria-label="Avancement ${pct} %"><span style="width:${pct}%"></span></div>
        <div class="between small"><span>${p.progress.done} / ${p.progress.total} étapes · ${pct} %</span><span class="muted">${esc(p.path)}</span></div>
        <div class="small">${p.current_step ? `Prochaine étape : <b>${esc(p.current_step.title)}</b> <span class="muted">(${esc(p.current_step.phase)})</span>` : `<span class="tag ok">Migration terminée</span>`}</div>
        <div class="flex small muted"><span>${p.object_count != null ? fmt(p.object_count) + " objets inventoriés" : "Inventaire à faire"}</span>·<span>${p.error_count} signature(s) d'erreur</span></div>
      </div>`; }).join("")}</div>`
    : `<div class="card empty">Aucun projet pour l'instant.<br><a class="btn btnp" href="#/project/new">Créer le premier projet</a></div>`}`;
  $$(".pcard", v).forEach(c => {
    const open = () => { S.projectId = +c.dataset.id; store.set("project", S.projectId); loadProjects().then(() => (location.hash = "#/execution")); };
    c.onclick = open; c.onkeydown = e => { if (e.key === "Enter") open(); };
  });
};

/* ------------------------------------------------------------------ project & path */
const SOURCES = ["NAV2009R2", "NAV2013", "NAV2013R2", "NAV2015", "NAV2016", "NAV2017", "NAV2018", "BC13", "BC14", ...Array.from({ length: 15 }, (_, i) => "BC" + (15 + i))];
function pathHtml(path) {
  const hops = path.hops;
  let html = `<div class="path"><div class="node src"><div class="nt">${esc(path.source)}</div><div class="nd">Base d'origine</div></div>`;
  hops.forEach(h => {
    html += `<div class="arrow ${h.kind === "cloud_migration" ? "cloud" : ""}"><span class="lab">${h.kind === "cloud_migration" ? "Migration cloud" : h.kind === "conversion" ? "Conversion" : "Mise à niveau"}${h.requirement === "recommended" ? " (recommandée)" : ""}</span>
      <span class="ev">${evTag(h.evidence)}</span></div>
      <div class="node ${h.kind === "cloud_migration" ? "dst" : ""}"><div class="nt">${esc(h.to_version)}</div><div class="nd">${esc(h.description)}</div>
      ${h.documentation_url ? `<div class="nd"><a href="${esc(h.documentation_url)}" target="_blank" rel="noopener">Source Microsoft ↗</a></div>` : ""}</div>`;
  });
  return html + `</div><div class="small muted">${esc(path.summary)}${path.to_verify.length ? ` · <span class="tag warn">${path.to_verify.length} saut(s) à vérifier</span>` : ""}</div>`;
}
VIEWS.project = async (v, arg) => {
  const isNew = arg === "new";
  const p = isNew ? { name: "", client: "", source_version: "NAV2016", target_version: 29, target_online: true, scenario: "B" } : await api(`/api/projects/${S.projectId}`);
  v.innerHTML = `<h1>${isNew ? "Nouveau projet" : "Projet & chemin de migration"}</h1>
    <p class="sub">La version source détermine les sauts obligatoires. Le plan d'exécution est généré à partir de ce chemin.</p>
    <div class="grid2">
      <div class="card"><h2>Identification</h2>
        <form class="form" id="pf">
          <label>Nom du projet<input name="name" required value="${esc(p.name)}" placeholder="Distribution Médina"></label>
          <label>Client<input name="client" value="${esc(p.client || "")}"></label>
          <div class="row">
            <label>Version NAV / BC source<select name="source_version">${SOURCES.map(s => `<option ${s === p.source_version ? "selected" : ""}>${s}</option>`).join("")}</select></label>
            <label>Version BC cible<select name="target_version">${[25, 26, 27, 28, 29, 30].map(n => `<option ${n === p.target_version ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          </div>
          <label class="chk"><input type="checkbox" name="target_online" ${p.target_online ? "checked" : ""}> Cible Business Central Online (migration cloud en fin de chemin)</label>
          <div class="flex"><button class="btn btnp">${isNew ? "Créer le projet" : "Enregistrer"}</button>${isNew ? "" : `<a class="btn" href="#/environment">Environnement →</a>`}</div>
        </form>
      </div>
      <div class="card"><h2>Ce qui change avec ce chemin</h2><div id="pathInfo" class="small"></div></div>
    </div>
    <div class="card"><h2>Chemin de migration</h2><div id="pathBox"></div></div>`;
  const f = $("#pf", v);
  const preview = async () => {
    try {
      const path = await api(`/api/upgrade-path?source=${encodeURIComponent(f.source_version.value)}&target=${f.target_version.value}&online=${f.target_online.checked}`);
      $("#pathBox", v).innerHTML = pathHtml(path);
      $("#pathInfo", v).innerHTML = `<dl class="dl"><dt>Mises à niveau on-premises</dt><dd>${path.upgrade_count}</dd>
        <dt>Migration cloud</dt><dd>${path.online ? "Oui (runtime d'intégration)" : "Non"}</dd>
        <dt>Conversion C/AL → AL</dt><dd>${/NAV|BC 1[34]/.test(path.source) && f.target_version.value >= 15 ? "Oui, dans BC 14 (Txt2AL)" : "Non"}</dd>
        <dt>Sauts à vérifier</dt><dd>${path.to_verify.length || "Aucun"}</dd></dl>
        <p class="muted">Chaque saut garde sa source. Les chemins supportés changent à chaque version : relire la page Microsoft avant de valider le plan.</p>`;
    } catch (e) { $("#pathBox", v).innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
  };
  f.onchange = preview; preview();
  f.onsubmit = async e => {
    e.preventDefault();
    const body = { name: f.name.value.trim(), client: f.client.value.trim() || null, source_version: f.source_version.value, target_version: +f.target_version.value, target_online: f.target_online.checked, scenario: f.target_online.checked ? "B" : "A" };
    try {
      const r = isNew ? await api("/api/projects", { method: "POST", json: body }) : await api(`/api/projects/${S.projectId}`, { method: "PUT", json: body });
      S.projectId = r.id; store.set("project", r.id); await loadProjects();
      toast(isNew ? "Projet créé. Renseignez maintenant l'environnement." : "Projet enregistré.");
      location.hash = isNew ? "#/environment" : "#/project";
    } catch (err) { fail(err); }
  };
};

/* ------------------------------------------------------------------ environment */
VIEWS.environment = async v => {
  const d = await api(`/api/projects/${S.projectId}/environment`);
  const groups = [...new Set(d.fields.map(f => f.group))];
  v.innerHTML = `<div class="between"><h1>Environnement</h1><button class="btn btnp" form="ef">Enregistrer</button></div>
    <p class="sub">Chemins et noms utilisés pour générer les scripts. Les valeurs par défaut suivent les dossiers d'installation standard : vérifiez-les sur la machine de migration.</p>
    ${S.system?.execution_available ? `<div class="note ok">PowerShell disponible sur cette machine : les étapes peuvent être exécutées depuis l'application.</div>`
      : `<div class="note warn">PowerShell n'est pas disponible là où tourne l'application : les étapes seront <b>simulées</b> (script généré, non exécuté). Lancez l'application sur la machine Windows qui a NAV, BC et SQL Server pour exécuter.</div>`}
    <form id="ef" class="grid2">${groups.map(g => `<div class="card"><h2>${esc(g)}</h2><div class="form">
      ${d.fields.filter(f => f.group === g).map(f => `<label>${esc(f.label)}<input name="${f.key}" value="${esc(d.values[f.key])}" placeholder="${esc(f.default)}">${f.help ? `<span class="help">${esc(f.help)}</span>` : ""}</label>`).join("")}
    </div></div>`).join("")}</form>`;
  $("#ef", v).onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    try { await api(`/api/projects/${S.projectId}/environment`, { method: "PUT", json: body }); toast("Environnement enregistré."); } catch (err) { fail(err); }
  };
};

/* ------------------------------------------------------------------ inventory */
function dropZone(id, label, accept, onFile) {
  setTimeout(() => {
    const z = document.getElementById(id); if (!z) return;
    const inp = $("input", z);
    inp.onchange = () => inp.files[0] && onFile(inp.files[0]);
    z.ondragover = e => { e.preventDefault(); z.classList.add("over"); };
    z.ondragleave = () => z.classList.remove("over");
    z.ondrop = e => { e.preventDefault(); z.classList.remove("over"); e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]); };
  });
  return `<div class="drop" id="${id}">${label}<br><label class="btn" style="display:inline-flex;margin-top:8px">Choisir un fichier<input type="file" accept="${accept}" hidden></label></div>`;
}
async function upload(url, file, field = "file") {
  const fd = new FormData(); fd.append(field, file);
  return api(url, { method: "POST", body: fd });
}
const hbars = (obj, total) => `<div class="hbars">${Object.entries(obj).map(([k, n]) => `<div class="hb"><span>${esc(k)}</span><div class="bar"><span style="width:${total ? 100 * n / total : 0}%"></span></div><span class="num">${fmt(n)}</span></div>`).join("")}</div>`;
VIEWS.inventory = async v => {
  const inv = await api(`/api/projects/${S.projectId}/inventory`);
  const zone = dropZone("invDrop", "Déposez l'export TXT de <b>tous les objets</b> NAV (Object Designer → Export, format .txt).<br><span class='small'>Ou lancez l'étape « Exporter tous les objets NAV » depuis l'écran Exécution.</span>", ".txt", async f => {
    toast("Analyse de " + f.name + "…");
    try { const r = await upload(`/api/projects/${S.projectId}/inventory`, f); toast(`${fmt(r.object_count)} objets, ${r.report_count} rapports analysés.`); route(); } catch (e) { fail(e); }
  });
  if (inv.empty) { v.innerHTML = `<h1>Inventaire</h1><p class="sub">Taille et forme de la base avant de planifier.</p><div class="card">${zone}</div>`; return; }
  const high = inv.risks.filter(r => r.severity === "high");
  v.innerHTML = `<div class="between"><h1>Inventaire</h1><span class="muted small">Source : ${esc(inv.source)} · ${esc((inv.updated_at || "").slice(0, 16).replace("T", " "))}</span></div>
    <div class="kpis">
      <div class="kpi"><div class="k">Objets</div><div class="v">${fmt(inv.object_count)}</div><div class="d">${fmt(inv.modified_count)} modifiés</div></div>
      <div class="kpi"><div class="k">Objets client</div><div class="v">${fmt(inv.by_origin.custom || 0)}</div><div class="d">${fmt(inv.by_origin.standard_modified || 0)} standards modifiés</div></div>
      <div class="kpi"><div class="k">Tables client</div><div class="v">${inv.custom_tables.length}</div><div class="d">${inv.custom_fields.length} champs ajoutés au standard</div></div>
      <div class="kpi"><div class="k">Bloquants BC Online</div><div class="v" style="color:${high.length ? "var(--err)" : "var(--ok)"}">${high.reduce((a, r) => a + r.count, 0)}</div><div class="d">objet(s) concerné(s)</div></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Objets par type</h2>${hbars(inv.by_type, inv.object_count)}</div>
      <div class="card"><h2>Origine</h2>${hbars(Object.fromEntries(Object.entries(inv.by_origin).map(([k, n]) => [ORIGIN[k] || k, n])), inv.object_count)}
        <h3 style="margin-top:14px">Étiquettes de version</h3><div class="flex">${Object.entries(inv.version_tags).map(([k, n]) => `<span class="tag">${esc(k)} · ${n}</span>`).join("")}</div></div>
    </div>
    <div class="card flush"><div style="padding:14px 16px 0"><h2>Fonctions indisponibles ou à revoir pour BC Online</h2><p class="small muted" style="margin:-4px 0 10px">Règles de détection à valider (statut « to_validate ») : un signal à vérifier, pas un verdict.</p></div>
      <table class="tb"><thead><tr><th>Règle</th><th>Gravité</th><th>Constat</th><th>Action</th><th class="num">Objets</th></tr></thead><tbody>
      ${inv.risks.map(r => `<tr><td class="mono">${esc(r.id)}</td><td><span class="tag ${r.severity === "high" ? "err" : r.severity === "medium" ? "warn" : ""}">${r.severity}</span></td><td>${esc(r.message)}<details><summary>Voir les objets</summary><div class="small">${r.objects.map(esc).join("<br>")}</div></details></td><td class="small">${esc(r.action)}</td><td class="num">${r.count}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">Aucune fonction bloquante détectée.</td></tr>`}
      </tbody></table></div>
    <div class="card flush"><div class="between" style="padding:14px 16px"><h2 style="margin:0">Objets</h2>
      <div class="flex"><input id="objQ" placeholder="Filtrer (nom, ID, étiquette)" aria-label="Filtrer les objets"><select id="objT" aria-label="Type"><option value="">Tous les types</option>${Object.keys(inv.by_type).map(t => `<option>${t}</option>`).join("")}</select>
      <select id="objO" aria-label="Origine"><option value="">Toutes origines</option>${Object.keys(inv.by_origin).map(o => `<option value="${o}">${ORIGIN[o] || o}</option>`).join("")}</select></div></div>
      <div class="scroll"><table class="tb"><thead><tr><th>Type</th><th class="num">ID</th><th>Nom</th><th>Origine</th><th>Version List</th><th>Date</th></tr></thead><tbody id="objB"></tbody></table></div></div>
    <div class="card">${zone}</div>`;
  const full = await api(`/api/projects/${S.projectId}/inventory?with_objects=true`);
  const draw = () => {
    const q = $("#objQ", v).value.toLowerCase(), t = $("#objT", v).value, o = $("#objO", v).value;
    const rows = full.objects.filter(x => (!t || x.type === t) && (!o || x.origin === o) && (!q || `${x.id} ${x.name} ${x.version_list}`.toLowerCase().includes(q)));
    $("#objB", v).innerHTML = rows.slice(0, 600).map(x => `<tr><td>${x.type}</td><td class="num">${x.id}</td><td>${esc(x.name)}</td><td><span class="tag ${x.origin === "custom" ? "teal" : x.origin === "standard_modified" ? "warn" : x.origin === "addon" ? "hyp" : ""}">${ORIGIN[x.origin]}</span></td><td class="small">${esc(x.version_list)}</td><td class="small">${esc(x.date)}</td></tr>`).join("")
      + (rows.length > 600 ? `<tr><td colspan="6" class="muted">${fmt(rows.length - 600)} objets de plus : affinez le filtre.</td></tr>` : "");
  };
  ["objQ", "objT", "objO"].forEach(id => ($("#" + id, v).oninput = draw)); draw();
};

/* ------------------------------------------------------------------ reports */
const CX = { simple: "ok", medium: "info", complex: "warn", highly_complex: "err" };
VIEWS.reports = async (v, arg) => {
  if (arg) return reportDetail(v, +arg);
  const d = await api(`/api/projects/${S.projectId}/reports`);
  const zone = dropZone("repDrop", "Déposez un export TXT contenant des rapports (un ou plusieurs objets).", ".txt", async f => {
    try { const r = await upload(`/api/projects/${S.projectId}/reports`, f); toast(`${r.imported.length} rapport(s) analysé(s).`); route(); } catch (e) { fail(e); }
  });
  const s = d.summary;
  v.innerHTML = `<h1>Rapports &amp; layouts</h1><p class="sub">Inventaire, structure, cohérence dataset ↔ layout, complexité justifiée et stratégie proposée pour chaque rapport. L'import de l'inventaire complet analyse aussi tous les rapports.</p>
    ${s.report_count ? `<div class="kpis"><div class="kpi"><div class="k">Rapports</div><div class="v">${s.report_count}</div></div>
      ${Object.entries(s.by_complexity).map(([k, n]) => `<div class="kpi"><div class="k">${esc(k)}</div><div class="v">${n}</div></div>`).join("")}</div>
    <div class="grid2"><div class="card"><h2>Stratégies proposées</h2>${hbars(s.by_strategy, s.report_count)}</div><div class="card"><h2>Layouts</h2>${hbars(s.by_layout, s.report_count)}</div></div>
    <div class="card flush"><div class="flex" style="padding:12px 16px"><input id="rq" placeholder="Filtrer" aria-label="Filtrer les rapports"></div><div class="scroll" style="max-height:560px"><table class="tb"><thead><tr><th class="num">ID</th><th>Nom</th><th>Origine</th><th>Layouts</th><th>Complexité</th><th>Stratégie proposée</th><th class="num">Erreurs</th><th class="num">Alertes</th></tr></thead><tbody id="rb"></tbody></table></div></div>` : ""}
    <div class="card">${zone}</div>`;
  if (!s.report_count) return;
  const draw = () => {
    const q = $("#rq", v).value.toLowerCase();
    $("#rb", v).innerHTML = d.reports.filter(r => !q || `${r.report_id} ${r.name} ${r.strategy.label}`.toLowerCase().includes(q)).map(r => `<tr class="click" data-id="${r.report_id}" tabindex="0">
      <td class="num">${r.report_id}</td><td>${esc(r.name)}</td><td>${ORIGIN[r.origin] || r.origin}</td><td>${r.layouts.map(l => `<span class="tag">${l}</span>`).join(" ") || "—"}</td>
      <td><span class="tag ${CX[r.complexity.class]}">${r.complexity.label} · ${r.complexity.score}</span></td><td>${esc(r.strategy.label)}</td>
      <td class="num">${r.errors ? `<span class="tag err">${r.errors}</span>` : 0}</td><td class="num">${r.warnings}</td></tr>`).join("");
    $$("#rb tr", v).forEach(tr => { tr.onclick = () => (location.hash = "#/reports/" + tr.dataset.id); tr.onkeydown = e => e.key === "Enter" && tr.click(); });
  };
  $("#rq", v).oninput = draw; draw();
};
async function reportDetail(v, id) {
  const r = await api(`/api/projects/${S.projectId}/reports/${id}`);
  v.innerHTML = `<div class="between"><div><a href="#/reports" class="small">← Rapports</a><h1>Rapport ${r.report_id} · ${esc(r.name)}</h1></div>
      <span class="tag ${CX[r.complexity.class]}" style="font-size:14px">${r.complexity.label} · score ${r.complexity.score}</span></div>
    <div class="grid2">
      <div class="card"><h2>Stratégie proposée <span class="tag warn">à valider</span></h2><p><b>${esc(r.strategy.label)}</b></p><p class="small">${esc(r.strategy.detail)}</p>
        <dl class="dl"><dt>Origine</dt><dd>${esc(r.strategy.origin_label)}</dd><dt>Version List</dt><dd class="mono">${esc(r.version_list)}</dd>
        <dt>Layouts</dt><dd>${r.layouts.join(", ") || "—"}${r.default_layout ? ` (défaut : ${r.default_layout})` : ""}</dd><dt>Traitement seul</dt><dd>${r.processing_only ? "Oui" : "Non"}</dd></dl></div>
      <div class="card"><h2>Pourquoi ce score</h2><table class="tb"><tbody>${r.complexity.factors.map(f => `<tr><td>${esc(f.label)}</td><td class="small muted">${esc(f.evidence)}</td><td class="num">+${f.points}</td></tr>`).join("")}</tbody></table></div>
    </div>
    <div class="card"><h2>Contrôles (${r.issues.length})</h2>${r.issues.length ? `<table class="tb"><tbody>${r.issues.map(i => `<tr><td><span class="tag ${i.severity === "error" ? "err" : i.severity === "warning" ? "warn" : ""}">${i.severity}</span></td><td class="mono">${esc(i.code)}</td><td>${esc(i.message)}</td></tr>`).join("")}</tbody></table>` : `<div class="note ok">Aucun écart détecté.</div>`}</div>
    <div class="grid2">
      <div class="card"><h2>DataItems</h2><table class="tb"><thead><tr><th>Nom</th><th>Table</th><th>Parent</th><th class="num">Colonnes</th><th>Triggers</th></tr></thead><tbody>${r.dataitems.map(d => `<tr><td>${esc(d.name)}</td><td>${d.table_id ?? ""}${d.is_integer ? ' <span class="tag info">Integer</span>' : ""}</td><td>${esc(d.parent || "")}</td><td class="num">${d.column_count}</td><td class="small">${d.triggers.join(", ")}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h2>Layout</h2>${r.rdlc ? `<dl class="dl"><dt>Champs du dataset RDLC</dt><dd>${r.rdlc.dataset_field_count}</dd><dt>Tablix / groupes</dt><dd>${r.rdlc.tablix_count} / ${r.rdlc.group_count}</dd><dt>Code VB</dt><dd>${r.rdlc.embedded_code_lines} ligne(s)</dd><dt>Polices</dt><dd>${r.rdlc.fonts.join(", ") || "—"}</dd><dt>Codes-barres</dt><dd>${r.rdlc.barcode_fonts.join(", ") || "—"}</dd></dl>` : ""}
        ${r.word ? `<dl class="dl"><dt>Contrôles de contenu Word</dt><dd>${r.word.content_controls} (${r.word.unbound_controls} non liés)</dd><dt>Champs liés</dt><dd>${r.word.bound_fields.join(", ")}</dd></dl>` : ""}
        ${r.classic ? `<dl class="dl"><dt>Sections Classic</dt><dd>${r.classic.section_count} (${r.classic.section_types.join(", ")})</dd><dt>Contrôles</dt><dd>${r.classic.control_count}</dd></dl>` : ""}
        ${!r.rdlc && !r.word && !r.classic ? `<p class="muted">Aucun layout.</p>` : ""}</div>
    </div>
    <div class="card flush"><div style="padding:14px 16px 0"><h2>Colonnes du dataset (${r.columns.length})</h2></div><div class="scroll"><table class="tb"><thead><tr><th>Colonne</th><th>Expression</th><th>DataItem</th></tr></thead><tbody>${r.columns.map(c => `<tr><td>${esc(c.name)}</td><td class="mono">${esc(c.source_expr)}</td><td>${esc(c.dataitem || "")}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="grid2"><div class="card"><h2>Dépendances</h2><dl class="dl">${Object.entries(r.dependencies).map(([k, ids]) => `<dt>${k}</dt><dd>${ids.join(", ")}</dd>`).join("") || "<dd>Aucune</dd>"}</dl></div>
    <div class="card"><h2>Code</h2><dl class="dl"><dt>Fonctions</dt><dd>${r.procedures.map(p => `${esc(p.name)} (${p.lines} l.)`).join(", ") || "—"}</dd><dt>Tables temporaires</dt><dd>${r.temporary_tables.join(", ") || "—"}</dd><dt>Labels</dt><dd>${r.labels.join(", ") || "—"}</dd><dt>Champs de la request page</dt><dd>${r.request_page_fields}</dd></dl></div></div>`;
}

/* ------------------------------------------------------------------ data */
VIEWS.data = async v => {
  const [d, env] = await Promise.all([api(`/api/projects/${S.projectId}/data`), api(`/api/projects/${S.projectId}/environment`)]);
  const cov = d.coverage;
  const inAl = x => cov ? (x.in_al ? `<span class="tag ok">Présent en AL</span>` : `<span class="tag err">Absent de l'AL</span>`) : `<span class="tag">Non contrôlé</span>`;
  const tables = cov ? cov.tables : d.custom_tables, fields = cov ? cov.fields : d.custom_fields;
  v.innerHTML = `<h1>Données</h1>
    <p class="sub">La migration cloud ne reprend que les tables qui existent à la fois dans la source et dans BC Online. Chaque table client et chaque champ ajouté à une table standard doit donc exister dans l'extension AL installée.</p>
    <div class="card"><h2>Couverture par l'extension AL</h2>
      <form class="row" id="scanF"><label>Dossier du projet AL<input name="folder" value="${esc(env.values.al_project)}"></label><button class="btn btnp" style="align-self:flex-end">Analyser le projet AL</button></form>
      ${cov ? `<div class="kpis" style="margin-top:12px"><div class="kpi"><div class="k">Tables client absentes</div><div class="v" style="color:${cov.missing_tables ? "var(--err)" : "var(--ok)"}">${cov.missing_tables}</div><div class="d">sur ${cov.tables.length}</div></div>
        <div class="kpi"><div class="k">Champs ajoutés absents</div><div class="v" style="color:${cov.missing_fields ? "var(--err)" : "var(--ok)"}">${cov.missing_fields}</div><div class="d">sur ${cov.fields.length}</div></div>
        <div class="kpi"><div class="k">Fichiers AL lus</div><div class="v">${d.al_scan?.al_file_count ?? "—"}</div><div class="d">${esc(d.al_scan?.folder || "")}</div></div></div>` : `<p class="muted small">Analyse disponible après la conversion Txt2AL (le dossier doit être lisible depuis la machine qui exécute l'application).</p>`}
    </div>
    ${!tables.length && !fields.length ? `<div class="card empty">Importez l'inventaire pour lister les tables et champs client.<br><a class="btn" href="#/inventory">Inventaire</a></div>` : `
    <div class="grid2">
      <div class="card flush"><div style="padding:14px 16px 0"><h2>Tables client (${tables.length})</h2></div><div class="scroll"><table class="tb"><thead><tr><th class="num">ID</th><th>Nom</th><th class="num">Champs</th><th>AL</th></tr></thead><tbody>
        ${tables.map(t => `<tr><td class="num">${t.id}</td><td>${esc(t.name)}</td><td class="num">${t.field_count}</td><td>${inAl(t)}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="card flush"><div style="padding:14px 16px 0"><h2>Champs client dans des tables standard (${fields.length})</h2></div><div class="scroll"><table class="tb"><thead><tr><th>Table</th><th class="num">Champ</th><th>Nom</th><th>Type</th><th>AL</th></tr></thead><tbody>
        ${fields.map(f => `<tr><td>${f.table_id} ${esc(f.table)}</td><td class="num">${f.id}</td><td>${esc(f.name)}</td><td class="mono">${esc(f.type)}</td><td>${inAl(f)}</td></tr>`).join("")}</tbody></table></div></div>
    </div>`}
    <div class="card"><h2>Correspondances et changements de structure</h2><p class="small muted">Les déplacements ou renommages de champs entre versions sont tenus dans le référentiel, avec leur preuve et leur validation nominative.</p><a class="btn" href="#/knowledge">Ouvrir le référentiel</a></div>`;
  $("#scanF", v).onsubmit = async e => {
    e.preventDefault();
    try { const r = await api(`/api/projects/${S.projectId}/al-scan`, { method: "POST", json: { folder: e.target.folder.value } }); toast(`${r.scan.al_file_count} fichiers AL lus.`); route(); } catch (err) { fail(err); }
  };
};

/* ------------------------------------------------------------------ execution */
VIEWS.execution = async (v, arg) => {
  const plan = await api(`/api/projects/${S.projectId}/plan`);
  const all = plan.phases.flatMap(p => p.steps);
  S.stepId = arg || S.stepId && all.find(s => s.id === S.stepId)?.id || (all.find(s => s.status !== "done") || all[0]).id;
  const pct = Math.round(100 * plan.progress.done / plan.progress.total);
  v.innerHTML = `<div class="between"><div><h1>Exécution</h1><div class="muted small">${esc(plan.upgrade_path.source)} → ${esc(plan.upgrade_path.target)} · ${plan.progress.done} / ${plan.progress.total} étapes</div></div>
      <div style="min-width:240px"><div class="bar"><span style="width:${pct}%"></span></div></div></div>
    ${S.system?.execution_available ? "" : `<div class="note warn">Exécution réelle indisponible ici (PowerShell absent) : les étapes PowerShell tournent en simulation. Lancez <span class="mono">nav2bc serve</span> sur la machine Windows de migration.</div>`}
    <div class="exec"><div class="phases">${plan.phases.map(ph => `<div class="phase"><div class="ph">${esc(ph.label)}<span>${ph.done}/${ph.steps.length}</span></div>
      ${ph.steps.map(s => `<button class="st ${s.id === S.stepId ? "on" : ""}" data-id="${s.id}"><span class="dot ${s.status}">${s.status === "done" ? "✓" : s.status === "failed" ? "✕" : ""}</span>
        <span>${esc(s.title)}<br><span class="kind">${KIND[s.kind]}${s.destructive ? " · modifie une base / un service" : ""}${s.missing.length ? " · ⚠ environnement incomplet" : ""}</span></span><span class="muted small">${s.order}</span></button>`).join("")}</div>`).join("")}
    </div><div id="stepBox"></div></div>`;
  $$(".st", v).forEach(b => (b.onclick = () => { S.stepId = b.dataset.id; $$(".st", v).forEach(x => x.classList.toggle("on", x === b)); history.replaceState(null, "", "#/execution/" + b.dataset.id); renderStep($("#stepBox", v)); }));
  renderStep($("#stepBox", v));
};

async function renderStep(box) {
  stopTimers();
  const s = await api(`/api/projects/${S.projectId}/steps/${S.stepId}`);
  const st = s.state, cl = st.checklist || {};
  const lastRun = s.runs[0];
  box.innerHTML = `<div class="card"><div class="between"><div><div class="muted small">${esc(s.phase_label)} · étape ${esc(s.id)}</div><h2 style="margin:2px 0 0">${esc(s.title)}</h2></div>
      <div class="flex">${statusTag(s.running_run ? "running" : st.status)}${evTag(s.evidence)}<span class="tag">${KIND[s.kind]}</span>${s.destructive ? `<span class="tag err">Modifie une base / un service</span>` : ""}</div></div>
    <p>${esc(s.description)}</p>
    ${s.documentation_url ? `<p class="small"><a href="${esc(s.documentation_url)}" target="_blank" rel="noopener">Documentation Microsoft ↗</a></p>` : ""}
    ${s.missing.length ? `<div class="note warn">Environnement incomplet : ${s.missing.map(esc).join(", ")}. <a href="#/environment">Compléter</a></div>` : ""}
    ${st.validated_by ? `<p class="small muted">Validé par <b>${esc(st.validated_by)}</b> le ${esc((st.validated_at || "").slice(0, 16).replace("T", " "))}${st.note ? " — " + esc(st.note) : ""}</p>` : ""}
    ${s.kind === "manual" ? `<h3>Points de contrôle</h3><div class="checklist">${s.checklist.map(i => `<label><input type="checkbox" data-item="${esc(i)}" ${cl[i] ? "checked" : ""}><span>${esc(i)}${cl[i] ? `<br><span class="by">${esc(cl[i].by)} · ${esc(cl[i].at.slice(0, 16).replace("T", " "))}</span>` : ""}</span></label>`).join("")}</div>
      <div class="flex" style="margin-top:12px">${st.status === "done" ? `<button class="btn" id="reset">Rouvrir l'étape</button>` : `<button class="btn btnp" id="complete">Valider l'étape</button>`}</div>` : `
      <div class="flex" style="margin-top:6px">
        ${s.kind === "powershell" ? `<button class="btn btnp" id="exec" ${s.running_run ? "disabled" : ""}>▶ Exécuter</button><button class="btn" id="sim" ${s.running_run ? "disabled" : ""}>Simuler</button>` : `<button class="btn btnp" id="exec" ${s.running_run ? "disabled" : ""}>▶ Lancer</button>`}
        ${st.status !== "todo" ? `<button class="btn" id="reset">Remettre à faire</button>` : ""}
      </div>`}
  </div>
  ${s.kind === "powershell" ? `<div class="card"><div class="between"><h2 style="margin:0">Script PowerShell</h2><span class="small muted">Origine : ${esc(s.script_origin)}</span></div>
    <p class="small muted">À relire avant exécution. Vous pouvez le modifier ici, ou déposer votre propre script dans <span class="mono">&lt;dossier de travail&gt;\\scripts\\${esc(s.id)}.ps1</span>.</p>
    <textarea class="code" id="script" spellcheck="false" aria-label="Script PowerShell">${esc(s.script)}</textarea>
    <div class="flex" style="margin-top:8px"><button class="btn" id="saveScript">Enregistrer ma version</button>${s.script_origin.startsWith("modifié") ? `<button class="btn" id="resetScript">Revenir au script généré</button>` : ""}<button class="btn" id="copyScript">Copier</button></div></div>` : ""}
  <div class="card"><div class="between"><h2 style="margin:0">Journal d'exécution</h2>${lastRun ? `<span class="small muted">Exécution #${lastRun.id} · ${esc(lastRun.mode)} · ${esc(lastRun.started_by || "")}</span>` : ""}</div>
    <div id="runErr"></div><pre class="console" id="log">${lastRun ? "" : "Aucune exécution pour l'instant."}</pre>
    ${s.runs.length > 1 ? `<details style="margin-top:8px"><summary>Historique (${s.runs.length})</summary><table class="tb"><tbody>${s.runs.map(r => `<tr class="click" data-run="${r.id}"><td>#${r.id}</td><td>${esc(r.mode)}</td><td>${esc(r.status)}</td><td>${esc(r.started_by || "")}</td><td class="small">${esc(r.started_at.slice(0, 16).replace("T", " "))}</td></tr>`).join("")}</tbody></table></details>` : ""}
  </div>`;

  const refresh = () => { renderStep(box); refreshPlanDots(); };
  $$("[data-item]", box).forEach(c => (c.onchange = async () => {
    const by = S.user || await askUser("Chaque point coché est signé.");
    if (!by) { c.checked = !c.checked; return; }
    try { await api(`/api/projects/${S.projectId}/steps/${s.id}/checklist`, { method: "POST", json: { item: c.dataset.item, checked: c.checked, by } }); refresh(); } catch (e) { fail(e); }
  }));
  const on = (id, fn) => { const el = $("#" + id, box); if (el) el.onclick = fn; };
  on("complete", async () => {
    const by = await askUser("La validation d'une étape est nominative.");
    if (!by) return;
    try { await api(`/api/projects/${S.projectId}/steps/${s.id}/complete`, { method: "POST", json: { by } }); toast("Étape validée."); refresh(); } catch (e) { fail(e); }
  });
  on("reset", async () => { try { await api(`/api/projects/${S.projectId}/steps/${s.id}/reset`, { method: "POST" }); refresh(); } catch (e) { fail(e); } });
  on("saveScript", async () => { try { await api(`/api/projects/${S.projectId}/steps/${s.id}/script`, { method: "PUT", json: { script: $("#script", box).value } }); toast("Script enregistré pour ce projet."); refresh(); } catch (e) { fail(e); } });
  on("resetScript", async () => { try { await api(`/api/projects/${S.projectId}/steps/${s.id}/script`, { method: "PUT", json: { script: null } }); refresh(); } catch (e) { fail(e); } });
  on("copyScript", async () => { try { await navigator.clipboard.writeText($("#script", box).value); toast("Script copié."); } catch { toast("Copie impossible : sélectionnez le texte.", true); } });
  const launch = async simulate => {
    let by = S.user, confirm = false;
    if (!simulate && s.kind === "powershell") {
      by = await askUser(s.destructive ? "Cette étape modifie une base ou un service. L'exécution est enregistrée à votre nom." : "L'exécution est enregistrée à votre nom.");
      if (!by) return;
      if (s.destructive) {
        confirm = await new Promise(res => {
          const b = modal(`<h2>Confirmer l'exécution</h2><div class="note err">« ${esc(s.title)} » modifie une base de données ou un service. Vérifiez qu'une sauvegarde récente existe.</div>
            <form class="form" id="cf" style="margin-top:12px"><label>Tapez <b>EXECUTER</b> pour confirmer<input name="c" autocomplete="off"></label>
            <div class="mact"><button type="button" class="btn" data-close>Annuler</button><button class="btn btnd">Exécuter</button></div></form>`);
          $("#cf", b).onsubmit = e => { e.preventDefault(); if (e.target.c.value.trim().toUpperCase() !== "EXECUTER") return; closeModal(); res(true); };
          $("[data-close]", b).onclick = () => { closeModal(); res(false); };
        });
        if (!confirm) return;
      }
    }
    try {
      const r = await api(`/api/projects/${S.projectId}/steps/${s.id}/run`, { method: "POST", json: { by, simulate, confirm } });
      await renderStep(box); follow(r.run_id, box);
    } catch (e) { fail(e); }
  };
  on("exec", () => launch(false));
  on("sim", () => launch(true));
  $$("[data-run]", box).forEach(tr => (tr.onclick = () => follow(+tr.dataset.run, box)));
  if (lastRun) follow(s.running_run || lastRun.id, box);
}

function follow(runId, box) {
  stopTimers();
  let offset = 0;
  const log = $("#log", box);
  log.textContent = "";
  const tick = async () => {
    try {
      const r = await api(`/api/runs/${runId}?offset=${offset}`);
      if (r.log) { log.textContent += r.log; log.scrollTop = log.scrollHeight; }
      offset = r.log_length;
      if (r.status !== "running") {
        stopTimers();
        const errs = (r.errors || []).filter(Boolean);
        $("#runErr", box).innerHTML = `<div class="note ${r.status === "success" ? "ok" : r.status === "failed" ? "err" : ""}" style="margin-bottom:8px">Exécution #${r.id} : <b>${{ success: "réussie", failed: "en échec", simulated: "simulée (rien n'a été exécuté)" }[r.status] || r.status}</b>${r.exit_code != null ? ` · code ${r.exit_code}` : ""}</div>
          ${errs.map(e => `<div class="${e.solution ? "sol" : "note warn"}" style="margin-bottom:6px"><span class="mono">${esc(e.normalized)}</span><br>${e.solution ? `<b>Solution connue</b> (confiance ${Math.round(100 * e.confidence)} %) : ${esc(e.solution)}` : `Nouvelle erreur : <a href="#/errors">documenter la solution</a> pour les prochaines fois.`}</div>`).join("")}`;
        if (box.dataset.lastStatus !== r.status) { box.dataset.lastStatus = r.status; refreshPlanDots(); }
      }
    } catch (e) { stopTimers(); fail(e); }
  };
  tick(); S.timers.push(setInterval(tick, 1500));
}

async function refreshPlanDots() {
  const plan = await api(`/api/projects/${S.projectId}/plan`);
  for (const s of plan.phases.flatMap(p => p.steps)) {
    const b = $(`.st[data-id="${CSS.escape(s.id)}"] .dot`);
    if (b) { b.className = "dot " + s.status; b.textContent = s.status === "done" ? "✓" : s.status === "failed" ? "✕" : ""; }
  }
  $$(".phase").forEach((el, i) => { const ph = plan.phases[i]; if (ph) $(".ph span", el).textContent = `${ph.done}/${ph.steps.length}`; });
}

/* ------------------------------------------------------------------ code AL */
VIEWS.code = async v => {
  const [plan, errs, d] = await Promise.all([api(`/api/projects/${S.projectId}/plan`), api("/api/errors?module=code"), api(`/api/projects/${S.projectId}/data`)]);
  const al = plan.phases.find(p => p.id === "al");
  v.innerHTML = `<h1>Code AL</h1><p class="sub">Conversion du code client C/AL en extension AL : export au nouveau format dans BC14, Txt2AL, projet AL, compilation alc, contrôle de couverture des données.</p>
    ${al ? `<div class="card flush"><table class="tb"><thead><tr><th>#</th><th>Étape</th><th>Type</th><th>Statut</th><th></th></tr></thead><tbody>
      ${al.steps.map(s => `<tr><td>${s.order}</td><td>${esc(s.title)}<div class="small muted">${esc(s.description)}</div></td><td>${KIND[s.kind]}</td><td>${statusTag(s.status)}</td><td><a class="btn sm" href="#/execution/${s.id}">Ouvrir</a></td></tr>`).join("")}</tbody></table></div>`
      : `<div class="note">Pas de conversion C/AL → AL dans ce chemin (la source est déjà en AL).</div>`}
    <div class="grid2">
      <div class="card"><h2>Couverture des données</h2>${d.coverage ? `<p>Tables client absentes de l'AL : <b>${d.coverage.missing_tables}</b> · champs : <b>${d.coverage.missing_fields}</b></p><a class="btn sm" href="#/data">Détail</a>` : `<p class="muted">Non contrôlée. Lancez l'étape « Contrôler la couverture des données » ou l'analyse depuis l'écran Données.</p>`}</div>
      <div class="card"><h2>Erreurs de compilation connues</h2>${errs.length ? `<table class="tb"><tbody>${errs.slice(0, 12).map(e => `<tr><td class="mono small">${esc(e.normalized)}</td><td class="num">${e.occurrences}×</td><td>${e.solution ? `<span class="tag ok">Solution</span>` : `<span class="tag warn">À documenter</span>`}</td></tr>`).join("")}</tbody></table><a class="btn sm" href="#/errors" style="margin-top:8px">Journal complet</a>` : `<p class="muted">Aucune pour l'instant : chaque erreur AL de l'étape de compilation est enregistrée ici.</p>`}</div>
    </div>`;
};

/* ------------------------------------------------------------------ validation */
VIEWS.validation = async v => {
  const [plan, d] = await Promise.all([api(`/api/projects/${S.projectId}/plan`), api(`/api/projects/${S.projectId}/data`)]);
  const val = plan.phases.find(p => p.id === "validation");
  const rc = d.rowcount;
  const LBL = { ok: ["Identique", "ok"], diff: ["Écart", "err"], missing_target: ["Absente de la cible", "warn"], new_target: ["Nouvelle dans la cible", "info"] };
  v.innerHTML = `<h1>Validation</h1><p class="sub">Dans l'ordre : technique (compilation, synchronisation), fonctionnelle (volumes et datasets), visuelle (rapports PDF).</p>
    <div class="card flush"><table class="tb"><thead><tr><th>#</th><th>Étape</th><th>Statut</th><th></th></tr></thead><tbody>${val.steps.map(s => `<tr><td>${s.order}</td><td>${esc(s.title)}</td><td>${statusTag(s.status)}</td><td><a class="btn sm" href="#/execution/${s.id}">Ouvrir</a></td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2>Volumes par table</h2>
      ${rc ? `<div class="kpis"><div class="kpi"><div class="k">Identiques</div><div class="v" style="color:var(--ok)">${rc.ok}</div></div><div class="kpi"><div class="k">Écarts</div><div class="v" style="color:${rc.diff ? "var(--err)" : "inherit"}">${rc.diff}</div></div><div class="kpi"><div class="k">Absentes de la cible</div><div class="v">${rc.missing_target}</div></div><div class="kpi"><div class="k">Nouvelles</div><div class="v">${rc.new_target}</div></div></div>
        <div class="flex" style="margin:12px 0"><select id="rcF" aria-label="Filtrer"><option value="issues">Écarts et absences</option><option value="">Toutes les tables</option></select><input id="rcQ" placeholder="Table"></div>
        <div class="scroll"><table class="tb"><thead><tr><th>Table (société$table)</th><th class="num">Source</th><th class="num">Cible</th><th class="num">Écart</th><th>Statut</th></tr></thead><tbody id="rcB"></tbody></table></div>`
      : `<p class="muted">Lancez les deux étapes de comptage puis la comparaison, ou déposez les deux CSV ci-dessous (colonnes TableName, Rows).</p>`}
      <form id="rcU" class="row" style="margin-top:12px"><label>CSV source<input type="file" name="source" accept=".csv" required></label><label>CSV cible<input type="file" name="target" accept=".csv" required></label><button class="btn" style="align-self:flex-end">Comparer</button></form>
    </div>`;
  if (rc) {
    const draw = () => {
      const f = $("#rcF", v).value, q = $("#rcQ", v).value.toLowerCase();
      $("#rcB", v).innerHTML = rc.rows.filter(r => (!f || r.status === "diff" || r.status === "missing_target") && (!q || r.table.toLowerCase().includes(q))).slice(0, 800)
        .map(r => `<tr><td>${esc(r.table)}</td><td class="num">${r.source == null ? "—" : fmt(r.source)}</td><td class="num">${r.target == null ? "—" : fmt(r.target)}</td><td class="num">${r.delta ? fmt(r.delta) : ""}</td><td><span class="tag ${LBL[r.status][1]}">${LBL[r.status][0]}</span></td></tr>`).join("") || `<tr><td colspan="5" class="empty">Aucun écart.</td></tr>`;
    };
    $("#rcF", v).onchange = draw; $("#rcQ", v).oninput = draw; draw();
  }
  $("#rcU", v).onsubmit = async e => {
    e.preventDefault();
    try { await api(`/api/projects/${S.projectId}/rowcount`, { method: "POST", body: new FormData(e.target) }); toast("Comparaison enregistrée."); route(); } catch (err) { fail(err); }
  };
};

/* ------------------------------------------------------------------ errors */
const MODULES = { code: "Code", reports: "Rapports", data: "Données / mise à niveau", validation: "Validation", other: "Autre" };
VIEWS.errors = async v => {
  const mod = new URLSearchParams(location.hash.split("?")[1] || "").get("m") || "";
  const list = await api("/api/errors" + (mod ? "?module=" + mod : ""));
  v.innerHTML = `<h1>Journal d'erreurs</h1><p class="sub">Chaque erreur est réduite à une signature (noms, numéros et chemins retirés). Quand elle revient, sur ce projet ou un autre, la solution documentée s'affiche directement avec sa confiance.</p>
    <div class="flex">${["", ...Object.keys(MODULES)].map(m => `<a class="btn sm ${m === mod ? "btnp" : ""}" href="#/errors${m ? "?m=" + m : ""}">${m ? MODULES[m] : "Toutes"}</a>`).join("")}
      <button class="btn sm" id="addErr">+ Consigner une erreur</button></div>
    <div class="card flush"><table class="tb"><thead><tr><th>Signature</th><th>Module</th><th class="num">Occurrences</th><th class="num">Projets</th><th>Solution</th><th></th></tr></thead><tbody>
    ${list.map(e => `<tr><td><span class="mono small">${esc(e.normalized)}</span><div class="small muted">Ex. : ${esc(e.example_message.slice(0, 160))}</div></td><td>${MODULES[e.module] || e.module}</td><td class="num">${e.occurrences}</td><td class="num">${e.project_count}</td>
      <td>${e.solution ? `<div class="sol">${esc(e.solution)}${e.root_cause ? `<div class="small muted">Cause : ${esc(e.root_cause)}</div>` : ""}<div class="small">Confiance ${Math.round(100 * e.confidence)} % · ${e.successes} ✓ / ${e.failures} ✕</div></div>` : `<span class="tag warn">À documenter</span>`}</td>
      <td><div class="flex"><button class="btn sm" data-sol="${e.id}">${e.solution ? "Modifier" : "Documenter"}</button>${e.solution ? `<button class="btn sm" data-fb="${e.id}" data-u="1" title="La solution a fonctionné">✓</button><button class="btn sm" data-fb="${e.id}" data-u="0" title="La solution n'a pas fonctionné">✕</button>` : ""}</div></td></tr>`).join("") || `<tr><td colspan="6" class="empty">Aucune erreur enregistrée.</td></tr>`}
    </tbody></table></div>`;
  $$("[data-sol]", v).forEach(b => (b.onclick = () => {
    const e = list.find(x => x.id === +b.dataset.sol);
    const m = modal(`<h2>Solution</h2><p class="mono small">${esc(e.normalized)}</p><form class="form" id="sf"><label>Cause<input name="root_cause" value="${esc(e.root_cause || "")}"></label>
      <label>Solution<textarea name="solution" rows="5" required>${esc(e.solution || "")}</textarea></label>
      <div class="mact"><button type="button" class="btn" data-close>Annuler</button><button class="btn btnp">Enregistrer</button></div></form>`);
    $("#sf", m).onsubmit = async ev => { ev.preventDefault(); try { await api(`/api/errors/${e.id}/solution`, { method: "POST", json: { solution: ev.target.solution.value, root_cause: ev.target.root_cause.value || null } }); closeModal(); route(); } catch (err) { fail(err); } };
  }));
  $$("[data-fb]", v).forEach(b => (b.onclick = async () => { try { await api(`/api/errors/${b.dataset.fb}/feedback`, { method: "POST", json: { useful: b.dataset.u === "1" } }); route(); } catch (e) { fail(e); } }));
  $("#addErr", v).onclick = () => {
    const m = modal(`<h2>Consigner une erreur</h2><form class="form" id="ef"><label>Module<select name="module">${Object.entries(MODULES).map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></label>
      <label>Message d'erreur (copié tel quel)<textarea name="message" rows="4" required></textarea></label><div class="mact"><button type="button" class="btn" data-close>Annuler</button><button class="btn btnp">Enregistrer</button></div></form>`);
    $("#ef", m).onsubmit = async ev => {
      ev.preventDefault();
      try {
        const r = await api("/api/errors", { method: "POST", json: { module: ev.target.module.value, message: ev.target.message.value, project_id: S.projectId || null } });
        closeModal(); toast(r.known ? "Erreur connue : " + r.solution : "Nouvelle signature enregistrée."); route();
      } catch (err) { fail(err); }
    };
  };
};

/* ------------------------------------------------------------------ knowledge */
const EVIDENCE = { documented: "Documenté (source Microsoft)", id_identity: "Identité d'ID", consultant: "Validation consultant", hypothesis: "Hypothèse" };
const SCOPE = { microsoft: "Microsoft", version: "Version", client: "Client", project: "Projet" };
const VSTAT = { proposed: ["Proposé", ""], reviewed: ["Revu", "info"], validated: ["Validé", "ok"], rejected: ["Rejeté", "err"] };
VIEWS.knowledge = async v => {
  const list = await api("/api/knowledge/entries");
  v.innerHTML = `<div class="between"><h1>Référentiel des changements</h1><button class="btn btnp" id="addK">+ Nouvelle fiche</button></div>
    <p class="sub">Un nom proche n'est jamais une preuve. Une fiche n'est appliquée automatiquement que si elle est validée nominativement et prouvée (documentation, identité d'ID ou validation consultant). Toute modification la renvoie en revue.</p>
    <div class="card flush"><table class="tb"><thead><tr><th>Changement</th><th>Source → cible</th><th>Preuve</th><th>Portée</th><th>Statut</th><th>Auto</th><th></th></tr></thead><tbody>
    ${list.map(e => `<tr><td><b>${esc(e.change_type)}</b><div class="small">${esc(e.description)}</div>${e.documentation_url ? `<a class="small" href="${esc(e.documentation_url)}" target="_blank" rel="noopener">Source ↗</a>` : ""}</td>
      <td class="small">${esc([e.source_table, e.source_field].filter(Boolean).join("."))} → ${esc([e.target_table, e.target_field].filter(Boolean).join("."))}<div class="muted">${esc(e.source_version || "")} → ${esc(e.target_version || "")}</div></td>
      <td><span class="tag ${e.evidence === "hypothesis" ? "hyp" : "teal"}">${EVIDENCE[e.evidence]}</span></td><td>${SCOPE[e.scope]}</td>
      <td><span class="tag ${VSTAT[e.validation_status][1]}">${VSTAT[e.validation_status][0]}</span>${e.validated_by ? `<div class="small muted">${esc(e.validated_by)}</div>` : ""}</td>
      <td>${e.auto_applicable ? `<span class="tag ok">Oui</span>` : `<span class="tag">Non</span>`}</td>
      <td><select data-st="${e.id}" aria-label="Changer le statut"><option value="">Statut…</option>${Object.entries(VSTAT).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select></td></tr>`).join("") || `<tr><td colspan="7" class="empty">Aucune fiche.</td></tr>`}
    </tbody></table></div>`;
  $$("[data-st]", v).forEach(s => (s.onchange = async () => {
    if (!s.value) return;
    const by = s.value === "validated" ? await askUser("La validation d'une fiche est nominative.") : S.user || null;
    if (s.value === "validated" && !by) { s.value = ""; return; }
    try { await api(`/api/knowledge/entries/${s.dataset.st}/status`, { method: "POST", json: { status: s.value, by } }); route(); } catch (e) { fail(e); }
  }));
  $("#addK", v).onclick = () => {
    const m = modal(`<h2>Nouvelle fiche de changement</h2><form class="form" id="kf">
      <div class="row"><label>Type de changement<select name="change_type"><option value="renamed">Renommé</option><option value="moved">Déplacé</option><option value="removed">Supprimé</option><option value="type_changed">Type modifié</option><option value="new">Nouveau</option><option value="behavior">Comportement</option></select></label>
      <label>Nature<select name="nature"><option value="structure">Structure</option><option value="functional">Fonctionnel</option></select></label></div>
      <div class="row"><label>Version source<input name="source_version" placeholder="NAV2016"></label><label>Version cible<input name="target_version" placeholder="BC29"></label></div>
      <div class="row"><label>Table source<input name="source_table"></label><label>Champ source<input name="source_field"></label></div>
      <div class="row"><label>Table cible<input name="target_table"></label><label>Champ cible<input name="target_field"></label></div>
      <label>Description<textarea name="description" rows="3" required></textarea></label>
      <div class="row"><label>Preuve<select name="evidence">${Object.entries(EVIDENCE).map(([k, l]) => `<option value="${k}" ${k === "hypothesis" ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Portée<select name="scope">${Object.entries(SCOPE).map(([k, l]) => `<option value="${k}" ${k === "project" ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
      <label>Lien de documentation<input name="documentation_url" placeholder="https://learn.microsoft.com/…"><span class="help">Obligatoire pour une preuve « Documenté ».</span></label>
      <label>Action requise<input name="required_action"></label>
      <div class="mact"><button type="button" class="btn" data-close>Annuler</button><button class="btn btnp">Enregistrer</button></div></form>`, true);
    $("#kf", m).onsubmit = async ev => {
      ev.preventDefault();
      const body = Object.fromEntries([...new FormData(ev.target).entries()].map(([k, x]) => [k, x.trim() || null]));
      try { await api("/api/knowledge/entries", { method: "POST", json: body }); closeModal(); toast("Fiche créée (statut : proposé)."); route(); } catch (err) { fail(err); }
    };
  };
};

/* ------------------------------------------------------------------ start */
(async () => {
  renderUser();
  try {
    S.system = await api("/api/system");
    $("#ver").textContent = "Version " + S.system.version;
    const b = $("#sysBadge");
    b.className = "sys " + (S.system.execution_available ? "on" : "off");
    b.textContent = S.system.execution_available ? "● Exécution PowerShell disponible" : "● Mode simulation";
    b.title = S.system.execution_available ? S.system.powershell : "PowerShell absent : les scripts sont générés, pas exécutés.";
    await loadProjects();
  } catch (e) { fail(e); }
  if (!location.hash) location.hash = "#/dashboard"; else route();
})();
