"use strict";

// ------------------------------------------------------------------ estado
const state = {
  range: "24h", service: "", category: "", status: "open", q: "", sort: "recent",
  issue: null, eventId: null, lastRunId: undefined, lastNewCheck: Date.now(),
  status_: null, notify: false,
};
const charts = {};
const serviceSlots = new Map();     // cor acompanha o serviço, não a posição no ranking
const $ = (id) => document.getElementById(id);

const appName = () => state.status_?.app_name || "CloudWatch Monitor";
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sem storage */ } },
};

// ------------------------------------------------------------------ utilidades
async function api(path, opts = {}) {
  const method = opts.method || "GET";
  // POST sempre como JSON: o servidor recusa outros formatos (proteção contra CSRF)
  const res = await fetch(path, {
    method,
    signal: opts.signal,
    headers: method === "GET" ? undefined : { "Content-Type": "application/json" },
    body: method === "GET" ? undefined : JSON.stringify(opts.body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const nf = new Intl.NumberFormat("pt-BR");
const pad = (n) => String(n).padStart(2, "0");

function fmtTime(ms, withDate = true) {
  if (!ms) return "–";
  const d = new Date(ms);
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withDate ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${t}` : t;
}
function fmtFull(ms) {
  if (!ms) return "–";
  const d = new Date(ms);
  return `${fmtTime(ms)}:${pad(d.getSeconds())}`;
}
function relTime(ms) {
  if (!ms) return "–";
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}
function qs(extra = {}) {
  const p = new URLSearchParams({ range: state.range });
  if (state.service) p.set("service", state.service);
  if (state.category) p.set("category", state.category);
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== "") p.set(k, v);
  return p.toString();
}
function toast(html, kind = "", onClick) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = html;
  el.onclick = () => { el.remove(); onClick && onClick(); };
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 9000);
}
function serviceColor(name) {
  if (name === "Outros") return css("--other");
  if (!serviceSlots.has(name)) serviceSlots.set(name, (serviceSlots.size % 8) + 1);
  return css(`--s${serviceSlots.get(name)}`);
}
function statusBadge(kind, label, icon) {
  return `<span class="status ${kind}"><span class="ic" aria-hidden="true">${icon}</span>${esc(label)}</span>`;
}
const ALARM_BADGE = {
  ALARM: () => statusBadge("critical", "ALARME", "!"),
  OK: () => statusBadge("good", "OK", "✓"),
  INSUFFICIENT_DATA: () => statusBadge("warning", "Sem dados", "?"),
};
function issueBadge(it) {
  if (it.status === "resolved") return statusBadge("good", "Resolvida", "✓");
  if (it.status === "ignored") return statusBadge("neutral", "Ignorada", "–");
  if (it.regressed_at) return statusBadge("critical", "Regressão", "↺");
  return statusBadge("serious", "Aberta", "!");
}
function consoleUrl(group, stream) {
  const enc = (s) => encodeURIComponent(s).replace(/%/g, "$25");
  const region = state.status_?.region || "us-east-1";
  let url = `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#logsV2:log-groups/log-group/${enc(group)}`;
  if (stream) url += `/log-events/${enc(stream)}`;
  return url;
}

// ------------------------------------------------------------------ gráficos
function baseOptions() {
  const ink2 = css("--ink-2"), muted = css("--muted"), grid = css("--grid");
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { labels: { color: ink2, usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, boxHeight: 10, font: { size: 12 } } },
      tooltip: {
        backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: ink2,
        borderColor: css("--axis"), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
      },
    },
    scales: {
      x: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: muted, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 12 } },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: muted, font: { size: 11 }, precision: 0 } },
    },
  };
}
// Atualiza o gráfico existente no lugar (sem destruir/recriar), para nada "piscar" ao trocar filtro.
function upsertChart(key, canvasId, config) {
  const chart = charts[key];
  if (chart && chart.config.type === config.type) {
    // Hover/tooltip ativos apontam para barras dos dados antigos: limpa antes de trocar.
    chart.setActiveElements([]);
    chart.tooltip?.setActiveElements([], { x: 0, y: 0 });
    chart.data = config.data;
    chart.options = config.options;
    try {
      chart.update("none");
      return;
    } catch (e) {
      console.warn("recriando gráfico", key, e);   // última defesa: nunca deixar congelado
      chart.destroy();
    }
  } else if (chart) {
    chart.destroy();
  }
  charts[key] = new Chart($(canvasId), config);
}
const withAlpha = (hex, alpha) => hex + alpha;   // "#2a78d6" + "55"
function bucketLabels(labels) {
  const long = state.range === "7d" || state.range === "30d";
  return labels.map((ms) => fmtTime(ms, long));
}

function renderTimeline(data) {
  const empty = !data.series.length;
  $("timelineEmpty").classList.toggle("hidden", !empty);
  $("timelineChart").parentElement.classList.toggle("hidden", empty);
  if (empty) return;
  const surface = css("--surface");
  const opts = baseOptions();
  opts.scales.x.stacked = true;
  opts.scales.y.stacked = true;
  opts.plugins.legend.position = "top";
  opts.plugins.legend.align = "start";
  // Legenda filtra (igual à barra), em vez de esconder a série
  opts.plugins.legend.onClick = (_, item) => {
    const name = data.series[item.datasetIndex]?.name;
    if (name && name !== "Outros") setFilter("service", name);
  };
  opts.plugins.legend.onHover = (e) => { e.native.target.style.cursor = "pointer"; };
  opts.plugins.legend.onLeave = (e) => { e.native.target.style.cursor = "default"; };
  opts.onHover = (e, els) => { if (e.native) e.native.target.style.cursor = els.length ? "pointer" : "default"; };
  // Os callbacks recebem lista vazia quando o filtro tira todos os itens (horário sem erros):
  // nunca acessar items[0] sem checar, senão o Chart.js para de desenhar.
  opts.plugins.tooltip.callbacks = {
    title: (items) => {
      if (!items.length) return "";
      const start = data.labels[items[0].dataIndex];
      return `${fmtTime(start)} – ${fmtTime(start + data.bucket_ms, false)}`;
    },
    footer: (items) => (items.length ? `Total: ${nf.format(items.reduce((a, i) => a + i.parsed.y, 0))}` : ""),
  };
  opts.plugins.tooltip.filter = (item) => item.parsed.y > 0;
  opts.onClick = (_, els) => {
    if (!els.length) return;
    const name = data.series[els[0].datasetIndex].name;
    if (name !== "Outros") setFilter("service", name);
  };
  upsertChart("timeline", "timelineChart", {
    type: "bar",
    data: {
      labels: bucketLabels(data.labels),
      datasets: data.series.map((s) => ({
        label: `${s.name} (${nf.format(s.total)})`, data: s.data,
        backgroundColor: serviceColor(s.name), borderColor: surface,
        borderWidth: { top: 2, bottom: 0, left: 0, right: 0 }, borderSkipped: false,
        barPercentage: 0.92, categoryPercentage: 0.92,
      })),
    },
    options: opts,
  });
}

function hbar(key, canvasId, rows, labelOf, onPick, selected) {
  const opts = baseOptions();
  opts.indexAxis = "y";
  opts.interaction = { mode: "nearest", axis: "y", intersect: false };
  opts.plugins.legend = { display: false };
  opts.plugins.tooltip.callbacks = {
    label: (i) => ` ${nf.format(i.parsed.x)} erros`,
    footer: (items) => (!items.length ? "" : rows[items[0].dataIndex]?.name === selected ? "clique para limpar o filtro" : "clique para filtrar"),
  };
  const color = css("--s1");
  // Com filtro ativo, a barra selecionada fica forte e as outras esmaecidas (continuam clicáveis)
  const colors = rows.map((r) => (!selected || r.name === selected ? color : withAlpha(color, "55")));
  opts.scales = {
    x: { beginAtZero: true, grid: { color: css("--grid") }, border: { display: false }, ticks: { color: css("--muted"), precision: 0, font: { size: 11 } } },
    y: { grid: { display: false }, border: { color: css("--axis") }, ticks: { color: css("--ink-2"), font: { size: 12 }, callback(v) { const s = this.getLabelForValue(v); return s.length > 42 ? s.slice(0, 41) + "…" : s; } } },
  };
  opts.onClick = (_, els) => els.length && onPick(rows[els[0].index]);
  opts.onHover = (e, els) => { if (e.native) e.native.target.style.cursor = els.length ? "pointer" : "default"; };
  upsertChart(key, canvasId, {
    type: "bar",
    data: { labels: rows.map(labelOf), datasets: [{ data: rows.map((r) => r.n), backgroundColor: colors, borderRadius: 4, borderSkipped: "start", barPercentage: 0.75, categoryPercentage: 0.9 }] },
    options: opts,
  });
}

function renderBreakdown(data) {
  state.knownServices = [...new Set([...(state.knownServices || []), ...data.services.map((s) => s.name)])];
  $("servicesBox").style.height = `${Math.max(140, data.services.length * 26 + 40)}px`;
  hbar("services", "servicesChart", data.services, (r) => r.name, (r) => setFilter("service", r.name), state.service);
  hbar("categories", "categoriesChart", data.categories, (r) => r.label, (r) => setFilter("category", r.name), state.category);

  const sel = $("serviceFilter");
  const current = state.service;
  const names = new Set(data.services.map((s) => s.name));
  if (current) names.add(current);
  sel.innerHTML = `<option value="">Todos os serviços</option>` +
    [...names].sort().map((n) => `<option ${n === current ? "selected" : ""}>${esc(n)}</option>`).join("");
  const csel = $("categoryFilter");
  csel.innerHTML = `<option value="">Todas as categorias</option>` +
    Object.entries(CATEGORY_LABELS).map(([k, v]) => `<option value="${k}" ${k === state.category ? "selected" : ""}>${v}</option>`).join("");
}

// ------------------------------------------------------------------ blocos
function renderSummary(s) {
  $("kTotal").textContent = nf.format(s.total);
  const diff = s.total - s.previous;
  if (s.previous === 0 && s.total === 0) $("kDelta").textContent = "igual ao período anterior";
  else if (s.previous === 0) $("kDelta").innerHTML = `<span class="up">▲ novo</span> vs período anterior (0)`;
  else {
    const pct = Math.round((diff / s.previous) * 100);
    const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
    const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "=";
    $("kDelta").innerHTML = `<span class="${cls}">${arrow} ${Math.abs(pct)}%</span> vs anterior (${nf.format(s.previous)})`;
  }
  $("kOpen").textContent = nf.format(s.open_issues);
  $("kRegress").textContent = s.regressions ? `${s.regressions} regressão(ões) no período` : "";
  $("kNew").textContent = nf.format(s.new_issues);
  $("kServices").textContent = nf.format(s.services);
  $("kAlarms").textContent = nf.format(s.alarms_in_alarm);
  $("kAlarmsTile").classList.toggle("alert", s.alarms_in_alarm > 0);
  const app = appName();
  document.title = s.alarms_in_alarm ? `🔴 (${s.alarms_in_alarm}) ${app}` : `(${s.open_issues}) ${app}`;
}

function renderAlarms(data) {
  const items = data.items;
  state.alarmItems = items;
  const bad = items.filter((a) => a.state === "ALARM" && !a.autoscaling).length;
  const auto = items.filter((a) => a.autoscaling).length;
  $("alarmsCount").textContent = items.length ? `${bad} em alarme · ${items.length} no total${auto ? ` (${auto} de auto scaling)` : ""}` : "";
  $("kAlarmsSub").textContent = items.length ? `de ${items.length - auto} alarmes` : "nenhum alarme";
  // alarmes de auto scaling (Target Tracking) ficam em ALARM o tempo todo: selo neutro, não vermelho
  $("alarmsList").innerHTML = items.length ? items.map((a) => `
    <li class="alarm-item" data-name="${esc(a.name)}" tabindex="0" title="Clique para entender este alarme">
      ${a.autoscaling ? statusBadge("neutral", "Auto scaling", "↕") : (ALARM_BADGE[a.state] || (() => statusBadge("neutral", a.state, "?")))()}
      <div><div class="alarm-name">${esc(a.name)}</div>
      <div class="alarm-meta">${esc(a.namespace)} · ${esc(a.metric)} · desde ${fmtTime(a.state_updated_at)}</div></div>
    </li>`).join("") : `<li class="muted">Nenhum alarme configurado.</li>`;
}

function renderIssues(data) {
  const items = data.items;
  $("issuesEmpty").classList.toggle("hidden", items.length > 0);
  const max = Math.max(1, ...items.map((i) => i.range_count));
  const rangeStart = Date.now() - { "1h": 3.6e6, "6h": 2.16e7, "24h": 8.64e7, "7d": 6.048e8, "30d": 2.592e9 }[state.range];
  $("issuesBody").innerHTML = items.map((it) => `
    <tr data-fp="${it.fingerprint}">
      <td><div class="issue-title">${esc(it.title)}</div>
        <div class="issue-sub">${issueBadge(it)}${it.first_seen >= rangeStart ? '<span class="pill">nova</span>' : ""}<span>${esc(it.log_group)}</span></div></td>
      <td class="svc">${esc(it.service)}</td>
      <td><span class="pill">${esc(it.category_label)}</span></td>
      <td class="num"><div class="bar-cell"><span class="bar-mini" style="width:${Math.round((it.range_count / max) * 60)}px"></span>${nf.format(it.range_count)}</div></td>
      <td class="num">${nf.format(it.count)}</td>
      <td class="when" title="${fmtFull(it.last_seen)}">${relTime(it.last_seen)}</td>
      <td class="when" title="${fmtFull(it.first_seen)}">${fmtTime(it.first_seen)}</td>
    </tr>`).join("");
}

// ------------------------------------------------------------------ carga
// Cada clique dispara uma carga nova e cancela a anterior; só a resposta mais recente é desenhada.
let loadSeq = 0;
let loadAbort = null;
async function loadAll() {
  const seq = ++loadSeq;
  loadAbort?.abort();
  loadAbort = new AbortController();
  const signal = loadAbort.signal;
  renderActiveFilters();
  document.body.classList.add("is-loading");
  try {
    const [summary, timeline, breakdown, issues, alarms] = await Promise.all([
      api(`/api/summary?${qs()}`, { signal }), api(`/api/timeline?${qs()}`, { signal }),
      api(`/api/breakdown?${qs()}`, { signal }),
      api(`/api/issues?${qs({ status: state.status, q: state.q, sort: state.sort })}`, { signal }),
      api("/api/alarms", { signal }),
    ]);
    if (seq !== loadSeq) return;
    renderSummary(summary);
    renderTimeline(timeline);
    renderBreakdown(breakdown);
    renderIssues(issues);
    renderAlarms(alarms);
    $("lastRefresh").textContent = `painel atualizado às ${fmtTime(Date.now(), false)}`;
    if (state.issue) loadIssue(state.issue, false);
  } catch (e) {
    if (e.name === "AbortError" || seq !== loadSeq) return;
    toast(`Falha ao carregar o painel: ${esc(e.message)}`, "critical");
  } finally {
    if (seq === loadSeq) document.body.classList.remove("is-loading");
  }
}
let searchTimer;
let issuesSeq = 0;
function loadIssuesOnly() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const seq = ++issuesSeq;
    const data = await api(`/api/issues?${qs({ status: state.status, q: state.q, sort: state.sort })}`);
    if (seq === issuesSeq) renderIssues(data);
  }, 200);
}
// Clicar no item já filtrado limpa o filtro; clicar em outro troca.
function setFilter(key, value) {
  state[key] = state[key] === value ? "" : value;
  loadAll();
}
const CATEGORY_LABELS = { timeout: "Timeout", http_5xx: "HTTP 5xx", fatal: "Fatal / Crash", exception: "Exceção", error: "Erro" };
function renderActiveFilters() {
  const chips = [];
  if (state.service) chips.push(`<button class="chip filter" data-clear="service" title="Remover filtro">Serviço: ${esc(state.service)}<span class="x">✕</span></button>`);
  if (state.category) chips.push(`<button class="chip filter" data-clear="category" title="Remover filtro">Categoria: ${esc(CATEGORY_LABELS[state.category] || state.category)}<span class="x">✕</span></button>`);
  if (chips.length > 1) chips.push(`<button class="chip" data-clear="all">limpar tudo</button>`);
  $("activeFilters").innerHTML = chips.join("");
}

// ------------------------------------------------------------------ status da coleta
async function pollStatus() {
  try {
    const s = await api("/api/status");
    state.status_ = s;
    const c = s.collector || {};
    const run = s.last_run;
    $("appName").textContent = s.app_name;
    $("envInfo").textContent = s.demo
      ? `Modo demonstração · dados fictícios · ${s.groups_monitored} log groups`
      : `CloudWatch · ${s.profile} · conta ${s.account || "…"} · ${s.region} · ${s.groups_monitored} log groups`;
    $("collectNow").classList.toggle("hidden", !!s.demo);

    const sel = $("intervalSelect");
    const choices = [...new Set([...s.interval_choices, s.interval_min])].sort((a, b) => a - b);
    const html = choices.map((m) => `<option value="${m}" ${m === s.interval_min ? "selected" : ""}>${m} min</option>`).join("");
    if (sel.dataset.html !== html && document.activeElement !== sel) { sel.innerHTML = html; sel.dataset.html = html; }

    const runId = run ? run.id : null;
    if (runId !== state.lastRunId) {
      const first = state.lastRunId === undefined;
      state.lastRunId = runId;
      if (!first) { await loadAll(); checkNew(); }
      if (!first && run?.status === "error") toast(`Coleta falhou: ${esc(run.error || "")}`, "critical");
    }
    state.collector = c;
    renderCollector();
    renderBackfill(s.backfill);
    if (typeof onAlerts === "function") onAlerts(s.alerts);  // alarmes na tela (alerts.js)
  } catch {
    const el = $("collectorStatus");
    el.className = "collector error";
    el.querySelector(".label").textContent = "servidor local fora do ar";
  }
}
function renderCollector() {
  const c = state.collector || {};
  const run = state.status_?.last_run;
  const el = $("collectorStatus");
  const label = el.querySelector(".label");
  if (c.running) {
    el.className = "collector running";
    label.textContent = `coletando · ${c.phase}`;
    $("collectNow").disabled = true;
    return;
  }
  $("collectNow").disabled = false;
  const st = run?.status || "ok";
  el.className = `collector ${st}`;
  const remaining = c.next_run ? Math.max(0, Math.round(c.next_run - Date.now() / 1000)) : null;
  const next = remaining === null ? "" : ` · próxima em ${Math.floor(remaining / 60)}:${pad(remaining % 60)}`;
  const word = { ok: "ok", partial: "parcial", error: "erro" }[st] || st;
  label.textContent = run ? `coleta ${word} ${relTime(run.finished_at)}${next}` : `aguardando 1ª coleta${next}`;
  el.title = run?.error || `${run?.rows_found ?? 0} linhas · ${run?.events_new ?? 0} eventos novos`;
}

// ------------------------------------------------------------------ lacunas / recuperação
function fmtDuration(hours) {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${Math.round(hours * 10) / 10} h`;
  return `${Math.round((hours / 24) * 10) / 10} dias`;
}
function toLocalInput(ms) {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function renderBackfill(b) {
  const el = $("backfillBanner");
  const job = b?.job, pending = b?.pending;
  if (job) {
    el.className = "banner running";
    el.innerHTML = `<div class="grow"><strong>Recuperando histórico</strong> · ${fmtTime(job.start)} → ${fmtTime(job.end)}
      · ${job.progress}% (coletado até ${fmtTime(job.cursor)})
      ${job.error ? `· <span class="muted">falhou um bloco, tentando de novo em 1 min: ${esc(job.error)}</span>` : ""}
      <div class="progress"><span style="width:${job.progress}%"></span></div></div>
      <button class="btn" data-action="cancel-backfill">Cancelar</button>`;
    if (state.jobCursor !== undefined && state.jobCursor !== job.cursor) loadAll();
    state.jobCursor = job.cursor;
  } else if (pending) {
    el.className = "banner";
    el.innerHTML = `<div class="grow"><strong>Há logs não coletados:</strong> o monitor ficou parado de
      ${fmtFull(pending.start)} até ${fmtFull(pending.end)} (${fmtDuration(pending.hours)}).</div>
      <button class="btn primary" data-action="open-gap">Decidir o que recuperar</button>`;
  } else {
    el.className = "banner hidden";
  }
  if (state.jobCursor !== undefined && !job) {
    state.jobCursor = undefined;
    toast("Recuperação do histórico concluída.", "good");
    loadAll();
  }
  state.pendingGap = pending || null;
  if (pending && state.gapPromptedFor !== pending.start) {
    state.gapPromptedFor = pending.start;
    openGapModal();
  }
}
function openGapModal() {
  const p = state.pendingGap;
  if (!p) return;
  $("gapText").innerHTML = `A última coleta cobriu até <strong>${fmtFull(p.start)}</strong>. Ficaram
    <strong>${fmtDuration(p.hours)}</strong> sem coletar (até ${fmtFull(p.end)}). O que você quer buscar?`;
  $("gapAllInfo").textContent = `${fmtFull(p.start)} até ${fmtFull(p.end)} · ${fmtDuration(p.hours)}`;
  const f = $("gapForm");
  f.elements.from.value = toLocalInput(p.start);
  f.elements.from.min = toLocalInput(p.start);
  f.elements.from.max = toLocalInput(p.end);
  f.elements.hours.max = Math.ceil(p.hours);
  $("gapMsg").textContent = "";
  $("gapModal").classList.add("show");
}
async function confirmGap() {
  const f = $("gapForm");
  const mode = f.elements.mode.value;
  const body = { mode, remember: f.elements.remember.checked };
  if (mode === "hours") body.hours = Number(f.elements.hours.value);
  if (mode === "from") body.from_ms = new Date(f.elements.from.value).getTime();
  try {
    await api("/api/backfill", { method: "POST", body });
    $("gapModal").classList.remove("show");
    toast(mode === "skip" ? "Ok, seguindo só daqui para frente." : "Recuperação iniciada em segundo plano.", "good");
    pollStatus();
  } catch (e) {
    $("gapMsg").textContent = e.message;
  }
}

async function checkNew() {
  try {
    const since = state.lastNewCheck;
    state.lastNewCheck = Date.now();
    const { items } = await api(`/api/issues/new?since=${since}`);
    for (const it of items.slice(0, 5)) {
      const kind = it.regressed_at > since ? "Regressão" : "Nova issue";
      toast(`<strong>${kind}</strong> · ${esc(it.service)}<br><span class="muted">${esc(it.title)}</span>`, "critical", () => loadIssue(it.fingerprint));
      if (state.notify && "Notification" in window && Notification.permission === "granted") {
        const n = new Notification(`${kind}: ${it.service}`, { body: it.title, tag: it.fingerprint });
        n.onclick = () => { window.focus(); loadIssue(it.fingerprint); };
      }
    }
    if (items.length > 5) toast(`+${items.length - 5} issues novas`, "critical");
  } catch { /* próxima rodada tenta de novo */ }
}

// ------------------------------------------------------------------ detalhe da issue
async function loadIssue(fp, openDrawer = true) {
  try {
    const data = await api(`/api/issue/${fp}?${qs()}`);
    const it = data.issue;
    const changed = state.issue !== fp;
    state.issue = fp;
    $("dTitle").textContent = it.title;
    $("dMeta").innerHTML = `${issueBadge(it)}<span class="pill">${esc(it.category_label)}</span><span class="pill">${esc(it.kind)}</span><span class="pill">${esc(it.service)}</span>`;
    $("dFacts").innerHTML = [
      ["Ocorrências (total)", nf.format(it.count)],
      ["Primeira vez", fmtFull(it.first_seen)],
      ["Última vez", `${fmtFull(it.last_seen)} (${relTime(it.last_seen)})`],
      ["Log group", it.log_group],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
    $("btnResolve").classList.toggle("hidden", it.status === "resolved");
    $("btnIgnore").classList.toggle("hidden", it.status === "ignored");
    $("btnReopen").classList.toggle("hidden", it.status === "open");

    const opts = baseOptions();
    opts.plugins.legend = { display: false };
    opts.plugins.tooltip.callbacks = { label: (i) => ` ${nf.format(i.parsed.y)} ocorrências` };
    upsertChart("issue", "issueChart", {
      type: "bar",
      data: { labels: bucketLabels(data.timeline.labels), datasets: [{ data: data.timeline.data, backgroundColor: css("--s1"), borderRadius: 4, borderSkipped: "start" }] },
      options: opts,
    });

    $("dEvents").innerHTML = data.events.map((e) => `
      <li data-id="${e.id}"><div class="ev-head"><span>${fmtFull(e.ts)}</span><span class="ev-stream">${esc(e.log_stream)}</span></div>
      <div class="ev-prev">${esc(e.preview)}</div></li>`).join("");
    if (changed || !data.events.some((e) => e.id === state.eventId)) {
      if (data.events.length) await showEvent(data.events[0].id);
    } else {
      markActiveEvent();
    }
    if (openDrawer) {
      $("drawer").classList.add("open");
      $("drawer").setAttribute("aria-hidden", "false");
      $("backdrop").classList.add("show");
    }
  } catch (e) {
    toast(`Não consegui abrir a issue: ${esc(e.message)}`, "critical");
  }
}
function markActiveEvent() {
  document.querySelectorAll("#dEvents li").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === state.eventId));
}
async function showEvent(id) {
  const ev = await api(`/api/event/${id}`);
  state.eventId = ev.id;
  $("dMessage").textContent = ev.message;
  $("dEventInfo").textContent = `· ${fmtFull(ev.ts)} · ${ev.log_stream}`;
  $("btnConsole").href = consoleUrl(ev.log_group, ev.log_stream);
  markActiveEvent();
}
function closeDrawer() {
  state.issue = null;
  state.eventId = null;
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("backdrop").classList.remove("show");
}
async function setIssueStatus(status) {
  try {
    await api(`/api/issue/${state.issue}/status`, { method: "POST", body: { status } });
    toast({ resolved: "Issue marcada como resolvida. Se voltar, vira regressão.", ignored: "Issue ignorada — não aparece mais nos gráficos.", open: "Issue reaberta." }[status], "good");
    await loadAll();
  } catch (e) {
    toast(`Erro: ${esc(e.message)}`, "critical");
  }
}

// ------------------------------------------------------------------ prompt
async function generatePrompt() {
  const modal = $("promptModal");
  modal.classList.add("show");
  $("promptText").value = "Gerando prompt… (buscando o contexto completo do log na AWS)";
  $("promptInfo").textContent = "";
  try {
    const mask = $("maskToggle").checked ? "1" : "0";
    // origem do prompt: uma issue (painel de detalhe) ou uma linha achada na busca
    const src = state.promptSource || { kind: "issue" };
    if (src.kind === "text") {
      $("promptText").value = src.text;
      $("promptInfo").textContent = `${nf.format(src.text.length)} caracteres`;
      return;
    }
    const { prompt } = src.kind === "line"
      ? await api("/api/search/prompt", { method: "POST", body: { ...src.item, mask: mask === "1" } })
      : await api(`/api/issue/${state.issue}/prompt?event_id=${state.eventId || ""}&mask=${mask}`);
    $("promptText").value = prompt;
    $("promptInfo").textContent = `${nf.format(prompt.length)} caracteres`;
  } catch (e) {
    $("promptText").value = `Erro ao gerar o prompt: ${e.message}`;
  }
}
async function copyPrompt() {
  const text = $("promptText").value;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    $("promptText").select();
    document.execCommand("copy");
  }
  toast("Prompt copiado! Cole no Claude.", "good");
}

// ------------------------------------------------------------------ configurações
async function openSettings() {
  const [s, runs] = await Promise.all([api("/api/settings"), api("/api/runs")]);
  const form = $("settingsForm");
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") el.checked = s[el.name] === "1";
    else el.value = s[el.name] ?? "";
  }
  $("notifToggle").checked = state.notify;
  $("settingsMsg").textContent = "";
  $("dbInfo").textContent = `Banco: ${state.status_?.db_path || ""}`;
  $("runsBody").innerHTML = runs.items.map((r) => `
    <tr><td class="when">${fmtFull(r.started_at)}</td>
    <td>${r.kind === "backfill" ? "recuperação" : "ao vivo"}</td>
    <td class="when">${r.window_start ? `${fmtTime(r.window_start)} → ${fmtTime(r.window_end)}` : "–"}</td>
    <td>${{ ok: statusBadge("good", "ok", "✓"), partial: statusBadge("warning", "parcial", "!"), error: statusBadge("critical", "erro", "✕"), running: statusBadge("neutral", "rodando", "…") }[r.status] || esc(r.status)}</td>
    <td class="num">${r.groups ?? "–"}</td><td class="num">${r.rows_found ?? "–"}</td><td class="num">${r.events_new ?? "–"}</td><td class="num">${r.issues_new ?? "–"}</td>
    <td class="small">${esc(r.error || "")}</td></tr>`).join("");
  $("settingsModal").classList.add("show");
}
async function saveSettings() {
  const body = {};
  for (const el of $("settingsForm").elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
  }
  try {
    await api("/api/settings", { method: "POST", body });
    await setNotify($("notifToggle").checked);
    $("settingsModal").classList.remove("show");
    toast("Configurações salvas. Valem a partir da próxima coleta.", "good");
    pollStatus();
  } catch (e) {
    $("settingsMsg").textContent = e.message;
  }
}
async function setNotify(on) {
  if (on && "Notification" in window && Notification.permission !== "granted") {
    on = (await Notification.requestPermission()) === "granted";
  }
  state.notify = on;
  store.set("notify", on);
}

// ------------------------------------------------------------------ tema
function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}
function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  store.set("theme", next);
  loadAll();
}

// ------------------------------------------------------------------ eventos
function bind() {
  $("rangeSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.range = b.dataset.range;
    store.set("range", state.range);
    document.querySelectorAll("#rangeSeg button").forEach((x) => x.classList.toggle("active", x === b));
    loadAll();
  });
  $("statusSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.status = b.dataset.status;
    document.querySelectorAll("#statusSeg button").forEach((x) => x.classList.toggle("active", x === b));
    loadIssuesOnly();
  });
  $("serviceFilter").addEventListener("change", (e) => { state.service = e.target.value; loadAll(); });
  $("categoryFilter").addEventListener("change", (e) => { state.category = e.target.value; loadAll(); });
  $("activeFilters").addEventListener("click", (e) => {
    const key = e.target.closest("[data-clear]")?.dataset.clear;
    if (!key) return;
    if (key === "all" || key === "service") state.service = "";
    if (key === "all" || key === "category") state.category = "";
    loadAll();
  });
  $("search").addEventListener("input", (e) => { state.q = e.target.value.trim(); loadIssuesOnly(); });
  $("sortSelect").addEventListener("change", (e) => { state.sort = e.target.value; loadIssuesOnly(); });
  $("issuesBody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-fp]"); if (tr) loadIssue(tr.dataset.fp);
  });
  $("dEvents").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]"); if (li) showEvent(Number(li.dataset.id));
  });
  $("closeDrawer").addEventListener("click", closeDrawer);
  $("backdrop").addEventListener("click", closeDrawer);
  $("btnResolve").addEventListener("click", () => setIssueStatus("resolved"));
  $("btnIgnore").addEventListener("click", () => setIssueStatus("ignored"));
  $("btnReopen").addEventListener("click", () => setIssueStatus("open"));
  $("btnPrompt").addEventListener("click", () => { state.promptSource = { kind: "issue" }; generatePrompt(); });
  $("maskToggle").addEventListener("change", generatePrompt);
  $("copyPrompt").addEventListener("click", copyPrompt);
  $("collectNow").addEventListener("click", async () => {
    await api("/api/collect", { method: "POST" });
    toast("Coleta iniciada…");
    setTimeout(pollStatus, 500);
  });
  $("intervalSelect").addEventListener("change", async (e) => {
    await api("/api/settings", { method: "POST", body: { interval_min: e.target.value } });
    toast(`Coleta a cada ${e.target.value} min.`, "good");
    pollStatus();
  });
  $("backfillBanner").addEventListener("click", async (e) => {
    const action = e.target.closest("[data-action]")?.dataset.action;
    if (action === "open-gap") openGapModal();
    if (action === "cancel-backfill" && confirm("Cancelar a recuperação do histórico? O que já foi coletado fica salvo.")) {
      await api("/api/backfill/cancel", { method: "POST" });
      state.jobCursor = undefined;
      pollStatus();
    }
  });
  $("gapConfirm").addEventListener("click", confirmGap);
  $("gapForm").addEventListener("focusin", (e) => {
    const opt = e.target.closest(".opt");
    if (opt && e.target.type !== "radio") opt.querySelector("input[type=radio]").checked = true;
  });
  $("openSettings").addEventListener("click", openSettings);
  $("saveSettings").addEventListener("click", saveSettings);
  $("themeToggle").addEventListener("click", toggleTheme);
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => {
    if (e.target === m || e.target.closest("[data-close]")) m.classList.remove("show");
  }));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = document.querySelector(".modal.show");
    if (open) open.classList.remove("show"); else closeDrawer();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => loadAll());
}

// ------------------------------------------------------------------ início
(async function init() {
  applyTheme(store.get("theme", null));
  state.range = store.get("range", "24h");
  state.notify = store.get("notify", false) && "Notification" in window && Notification.permission === "granted";
  document.querySelectorAll("#rangeSeg button").forEach((x) => x.classList.toggle("active", x.dataset.range === state.range));
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  bind();
  await pollStatus();
  await loadAll();
  const deep = location.hash.match(/^#issue=([0-9a-f]{16})$/);
  if (deep) loadIssue(deep[1]);
  setInterval(pollStatus, 5000);
  setInterval(renderCollector, 1000);
  setInterval(loadAll, 60000);
})();
