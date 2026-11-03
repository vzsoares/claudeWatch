// Lista de bloqueio: falsos alarmes que não aparecem nem contam como erro por 3 a 60 dias, ou para sempre.

const periodText = (days) => (days ? `por ${days} dias` : "para sempre");
const pickedDays = (form) => form.querySelector('input[name="days"]:checked')?.value || "";

// "até 06/11 (faltam 3 dias)" · "para sempre" · "expirou em 06/11"
function untilText(b) {
  if (!b.expires_at) return "🔒 para sempre";
  if (!b.active) return `⌛ expirou em ${fmtFull(b.expires_at)} — voltou a ser registrado`;
  const left = Math.ceil((b.expires_at - Date.now()) / 86_400_000);
  return `⏳ até ${fmtFull(b.expires_at)} (falta${left > 1 ? "m" : ""} ${left} dia${left > 1 ? "s" : ""})`;
}

function openBlock() {
  if (!state.issue) return;
  const f = $("blockForm");
  f.reset();
  $("blockGo").textContent = "🚫 Ignorar por 3 dias";
  $("blockMsg").textContent = "";
  $("blockIssue").innerHTML = `${esc($("dTitle").textContent)}<div class="muted small">${$("dMeta").textContent}</div>`;
  $("blockModal").classList.add("show");
  f.elements.note.focus();
}

async function submitBlock(e) {
  e.preventDefault();
  const note = $("blockForm").elements.note.value.trim();
  const days = pickedDays($("blockForm"));
  if (note.length < 10) {
    $("blockMsg").textContent = "Escreva a justificativa (mínimo 10 caracteres).";
    return;
  }
  try {
    const r = await api("/api/blacklist", { method: "POST", body: { fingerprint: state.issue, note, days: days || null } });
    $("blockModal").classList.remove("show");
    closeDrawer();
    toast(`Erro ignorado ${periodText(days)}: ${nf.format(r.removed)} ocorrências apagadas. Veja na lista 🚫.`, "good");
    loadAll();
  } catch (err) {
    $("blockMsg").textContent = err.message;
  }
}

async function openBlacklist() {
  $("blacklistModal").classList.add("show");
  $("blacklistMsg").textContent = "";
  await loadBlacklist();
}

async function loadBlacklist() {
  try {
    const { items } = await api("/api/blacklist");
    $("blacklistItems").innerHTML = items.map((b) => `
      <li data-id="${b.id}" class="${b.active ? "" : "expired"}">
        <div>
          <div class="alarm-name">${b.kind === "issue" ? "Issue" : "Texto"}: ${esc(b.label)}</div>
          <div class="alarm-desc">${b.kind === "text" ? `mensagem contém “${esc(b.value)}”${b.service ? ` · só serviços com “${esc(b.service)}”` : ""} · ` : ""}
            bloqueado ${esc(relTime(b.created_at))} (${fmtFull(b.created_at)}) ·
            ${nf.format(b.hits)} descartadas desde então${b.last_hit_ts ? `, a última ${esc(relTime(b.last_hit_ts))}` : ""}</div>
          <div class="bl-until">${esc(untilText(b))}</div>
          <div class="bl-note">📝 ${esc(b.note)}</div>
          ${b.sample ? `<details><summary class="muted small">ver exemplo da mensagem</summary><pre class="log bl-sample">${esc(b.sample)}</pre></details>` : ""}
        </div>
        <div class="alarm-btns"><button class="btn" data-act="remove">Remover da lista</button></div>
      </li>`).join("") || `<li class="muted">Nenhum erro bloqueado. Use “🚫 Ignorar por…” no detalhe de uma issue.</li>`;
  } catch (e) {
    toast(`Não consegui carregar a lista de bloqueio: ${esc(e.message)}`, "critical");
  }
}

async function submitBlacklistText(e) {
  e.preventDefault();
  const f = e.target.elements;
  const days = pickedDays(e.target);
  const body = { text: f.text.value.trim(), service: f.service.value.trim(), note: f.note.value.trim(), days: days || null };
  if (body.text.length < 3) return void ($("blacklistMsg").textContent = "Informe o texto (mínimo 3 caracteres).");
  if (body.note.length < 10) return void ($("blacklistMsg").textContent = "Escreva a justificativa (mínimo 10 caracteres).");
  try {
    const r = await api("/api/blacklist", { method: "POST", body });
    e.target.reset();
    $("blacklistMsg").textContent = "";
    toast(`Bloqueado ${periodText(days)}: ${nf.format(r.removed)} ocorrências apagadas.`, "good");
    await loadBlacklist();
    loadAll();
  } catch (err) {
    $("blacklistMsg").textContent = err.message;
  }
}

(function initBlacklist() {
  $("btnBlock").addEventListener("click", openBlock);
  $("blockForm").addEventListener("submit", submitBlock);
  // o botão diz o que vai acontecer: "Ignorar por 10 dias" / "Ignorar para sempre"
  $("blockForm").addEventListener("change", () => {
    const d = pickedDays($("blockForm"));
    $("blockGo").textContent = d ? `🚫 Ignorar por ${d} dias` : "🚫 Ignorar para sempre";
  });
  $("openBlacklist").addEventListener("click", openBlacklist);
  $("blacklistForm").addEventListener("submit", submitBlacklistText);
  $("blacklistItems").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li || e.target.closest("[data-act]")?.dataset.act !== "remove") return;
    if (!confirm("Remover da lista de bloqueio? As próximas ocorrências voltam a ser registradas e contadas.")) return;
    try {
      await api(`/api/blacklist/${li.dataset.id}/delete`, { method: "POST" });
      toast("Removido da lista de bloqueio.", "good");
      loadBlacklist();
    } catch (err) {
      toast(`Erro: ${esc(err.message)}`, "critical");
    }
  });
})();
