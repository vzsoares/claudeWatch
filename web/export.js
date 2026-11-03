// "Baixar logs": todas as ocorrências guardadas num período, em .txt (padrão: últimos 7 dias).

const toLocalInput = (ms) => {
  const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60_000);
  return d.toISOString().slice(0, 16);
};

function exportParams() {
  const f = $("exportForm").elements;
  const from = new Date(f.from.value).getTime();
  const to = new Date(f.to.value).getTime();
  const p = new URLSearchParams({ from, to, warnings: f.warnings.checked ? "1" : "0", ignored: f.ignored.checked ? "1" : "0" });
  if (f.filters.checked && !$("exportFiltersWrap").classList.contains("hidden")) {
    if (state.service) p.set("service", state.service);
    if (state.category) p.set("category", state.category);
  }
  return { p, from, to };
}

function setExportDays(days) {
  const f = $("exportForm").elements;
  const now = Date.now();
  f.to.value = toLocalInput(now);
  f.from.value = toLocalInput(now - days * 86_400_000);
  updateExportCount();
}

let exportSeq = 0;
async function updateExportCount() {
  const { p, from, to } = exportParams();
  const seq = ++exportSeq;
  $("exportMsg").textContent = "";
  if (!(from < to)) {
    $("exportCount").textContent = "Escolha um início antes do fim.";
    $("exportGo").disabled = true;
    return;
  }
  $("exportCount").textContent = "Contando…";
  try {
    const r = await api(`/api/logs/count?${p}`);
    if (seq !== exportSeq) return;
    $("exportGo").disabled = r.events === 0;
    $("exportCount").textContent = r.events === 0
      ? "Nenhuma ocorrência guardada nesse período."
      : `${nf.format(r.events)} ocorrências em ${nf.format(r.issues)} issues` +
        (r.events > r.max ? ` — o arquivo traz as ${nf.format(r.max)} mais recentes` : "");
  } catch (e) {
    if (seq === exportSeq) $("exportCount").textContent = `Não consegui contar: ${e.message}`;
  }
}

function openExport() {
  const parts = [state.service && `serviço ${state.service}`, state.category && `categoria ${CATEGORY_LABELS[state.category] || state.category}`].filter(Boolean);
  $("exportFiltersWrap").classList.toggle("hidden", !parts.length);
  $("exportFiltersLabel").textContent = `Só os filtros da tela (${parts.join(", ")})`;
  $("exportModal").classList.add("show");
  setExportDays(7);
}

(function initExport() {
  $("openExport").addEventListener("click", openExport);
  const form = $("exportForm");
  form.addEventListener("change", updateExportCount);
  form.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => setExportDays(Number(b.dataset.days))));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const { p, from, to } = exportParams();
    if (!(from < to)) return void ($("exportMsg").textContent = "A data de início precisa ser antes da data de fim.");
    const a = document.createElement("a");
    a.href = `/api/logs.txt?${p}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast("Gerando o arquivo… o download começa em instantes.", "good");
    $("exportModal").classList.remove("show");
  });
})();
