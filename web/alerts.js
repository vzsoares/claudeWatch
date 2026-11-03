"use strict";
// Alarmes na tela: aviso grande no meio da tela (borda piscando) + opcional notificação do sistema e som.
// Usa helpers globais de app.js (api, esc, $, toast, fmtFull, relTime, loadIssue, setFilter, state).

const alarm = {
  data: null,          // resposta de /api/alerts (regras, histórico…)
  active: [],          // disparos não reconhecidos
  notify: true, sound: false,
  snoozeUntil: 0,
  seen: new Set(),     // ids já notificados (som/notificação só uma vez)
  firstPoll: true,
  titleTimer: null, baseTitle: "",
};

// ------------------------------------------------------------------ aviso no meio da tela
function onAlerts(summary) {
  if (!summary) return;
  alarm.active = summary.active || [];
  alarm.notify = summary.notify;
  alarm.sound = summary.sound;
  const n = alarm.active.length;
  $("alarmBadge").textContent = n;
  $("alarmBadge").classList.toggle("hidden", !n);
  $("openAlarms").classList.toggle("has-alarm", n > 0);

  const fresh = alarm.active.filter((a) => !alarm.seen.has(a.id));
  fresh.forEach((a) => alarm.seen.add(a.id));
  // na abertura do painel, alertas antigos aparecem, mas sem som/notificação em rajada
  if (fresh.length && !alarm.firstPoll) {
    if (alarm.sound) playAlarmSound(fresh[0].severity);
    if (alarm.notify) systemNotify(fresh[0], fresh.length);
    alarm.snoozeUntil = 0;  // alerta novo fura a soneca
  }
  alarm.firstPoll = false;
  renderOverlay();
}

function renderOverlay() {
  const ov = $("alertOverlay");
  const a = alarm.active[0];
  const show = !!a && Date.now() >= alarm.snoozeUntil;
  ov.classList.toggle("hidden", !show);
  flashTitle(show);
  if (!show) return;
  const warning = a.severity === "warning";
  ov.classList.toggle("warning", warning);
  $("alertRule").textContent = `${warning ? "Aviso" : "Alarme crítico"} · ${a.rule_name}`;
  $("alertTitle").textContent = a.title;
  $("alertDetail").textContent = a.detail || "";
  const others = alarm.active.length - 1;
  $("alertMeta").textContent = `${fmtFull(a.ts)} (${relTime(a.ts)})` + (others > 0 ? ` · +${others} outro(s) alarme(s) não reconhecido(s)` : "");
  $("alertView").classList.toggle("hidden", !a.fingerprint && !a.service);
  $("alertView").textContent = a.fingerprint ? "🔍 Ver issue" : "🔍 Ver serviço";
  $("alertAckAll").classList.toggle("hidden", others <= 0);
  $("alertAckAll").textContent = `Reconhecer todos (${alarm.active.length})`;
  if (!ov.contains(document.activeElement)) $("alertAck").focus({ preventScroll: true });
}

function flashTitle(on) {
  if (on && !alarm.titleTimer) {
    alarm.titleTimer = setInterval(() => {
      document.title = document.title.startsWith("🚨") ? alarm.baseTitle : `🚨 ALARME — ${alarm.baseTitle}`;
    }, 900);
  } else if (!on && alarm.titleTimer) {
    clearInterval(alarm.titleTimer);
    alarm.titleTimer = null;
  }
  if (!document.title.startsWith("🚨")) alarm.baseTitle = document.title;
}

async function ackAlert(all = false) {
  const a = alarm.active[0];
  try {
    await api(all ? "/api/alerts/ack-all" : `/api/alerts/${a.id}/ack`, { method: "POST" });
  } catch (e) {
    toast(`Não consegui reconhecer: ${esc(e.message)}`, "critical");
  }
  alarm.active = all ? [] : alarm.active.slice(1);
  renderOverlay();
  pollStatus();
}

async function viewAlert() {
  const a = alarm.active[0];
  await ackAlert(false);
  if (a.fingerprint) loadIssue(a.fingerprint);
  else if (a.service) { state.service = a.service; loadAll(); }
}

function systemNotify(a, count) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const n = new Notification(`🚨 ${a.title}`, {
    body: (a.detail || "") + (count > 1 ? `\n(+${count - 1} outros)` : ""), tag: `alarm-${a.id}`, requireInteraction: true,
  });
  n.onclick = () => { window.focus(); n.close(); };
}

// Som gerado na hora (sem arquivo): três bipes; crítico mais agudo
function playAlarmSound(severity) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const freq = severity === "warning" ? 660 : 880;
    [0, 0.35, 0.7].forEach((t) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + t);
      gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.25);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + t);
      osc.stop(ctx.currentTime + t + 0.3);
    });
    setTimeout(() => ctx.close(), 1500);
  } catch { /* navegador sem áudio */ }
}

// ------------------------------------------------------------------ janela de configuração
const ALARM_FIELDS = {
  spike: [["threshold", "Erros (mínimo)", 20], ["window_min", "Em quantos minutos", 10]],
  anomaly: [["factor", "Vezes acima da média", 3], ["window_min", "Janela (min)", 15], ["min_count", "Mínimo de erros", 10]],
  match: [["text", "Texto no erro", ""], ["regex", "É expressão regular", false]],
  cw_alarm: [["alarm", "Só alarmes com nome contendo (opcional)", ""], ["include_autoscaling", "Incluir alarmes de auto scaling (TargetTracking — ficam em ALARM o tempo todo)", false]],
  silence: [["group", "Log group (nome exato)", ""], ["minutes", "Sem logs há (min)", 60]],
  stalled: [["minutes", "Sem coleta há (min)", 20]],
};
const ALARM_HELP = {
  new_issue: "Avisa quando aparece um erro que nunca tinha acontecido.",
  regression: "Avisa quando um erro que você marcou como resolvido volta a acontecer.",
  spike: "Avisa quando um serviço passa de N erros num intervalo (cada serviço é contado separado).",
  anomaly: "Avisa quando um serviço tem X vezes mais erros que a média dele nas últimas 24h — não precisa adivinhar um número.",
  match: "Avisa quando chega um erro contendo um texto (ex.: payment failed, ETIMEDOUT).",
  cw_alarm: "Avisa quando um alarme do CloudWatch da sua conta entra em ALARM.",
  silence: "Avisa quando um log group fica sem receber logs — serviço parado não gera erro, gera silêncio.",
  stalled: "Avisa quando o próprio monitor fica sem conseguir coletar (internet ou credenciais).",
};
const NO_FILTERS = new Set(["cw_alarm", "silence", "stalled"]);

function ruleSummary(r) {
  const p = r.params || {};
  const parts = {
    spike: `≥ ${p.threshold} erros em ${p.window_min} min, por serviço`,
    anomaly: `≥ ${p.factor}× a média das últimas 24h (mín. ${p.min_count} erros em ${p.window_min} min)`,
    match: `erro contendo “${p.text}”${p.regex ? " (regex)" : ""}`,
    cw_alarm: p.alarm ? `alarmes com “${p.alarm}” no nome` : "qualquer alarme da conta",
    silence: `${p.group} sem logs há ${p.minutes} min`,
    stalled: `nenhuma coleta ok há ${p.minutes} min`,
    new_issue: "qualquer issue nova", regression: "qualquer regressão",
  }[r.kind] || "";
  const filters = [p.service && `serviços com “${p.service}”`, p.category && `categoria ${(alarm.data?.categories || {})[p.category] || p.category}`].filter(Boolean);
  const repeat = r.cooldown_min ? `não repete por ${r.cooldown_min} min` : "avisa uma vez por ocorrência";
  return [parts, ...filters, repeat].join(" · ");
}

async function openAlarms() {
  $("alarmsModal").classList.add("show");
  await loadAlarms();
}

async function loadAlarms() {
  try {
    alarm.data = await api("/api/alerts");
  } catch (e) {
    toast(`Não consegui carregar os alarmes: ${esc(e.message)}`, "critical");
    return;
  }
  const d = alarm.data;
  $("alarmNotify").checked = d.notify;
  $("alarmSound").checked = d.sound;
  $("alarmRules").innerHTML = d.rules.map((r) => `
    <li class="${r.enabled ? "" : "off"}" data-id="${r.id}">
      <label class="switch" title="${r.enabled ? "Ligado" : "Desligado"}"><input type="checkbox" data-act="toggle" ${r.enabled ? "checked" : ""}
        aria-label="Ligar ${esc(r.name)}"><span></span></label>
      <div><div class="alarm-name">${r.severity === "critical" ? "🔴" : "🟠"} ${esc(r.name)}</div>
        <div class="alarm-desc">${esc(r.kind_label)} — ${esc(ruleSummary(r))}${r.last_fired ? ` · último disparo ${esc(relTime(r.last_fired))}` : ""}</div></div>
      <div class="alarm-btns">
        <button class="btn" data-act="test">▶ Testar</button>
        <button class="btn" data-act="edit">Editar</button>
        <button class="btn" data-act="delete" aria-label="Excluir ${esc(r.name)}">🗑</button>
      </div>
    </li>`).join("") || `<li class="muted">Nenhuma regra. Crie uma em “Novo alarme”.</li>`;
  $("alarmHistory").innerHTML = d.history.map((h) => `
    <tr><td class="when">${fmtFull(h.ts)}</td><td>${esc(h.rule_name)}</td>
      <td>${esc(h.title)}<div class="muted small">${esc((h.detail || "").slice(0, 160))}</div></td>
      <td class="when">${h.acknowledged_at ? fmtFull(h.acknowledged_at) : "<strong>pendente</strong>"}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">Nenhum disparo ainda.</td></tr>`;
  const kinds = $("alarmKind");
  if (!kinds.options.length) {
    kinds.innerHTML = Object.entries(d.kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("alarmForm").elements.category.innerHTML = `<option value="">Todas</option>` +
      Object.entries(d.categories).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  }
  $("alarmGroups").innerHTML = d.log_groups.map((g) => `<option value="${esc(g)}"></option>`).join("");
}

function renderParamFields(kind, values = {}) {
  $("alarmKindHelp").textContent = ALARM_HELP[kind] || "";
  $("alarmFilters").querySelectorAll("label")[0].classList.toggle("hidden", NO_FILTERS.has(kind));
  $("alarmFilters").querySelectorAll("label")[1].classList.toggle("hidden", NO_FILTERS.has(kind));
  $("alarmParams").innerHTML = (ALARM_FIELDS[kind] || []).map(([key, label, def]) => {
    const v = values[key] ?? def;
    if (typeof def === "boolean") return `<label class="check"><input type="checkbox" name="p_${key}" ${v ? "checked" : ""}> ${esc(label)}</label>`;
    const type = typeof def === "number" ? "number" : "text";
    const list = key === "group" ? 'list="alarmGroups"' : "";
    return `<label>${esc(label)}<input type="${type}" name="p_${key}" value="${esc(v)}" ${list} ${type === "number" ? 'step="any" min="0"' : ""}></label>`;
  }).join("");
}

function editRule(rule) {
  const f = $("alarmForm");
  f.classList.remove("hidden");
  $("alarmFormTitle").textContent = rule ? `Editar “${rule.name}”` : "Novo alarme";
  $("alarmFormMsg").textContent = "";
  f.elements.id.value = rule?.id || "";
  f.elements.kind.value = rule?.kind || "spike";
  f.elements.name.value = rule?.name || "";
  f.elements.severity.value = rule?.severity || "critical";
  f.elements.cooldown_min.value = rule?.cooldown_min ?? 30;
  f.elements.service.value = rule?.params?.service || "";
  f.elements.category.value = rule?.params?.category || "";
  renderParamFields(f.elements.kind.value, rule?.params || {});
  f.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function saveRule(e) {
  e.preventDefault();
  const f = $("alarmForm");
  const params = { service: f.elements.service.value, category: f.elements.category.value };
  for (const el of $("alarmParams").querySelectorAll("input")) {
    params[el.name.slice(2)] = el.type === "checkbox" ? el.checked : el.value;
  }
  const id = f.elements.id.value;
  const current = alarm.data.rules.find((r) => String(r.id) === id);
  try {
    await api("/api/alerts/rules", { method: "POST", body: {
      id: id || undefined, kind: f.elements.kind.value, name: f.elements.name.value, severity: f.elements.severity.value,
      cooldown_min: f.elements.cooldown_min.value, params, enabled: current ? !!current.enabled : true,
    } });
    f.classList.add("hidden");
    toast(id ? "Alarme atualizado." : "Alarme criado e ligado.", "good");
    loadAlarms();
  } catch (err) {
    $("alarmFormMsg").textContent = err.message;
  }
}

function bindAlarms() {
  $("openAlarms").addEventListener("click", openAlarms);
  $("alertAck").addEventListener("click", () => ackAlert(false));
  $("alertAckAll").addEventListener("click", () => ackAlert(true));
  $("alertView").addEventListener("click", viewAlert);
  $("alertSnooze").addEventListener("click", () => {
    alarm.snoozeUntil = Date.now() + 10 * 60_000;
    renderOverlay();
    toast("Aviso escondido por 10 minutos. O alarme continua pendente no botão 🔔.");
  });
  $("alarmNew").addEventListener("click", () => editRule(null));
  $("alarmCancel").addEventListener("click", () => $("alarmForm").classList.add("hidden"));
  $("alarmKind").addEventListener("change", (e) => renderParamFields(e.target.value));
  $("alarmForm").addEventListener("submit", saveRule);
  $("alarmTestGeneric").addEventListener("click", async () => {
    await api("/api/alerts/test", { method: "POST", body: {} });
    $("alarmsModal").classList.remove("show");
    pollStatus();
  });
  for (const id of ["alarmNotify", "alarmSound"]) {
    $(id).addEventListener("change", async (e) => {
      if (id === "alarmNotify" && e.target.checked && "Notification" in window && Notification.permission !== "granted") {
        if ((await Notification.requestPermission()) !== "granted") {
          e.target.checked = false;
          toast("O navegador não permitiu notificações. Libere nas configurações do site.", "critical");
        }
      }
      if (id === "alarmSound" && e.target.checked) playAlarmSound("critical");  // amostra do som
      await api("/api/alerts/settings", { method: "POST", body: { alert_notify: $("alarmNotify").checked, alert_sound: $("alarmSound").checked } });
    });
  }
  $("alarmRules").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]");
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!li || !act) return;
    const id = li.dataset.id;
    const rule = alarm.data.rules.find((r) => String(r.id) === id);
    if (act === "toggle") {
      try {
        await api(`/api/alerts/rules/${id}/toggle`, { method: "POST", body: { enabled: e.target.checked } });
      } catch (err) {
        // regra incompleta (ex.: silêncio sem log group): desfaz a chave e abre o formulário para completar
        e.target.checked = false;
        toast(`${esc(err.message)} — complete o alarme e salve para ligá-lo.`, "warning");
        editRule({ ...rule, enabled: 1 });
        return;
      }
      toast(e.target.checked ? `“${esc(rule.name)}” ligado.` : `“${esc(rule.name)}” desligado.`, "good");
      loadAlarms();
    } else if (act === "test") {
      await api("/api/alerts/test", { method: "POST", body: { rule_id: Number(id) } });
      $("alarmsModal").classList.remove("show");
      pollStatus();
    } else if (act === "edit") {
      editRule(rule);
    } else if (act === "delete" && confirm(`Excluir o alarme “${rule.name}”?`)) {
      await api(`/api/alerts/rules/${id}/delete`, { method: "POST" });
      loadAlarms();
    }
  });
  // Esc reconhece o aviso aberto (e não fecha modais por baixo dele)
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("alertOverlay").classList.contains("hidden")) {
      e.stopImmediatePropagation();
      ackAlert(false);
    }
  }, true);
}

bindAlarms();
