const BASE = "http://192.168.4.1";
const SWEAT_THRESHOLD = 100;

const app = {
  refreshMs: 500,
  timer: null,
  activeView: "global",
  displayMode: "grid",
  search: "",
  selectedId: "p001",
  viz: "none",
  bubbleMin: false,
  uiDetailsOpen: {},
  lockSingleRebuildUntilMs: 0
};

const participants = [
  {
    id: "p001",
    name: "Patient 001",
    bracelet: "Bracelet A (live)",
    live: true,
    lastTs: "-",
    vitals: { tempC: null, humPct: null, adcRaw: null, touch: null, led: null, buzzer: null },
    alertLevel: "MEDIUM",
    hydrationBalance: "Pending",
    profile: {
      fullName: "",
      age: "",
      sex: "N/A",
      weightKg: "",
      heightCm: "",
      activity: "Sédentaire",
      ambient: "",
      conditions: "",
      notes: ""
    }
  },
  { id:"p002", name:"Patient 002", bracelet:"Bracelet B", live:false, lastTs:"-", vitals:{}, alertLevel:"LOW", hydrationBalance:"Pending", profile:{fullName:"",age:"",sex:"N/A",weightKg:"",heightCm:"",activity:"Sédentaire",ambient:"",conditions:"",notes:""} },
  { id:"p003", name:"Patient 003", bracelet:"Bracelet C", live:false, lastTs:"-", vitals:{}, alertLevel:"LOW", hydrationBalance:"Pending", profile:{fullName:"",age:"",sex:"N/A",weightKg:"",heightCm:"",activity:"Sédentaire",ambient:"",conditions:"",notes:""} },
  { id:"p004", name:"Patient 004", bracelet:"Bracelet D", live:false, lastTs:"-", vitals:{}, alertLevel:"LOW", hydrationBalance:"Pending", profile:{fullName:"",age:"",sex:"N/A",weightKg:"",heightCm:"",activity:"Sédentaire",ambient:"",conditions:"",notes:""} }
];

let chartX = [];
let chartY = [];

function computeHydrationBalance(p){ return "Pending"; }
function computeAlertLevel(p){ return p.alertLevel || "LOW"; }

function alertClass(level){
  const x = String(level || "").toUpperCase();
  if (x === "HIGH") return "badge alert-high";
  if (x === "MEDIUM") return "badge alert-med";
  return "badge alert-low";
}

function fmt2(x){ return (x === null || x === undefined) ? "-" : Number(x).toFixed(2); }
function fmt1(x){ return (x === null || x === undefined) ? "-" : Number(x).toFixed(1); }
function fmt0(x){ return (x === null || x === undefined) ? "-" : String(x); }

function touchText(v){
  const isTouched =
    (v === true) || (v === 1) || (v === "1") || (v === "true") || (v === "on");
  return isTouched ? "TOUCHED" : "RELEASED";
}

function hasSweat(adcRaw){
  if (adcRaw === null || adcRaw === undefined) return false;
  return Number(adcRaw) >= SWEAT_THRESHOLD;
}

function sweatStatusHtml(adcRaw){
  const yes = hasSweat(adcRaw);
  return `
    <div class="sweatBox ${yes ? "sweatYes" : "sweatNo"}">
      ${yes ? "Sweat detected" : "No sweat detected"}
      <div class="small" style="margin-top:6px">adcRaw: ${fmt0(adcRaw)} | threshold: ${SWEAT_THRESHOLD}</div>
    </div>
  `;
}

function setConn(ok){
  const cls = ok ? "dot ok" : "dot";
  document.getElementById("connDot").className = cls;
  document.getElementById("ctrlDot").className = cls;
  document.getElementById("connText").textContent = ok ? "Connected" : "Disconnected";
  document.getElementById("ctrlText").textContent = ok ? "Connected" : "Disconnected";
}

function escapeHtml(s){
  return String(s)
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

function showView(view){
  app.activeView = view;
  document.querySelectorAll(".tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  ["global","single","settings"].forEach(v => {
    document.getElementById(`view-${v}`).classList.toggle("hidden", v !== view);
  });
  renderAll();
}

function filtered(){
  const q = (app.search || "").trim().toLowerCase();
  if (!q) return participants;
  return participants.filter(p => {
    const hay = `${p.name} ${p.bracelet} ${p.id} ${p.profile.fullName}`.toLowerCase();
    return hay.includes(q);
  });
}

function renderAll(){
  if (app.activeView === "global") renderGlobal();
  if (app.activeView === "single") renderSingle();
}

function commonCardHtml(p){
  const alertLevel = computeAlertLevel(p);
  const hb = computeHydrationBalance(p);
  p.alertLevel = alertLevel;
  p.hydrationBalance = hb;

  return `
    <div class="patientTop">
      <div>
        <p class="patientName">${escapeHtml(p.name)}</p>
        <div class="patientSub">${escapeHtml(p.bracelet)} <span class="mono">(${p.id})</span></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
        <span class="${alertClass(alertLevel)}">ALERT: ${escapeHtml(alertLevel)}</span>
        <span class="badge">${p.live ? "LIVE" : "PLACEHOLDER"}</span>
      </div>
    </div>

    <div class="vitals">
      <div class="vital">
        <p class="k">Température</p>
        <p class="v">${p.live ? `<span>${fmt2(p.vitals.tempC)}</span><span class="small"> C</span>` : "-"}</p>
      </div>
      <div class="vital">
        <p class="k">Humidité</p>
        <p class="v">${p.live ? `<span>${fmt1(p.vitals.humPct)}</span><span class="small"> %</span>` : "-"}</p>
      </div>
      <div class="vital">
        <p class="k">Conductivité brute</p>
        <p class="v">${p.live ? fmt0(p.vitals.adcRaw) : "-"}</p>
        <div class="small">adcRaw</div>
      </div>
      <div class="vital">
        <p class="k">Touch</p>
        <p class="v">${p.live ? escapeHtml(touchText(p.vitals.touch)) : "-"}</p>
        <div class="small">${p.live ? `raw: ${escapeHtml(String(p.vitals.touch))}` : ""}</div>
      </div>
    </div>

    ${p.live ? sweatStatusHtml(p.vitals.adcRaw) : ""}

    <div class="calc">
      <div class="calcBox">
        <p class="k">Bilan hydrique</p>
        <p class="v" style="font-size:16px">${escapeHtml(hb)}</p>
        <div class="small">Calcul à venir</div>
      </div>
      <div class="calcBox">
        <p class="k">Niveau d’alerte</p>
        <p class="v" style="font-size:16px">${escapeHtml(alertLevel)}</p>
        <div class="small">Règles puis modèle</div>
      </div>
    </div>

    <div class="vital" style="margin-top:10px">
      <p class="k">Device</p>
      <div class="small">LED: <b>${p.live ? (p.vitals.led ? "ON" : "OFF") : "-"}</b></div>
      <div class="small">Buzzer: <b>${p.live ? (p.vitals.buzzer ? "ON" : "OFF") : "-"}</b></div>
    </div>
  `;
}

function renderGlobal(){
  const list = filtered();
  document.getElementById("globalGridWrap").classList.toggle("hidden", app.displayMode !== "grid");
  document.getElementById("globalTableWrap").classList.toggle("hidden", app.displayMode !== "table");

  if (app.displayMode === "grid"){
    const el = document.getElementById("globalGrid");
    el.innerHTML = "";
    list.forEach(p => {
      const card = document.createElement("div");
      card.className = `patientCard ${p.live ? "live" : "placeholder"}`;
      card.innerHTML = `
        ${commonCardHtml(p)}
        <div class="actionsRow">
          <button class="btn primary" onclick="openSingle('${p.id}', true)">Voir profil</button>
        </div>
        <div class="hint">Dernière mise à jour: ${escapeHtml(p.lastTs || "-")}</div>
      `;
      el.appendChild(card);
    });
    return;
  }

  const rows = list.map(p => {
    const alertLevel = computeAlertLevel(p);
    const hb = computeHydrationBalance(p);
    const temp = p.live ? fmt2(p.vitals.tempC) : "-";
    const hum = p.live ? fmt1(p.vitals.humPct) : "-";
    const cond = p.live ? fmt0(p.vitals.adcRaw) : "-";
    const sweat = p.live ? (hasSweat(p.vitals.adcRaw) ? "YES" : "NO") : "-";
    return `
      <tr>
        <td><b>${escapeHtml(p.name)}</b><div class="mono">${escapeHtml(p.id)}</div></td>
        <td>${escapeHtml(p.bracelet)}</td>
        <td>${escapeHtml(alertLevel)}</td>
        <td>${escapeHtml(hb)}</td>
        <td>${temp}</td>
        <td>${hum}</td>
        <td>${cond}</td>
        <td>${sweat}</td>
        <td><button class="btn primary" onclick="openSingle('${p.id}', true)">Voir profil</button></td>
      </tr>
    `;
  }).join("");

  document.getElementById("globalTableWrap").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Participant</th>
          <th>Bracelet</th>
          <th>Alerte</th>
          <th>Bilan hydrique</th>
          <th>Temp</th>
          <th>Hum</th>
          <th>Conductivité brute</th>
          <th>Sweat</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderSingle(){
  if (Date.now() < app.lockSingleRebuildUntilMs) return;

  const p = participants.find(x => x.id === app.selectedId) || participants[0];
  if (!p) return;

  const profileKey = `${p.id}:profile`;
  const vizKey = `${p.id}:viz`;

  const isProfileOpen = !!app.uiDetailsOpen[profileKey];
  const isVizOpen = !!app.uiDetailsOpen[vizKey];

  document.getElementById("singleWrap").innerHTML = `
    <div class="patientCard ${p.live ? "live" : "placeholder"}">
      ${commonCardHtml(p)}

      <details id="profileDetails" ${isProfileOpen ? "open" : ""}>
        <summary>Profil participant</summary>
        ${profileFormHtml(p)}
      </details>

      <details id="vizDetails" ${isVizOpen ? "open" : ""}>
        <summary>Visualisation (optionnelle)</summary>
        <div class="subnote">Choisis le paramètre à visualiser avec le menu en haut de cette page.</div>
        ${renderVizBlock()}
      </details>

      <div class="hint">Dernière mise à jour: ${escapeHtml(p.lastTs || "-")}</div>
    </div>
  `;

  const prof = document.getElementById("profileDetails");
  const viz = document.getElementById("vizDetails");

  if (prof) prof.addEventListener("toggle", () => app.uiDetailsOpen[profileKey] = prof.open);
  if (viz) viz.addEventListener("toggle", () => app.uiDetailsOpen[vizKey] = viz.open);

  if (app.viz !== "none"){
    setTimeout(() => setupSingleChart(p), 0);
  }
}

function renderVizBlock(){
  if (app.viz === "none"){
    return `<div class="subnote">Aucune visualisation active.</div>`;
  }
  return `
    <div class="plotWrap">
      <canvas id="singleChart" width="900" height="320"></canvas>
    </div>
  `;
}

function setupSingleChart(p){
  const canvas = document.getElementById("singleChart");
  if (!canvas) return;

  const labels = chartX.slice();
  let data = [];
  let labelName = "";

  if (app.viz === "hum"){
    data = chartY.map(x => x.h);
    labelName = "Humidité (%)";
  } else if (app.viz === "cond"){
    data = chartY.map(x => x.c);
    labelName = "Conductivité brute (adcRaw)";
  } else {
    data = chartY.map(x => x.t);
    labelName = "Température (C)";
  }

  const ctx = canvas.getContext("2d");
  new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ label: `${p.name} ${labelName}`, data, tension: 0.3 }] },
    options: { responsive: false, animation: false }
  });
}

function profileFormHtml(p){
  const pr = p.profile || {};
  const sex = pr.sex || "N/A";
  const activity = pr.activity || "Sédentaire";
  const esc = escapeHtml;

  return `
    <div class="formGrid" oninput="lockSingleRebuild()">
      <div>
        <label>Nom complet</label>
        <input value="${esc(pr.fullName || "")}" placeholder="Ex: Aymen Zire" oninput="setProfile('${p.id}','fullName',this.value)">
      </div>
      <div>
        <label>Âge (années)</label>
        <input type="number" min="0" value="${esc(pr.age || "")}" placeholder="Ex: 17" oninput="setProfile('${p.id}','age',this.value)">
      </div>

      <div>
        <label>Sexe</label>
        <select onchange="setProfile('${p.id}','sex',this.value); lockSingleRebuild()">
          <option value="N/A" ${sex==="N/A" ? "selected" : ""}>N/A</option>
          <option value="F" ${sex==="F" ? "selected" : ""}>F</option>
          <option value="M" ${sex==="M" ? "selected" : ""}>M</option>
          <option value="Autre" ${sex==="Autre" ? "selected" : ""}>Autre</option>
        </select>
      </div>

      <div>
        <label>Poids (kg)</label>
        <input type="number" min="0" step="0.1" value="${esc(pr.weightKg || "")}" placeholder="Ex: 70.5" oninput="setProfile('${p.id}','weightKg',this.value)">
      </div>

      <div>
        <label>Taille (cm)</label>
        <input type="number" min="0" value="${esc(pr.heightCm || "")}" placeholder="Ex: 175" oninput="setProfile('${p.id}','heightCm',this.value)">
      </div>

      <div>
        <label>Niveau d’activité</label>
        <select onchange="setProfile('${p.id}','activity',this.value); lockSingleRebuild()">
          <option ${activity==="Sédentaire" ? "selected" : ""}>Sédentaire</option>
          <option ${activity==="Modéré" ? "selected" : ""}>Modéré</option>
          <option ${activity==="Élevé" ? "selected" : ""}>Élevé</option>
        </select>
      </div>

      <div>
        <label>Température ambiante (si connu)</label>
        <input value="${esc(pr.ambient || "")}" placeholder="Ex: 22 C" oninput="setProfile('${p.id}','ambient',this.value)">
      </div>

      <div>
        <label>Médication / Conditions</label>
        <input value="${esc(pr.conditions || "")}" placeholder="Ex: diurétiques" oninput="setProfile('${p.id}','conditions',this.value)">
      </div>

      <div style="grid-column:1/-1">
        <label>Notes</label>
        <input value="${esc(pr.notes || "")}" placeholder="Infos utiles pour le bilan hydrique" oninput="setProfile('${p.id}','notes',this.value)">
      </div>

      <div style="grid-column:1/-1">
        <button class="btn primary" onclick="saveProfiles()">Sauvegarder profils (local)</button>
        <span class="subnote">Sauvegarde dans le navigateur (localStorage), pas sur ESP32.</span>
      </div>
    </div>
  `;
}

function lockSingleRebuild(){
  app.lockSingleRebuildUntilMs = Date.now() + 2500;
}

function openSingle(id, openProfile=false){
  app.selectedId = id;
  showView("single");
  document.getElementById("singleSelect").value = id;
  app.uiDetailsOpen[`${id}:profile`] = !!openProfile;
  app.uiDetailsOpen[`${id}:viz`] = (app.viz !== "none");
  renderAll();
}

function setProfile(id, key, value){
  const p = participants.find(x => x.id === id);
  if (!p) return;
  if (!p.profile) p.profile = {};
  p.profile[key] = value;
}

function saveProfiles(){
  const payload = participants.map(p => ({ id: p.id, profile: p.profile }));
  localStorage.setItem("bracelet_profiles", JSON.stringify(payload));
}

function loadProfiles(){
  try{
    const raw = localStorage.getItem("bracelet_profiles");
    if (!raw) return;
    const payload = JSON.parse(raw);
    payload.forEach(x => {
      const p = participants.find(p2 => p2.id === x.id);
      if (p && x.profile) p.profile = { ...p.profile, ...x.profile };
    });
  } catch(e){}
}

function populateSingleSelect(){
  const sel = document.getElementById("singleSelect");
  sel.innerHTML = participants.map(p => `<option value="${p.id}">${escapeHtml(p.name)} (${p.id})</option>`).join("");
  sel.value = app.selectedId;
}

async function refreshLive(){
  try{
    const r = await fetch(`${BASE}/api/state`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);

    const raw = await r.text();
    const safe = raw.replace(/\bnan\b/gi, "null");
    const s = JSON.parse(safe);

    setConn(true);

    const p = participants.find(x => x.id === "p001");
    if (p){
      p.vitals.tempC = (s.tempC == null) ? null : Number(s.tempC);
      p.vitals.humPct = (s.humPct == null) ? null : Number(s.humPct);
      p.vitals.adcRaw = (s.adcRaw == null) ? null : Number(s.adcRaw);
      p.vitals.led = !!s.led;
      p.vitals.buzzer = !!s.buzzer;
      p.vitals.touch = s.touch;
      p.lastTs = new Date().toLocaleTimeString();

      const now = new Date().toLocaleTimeString();
      const t = (s.tempC == null) ? null : Number(s.tempC);
      const h = (s.humPct == null) ? null : Number(s.humPct);
      const c = (s.adcRaw == null) ? null : Number(s.adcRaw);

      if (t != null || h != null || c != null){
        chartX.push(now);
        chartY.push({ t, h, c });
        if (chartX.length > 60){
          chartX.shift();
          chartY.shift();
        }
      }
    }

    renderAll();
  } catch(e){
    console.log("Refresh error:", e);
    setConn(false);
    const p = participants.find(x => x.id === "p001");
    if (p){
      p.lastTs = "-";
      p.vitals = { tempC: null, humPct: null, adcRaw: null, touch: null, led: null, buzzer: null };
    }
    renderAll();
  }
}

function startTimer(){
  if (app.timer) clearInterval(app.timer);
  app.timer = setInterval(refreshLive, app.refreshMs);
}

function toggleBubble(){
  app.bubbleMin = !app.bubbleMin;
  document.getElementById("bubbleBody").classList.toggle("hidden", app.bubbleMin);
  document.getElementById("bubbleMinBtn").textContent = app.bubbleMin ? "Open" : "Min";
}

function ledOn(){ fetch(`${BASE}/led?state=1`, { cache: "no-store" }).then(() => refreshLive()); }
function ledOff(){ fetch(`${BASE}/led?state=0`, { cache: "no-store" }).then(() => refreshLive()); }
function buzzerOn(){ fetch(`${BASE}/buzzer?state=1`, { cache: "no-store" }).then(() => refreshLive()); }
function buzzerOff(){ fetch(`${BASE}/buzzer?state=0`, { cache: "no-store" }).then(() => refreshLive()); }

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

document.getElementById("searchInput").addEventListener("input", (e) => {
  app.search = e.target.value;
  renderAll();
});

document.getElementById("displaySelect").addEventListener("change", (e) => {
  app.displayMode = e.target.value;
  renderAll();
});

populateSingleSelect();
document.getElementById("singleSelect").addEventListener("change", (e) => {
  app.selectedId = e.target.value;
  renderAll();
});

document.getElementById("vizSelect").addEventListener("change", (e) => {
  app.viz = e.target.value;
  app.uiDetailsOpen[`${app.selectedId}:viz`] = (app.viz !== "none");
  renderAll();
});

function applyBg(){
  const c = document.getElementById("bgColor").value;
  document.documentElement.style.setProperty("--bg", c);
}

function applyTiles(){
  const a = document.getElementById("tileA").value;
  const b = document.getElementById("tileB").value;
  const c = document.getElementById("tileC").value;
  const d = document.getElementById("tileD").value;

  document.documentElement.style.setProperty("--tileA", hexToRgba(a, 0.18));
  document.documentElement.style.setProperty("--tileB", hexToRgba(b, 0.12));
  document.documentElement.style.setProperty("--tileC", hexToRgba(c, 0.14));
  document.documentElement.style.setProperty("--tileD", hexToRgba(d, 0.10));
}

function hexToRgba(hex, alpha){
  const h = hex.replace("#","");
  const r = parseInt(h.substring(0,2),16);
  const g = parseInt(h.substring(2,4),16);
  const b = parseInt(h.substring(4,6),16);
  return `rgba(${r},${g},${b},${alpha})`;
}

loadProfiles();
showView("global");
startTimer();
refreshLive();