"use strict";
// "Entendendo o alarme": o que é, por que disparou, se é grave e o que fazer.
// Base de conhecimento local (nada é consultado fora da máquina). Usa helpers de app.js.

const LEVELS = {
  ok: { label: "Normal — não precisa fazer nada", cls: "good", icon: "✓" },
  info: { label: "Informativo — acompanhe", cls: "neutral", icon: "i" },
  warning: { label: "Atenção — investigue quando puder", cls: "warning", icon: "!" },
  critical: { label: "Crítico — investigue agora", cls: "critical", icon: "!" },
};

// Cada entrada: test(alarme) -> bool; level; title; what; todo[] (passos)
const ALARM_KB = [
  {
    test: (a) => a.name.startsWith("TargetTracking-") && /AlarmLow/i.test(a.name),
    level: () => "ok", title: "Auto scaling: uso baixo (reduzir capacidade)",
    what: "Este alarme foi criado automaticamente pelo auto scaling (Target Tracking). Ele entra em ALARM quando o uso fica abaixo da meta — é o sinal que o auto scaling usa para desligar máquinas/tarefas sobrando e economizar. Ficar em ALARM boa parte do tempo é normal.",
    todo: ["Nada. É o funcionamento esperado do auto scaling.", "Só investigue se o serviço estiver lento ou com falta de capacidade — aí a meta (target) pode estar baixa demais."],
  },
  {
    test: (a) => a.name.startsWith("TargetTracking-") && /AlarmHigh/i.test(a.name),
    level: () => "info", title: "Auto scaling: uso alto (aumentar capacidade)",
    what: "Alarme criado automaticamente pelo auto scaling. Em ALARM, ele está pedindo mais capacidade porque o uso passou da meta. Normalmente o próprio auto scaling resolve sozinho em alguns minutos.",
    todo: ["Acompanhe: deve voltar para OK quando as novas máquinas/tarefas subirem.", "Se ficar muito tempo em ALARM, o serviço pode ter batido o limite máximo de capacidade — confira o máximo do Auto Scaling Group / capacity provider.", "Veja se há erros de timeout ou lentidão no painel no mesmo horário."],
  },
  {
    test: (a) => a.metric === "HTTPCode_Target_5XX_Count",
    level: () => "critical", title: "Erros 5xx vindos do seu serviço",
    what: "O load balancer recebeu respostas de erro (500, 502, 503, 504) da sua aplicação. Usuários estão vendo falhas.",
    todo: ["Veja os erros do serviço no painel no mesmo horário (botão abaixo).", "Houve deploy recente? Se sim, considere voltar a versão anterior.", "Confira dependências: banco de dados, APIs externas, filas.", "Use “Gerar Prompt” numa issue relacionada para o diagnóstico."],
  },
  {
    test: (a) => a.metric === "HTTPCode_ELB_5XX_Count",
    level: () => "critical", title: "Erros 5xx gerados pelo próprio load balancer",
    what: "O load balancer não conseguiu falar com a aplicação (geralmente 502/503): nenhum container saudável, timeout ou container caindo.",
    todo: ["Confira se há tarefas/containers saudáveis no serviço (ECS → serviço → aba Tasks/Events).", "Veja se os containers estão reiniciando (falta de memória, erro ao iniciar).", "Verifique o health check do target group."],
  },
  {
    test: (a) => /HealthyHostCount/.test(a.metric),
    level: () => "critical", title: "Nenhum (ou poucos) servidores saudáveis",
    what: "O load balancer está com menos destinos saudáveis do que o mínimo. Se chegar a zero, o site/API fica fora do ar.",
    todo: ["Abra o serviço no ECS e veja a aba Events: tarefas caindo, falhando no health check ou sem conseguir iniciar.", "Procure erros de inicialização no painel (falta de variável, conexão com banco, memória).", "Confira se o caminho do health check (ex.: /health) responde 200."],
  },
  {
    test: (a) => /UnHealthyHostCount/.test(a.metric),
    level: () => "warning", title: "Servidores falhando no health check",
    what: "Algum destino do load balancer está respondendo mal ao health check e saiu do rodízio.",
    todo: ["Veja os erros do serviço no painel.", "Confira memória/CPU dos containers e se estão reiniciando."],
  },
  {
    test: (a) => /TargetResponseTime|Latency|Duration/.test(a.metric),
    level: () => "warning", title: "Lentidão (tempo de resposta alto)",
    what: "As requisições estão demorando mais do que o limite. Usuários sentem o sistema lento e pode virar timeout.",
    todo: ["Veja se há timeouts no painel (categoria Timeout).", "Confira o banco de dados (CPU, conexões, consultas lentas) e APIs externas.", "Verifique se o serviço está no limite de capacidade (CPU/memória)."],
  },
  {
    test: (a) => a.namespace === "AWS/SQS" && /ApproximateNumberOfMessagesVisible/.test(a.metric) && /dlq|dead|erro/i.test(a.name),
    level: () => "critical", title: "Mensagens na fila de erros (DLQ)",
    what: "Mensagens que falharam várias vezes foram para a fila de “mortos” (dead-letter queue). Cada uma é algo que não foi processado (ex.: um documento não enviado).",
    todo: ["Veja os erros do consumidor dessa fila no painel (a Lambda/serviço que lê a fila).", "No console do SQS, inspecione algumas mensagens da DLQ para entender o que falhou.", "Depois de corrigir, reprocesse as mensagens (“Start DLQ redrive”)."],
  },
  {
    test: (a) => a.namespace === "AWS/SQS",
    level: () => "warning", title: "Fila acumulando mensagens",
    what: "A fila está com mais mensagens (ou mensagens mais antigas) do que o esperado: o consumidor não está dando conta ou parou.",
    todo: ["Veja se o consumidor (Lambda/serviço) está com erros ou throttling no painel.", "Confira se o consumidor está ligado e com capacidade suficiente."],
  },
  {
    test: (a) => a.namespace === "AWS/Lambda" && /Errors/.test(a.metric),
    level: () => "critical", title: "Erros na Lambda",
    what: "A função terminou com erro mais vezes do que o limite.",
    todo: ["Veja os erros dessa Lambda no painel e use “Gerar Prompt” na issue.", "Houve deploy recente da função?"],
  },
  {
    test: (a) => a.namespace === "AWS/Lambda" && /Throttles/.test(a.metric),
    level: () => "warning", title: "Lambda sendo recusada por limite (throttling)",
    what: "A AWS recusou execuções porque a função (ou a conta) bateu o limite de concorrência. Essas execuções não rodaram.",
    todo: ["Confira a concorrência reservada da função e o limite da conta (Service Quotas).", "Veja se houve um pico de chamadas (fila, evento repetido)."],
  },
  {
    test: (a) => a.namespace === "AWS/RDS" && /CPUUtilization/.test(a.metric),
    level: () => "warning", title: "Banco de dados com CPU alta",
    what: "O banco está muito ocupado. Consultas ficam lentas e podem dar timeout.",
    todo: ["Veja no RDS → Performance Insights quais consultas estão pesando.", "Procure timeouts de banco no painel.", "Considere índices ou aumentar a instância."],
  },
  {
    test: (a) => a.namespace === "AWS/RDS" && /FreeStorageSpace/.test(a.metric),
    level: () => "critical", title: "Banco de dados ficando sem disco",
    what: "Se o espaço acabar, o banco para de aceitar gravações.",
    todo: ["Aumente o armazenamento ou ative o Storage Autoscaling no RDS.", "Veja o que está crescendo (tabelas de log, binlogs)."],
  },
  {
    test: (a) => a.namespace === "AWS/RDS" && /DatabaseConnections/.test(a.metric),
    level: () => "warning", title: "Muitas conexões no banco",
    what: "O número de conexões está alto. Perto do máximo, novas conexões falham.",
    todo: ["Procure erros de conexão no painel (ETIMEDOUT, too many connections).", "Verifique pools de conexão das aplicações e Lambdas abrindo conexão a cada execução."],
  },
  {
    test: (a) => a.namespace === "AWS/EC2" && /StatusCheckFailed/.test(a.metric),
    level: () => "critical", title: "Servidor (EC2) com falha de status",
    what: "A AWS detectou problema na máquina (hardware/rede) ou no sistema operacional.",
    todo: ["Abra a instância no EC2 → aba Status checks.", "Reiniciar (reboot) costuma resolver falhas do sistema; falhas da AWS podem exigir stop/start."],
  },
  {
    test: (a) => /CPUUtilization|MemoryUtilization/.test(a.metric),
    level: () => "warning", title: "Uso alto de CPU ou memória",
    what: "O recurso está perto do limite de capacidade. Pode ficar lento ou reiniciar por falta de memória.",
    todo: ["Veja se há erros de memória/timeout no painel.", "Considere aumentar a capacidade ou ajustar o auto scaling."],
  },
  {
    test: (a) => !a.namespace.startsWith("AWS/"),
    level: () => "warning", title: "Métrica personalizada da sua aplicação",
    what: "Este alarme vigia uma métrica criada pelo seu time (muitas vezes um “metric filter” que conta um tipo de erro nos logs).",
    todo: ["Busque o nome da métrica ou do alarme nos logs (🔎 Buscar nos logs) para achar a origem.", "Veja quem criou o alarme e a regra da métrica no console do CloudWatch."],
  },
];

const FALLBACK = {
  level: () => "warning", title: "Alarme do CloudWatch",
  what: "Um alarme configurado na sua conta passou do limite definido para a métrica que ele vigia.",
  todo: ["Leia o motivo abaixo (valor medido × limite).", "Abra o alarme no console da AWS para ver o gráfico da métrica.", "Veja os erros no painel no mesmo horário."],
};

const RULE_EXPLAIN = {
  new_issue: { level: "warning", title: "Um erro novo apareceu", what: "Um tipo de erro que nunca tinha acontecido começou a acontecer. Muitas vezes é efeito de um deploy ou de uma mudança externa.", todo: ["Abra a issue e veja o log completo.", "Houve deploy recente desse serviço?", "Use “✨ Gerar Prompt” para pedir o diagnóstico ao Claude.", "Se for ruído conhecido, marque a issue como Ignorada."] },
  regression: { level: "critical", title: "Um erro resolvido voltou", what: "Você tinha marcado esta issue como resolvida, mas ela voltou a acontecer — a correção não funcionou ou foi desfeita.", todo: ["Abra a issue e compare com a última ocorrência antes de resolver.", "Confira se a correção foi mesmo para produção.", "Use “✨ Gerar Prompt” com o contexto completo."] },
  spike: { level: "critical", title: "Pico de erros num serviço", what: "Um serviço passou do limite de erros que você definiu num curto intervalo. Costuma indicar um incidente em andamento.", todo: ["Veja os erros do serviço (botão abaixo) e qual issue está crescendo.", "Houve deploy, mudança de configuração ou falha de dependência?", "Avise o time se for impacto em produção."] },
  anomaly: { level: "warning", title: "Erros bem acima do normal", what: "O serviço está com muito mais erros do que a média dele nas últimas 24 horas.", todo: ["Veja os erros do serviço e a issue que mais cresceu.", "Compare com o mesmo horário de ontem no gráfico (período 7 dias)."] },
  match: { level: "critical", title: "Apareceu um erro que você pediu para vigiar", what: "Chegou um erro com o texto configurado no alarme.", todo: ["Abra a issue e veja o log completo.", "Use “✨ Gerar Prompt” para o diagnóstico."] },
  silence: { level: "warning", title: "Um serviço parou de gerar logs", what: "O log group ficou sem receber logs pelo tempo configurado. Serviço parado não gera erro — gera silêncio.", todo: ["Confira se o serviço/Lambda está rodando (ECS → Tasks, Lambda → Monitoramento).", "Veja se a origem dos eventos (fila, agendamento, API) parou.", "Se for esperado (ex.: fora do horário comercial), ajuste o tempo do alarme."] },
  stalled: { level: "critical", title: "O monitor não está conseguindo coletar", what: "Faz tempo que a coleta dos logs não funciona. Enquanto isso, erros novos não aparecem no painel.", todo: ["Veja a mensagem de erro em ⚙ Configurações → Últimas coletas.", "Confira a internet e as credenciais da AWS (rode “python configurar.py”).", "Se a sessão SSO expirou: aws sso login."] },
};

function parseReason(reason) {
  if (!reason) return null;
  const value = reason.match(/\[\s*([\d.]+)/);
  const cmp = reason.match(/(greater than or equal to|greater than|less than or equal to|less than) the threshold \(([\d.]+)\)/);
  if (!value || !cmp) return null;
  const words = { "greater than or equal to": "maior ou igual a", "greater than": "maior que", "less than or equal to": "menor ou igual a", "less than": "menor que" };
  const n = (x) => nf.format(Number(x));
  return `Valor medido: <strong>${esc(n(value[1]))}</strong> · o alarme dispara quando é <strong>${esc(words[cmp[1]])} ${esc(n(cmp[2]))}</strong>`;
}

function guessService(name) {
  const services = state.knownServices || [];
  const tokens = name.toLowerCase().replace(/^(prod|dev|hml|homolog|stg|staging)-/, "").split(/[-_]/).filter((t) => t.length > 2);
  let best = null, bestScore = 0;
  for (const s of services) {
    const sl = s.toLowerCase();
    const score = tokens.reduce((acc, t) => acc + (sl.includes(t) ? t.length : 0), 0);
    if (score > bestScore) { best = s; bestScore = score; }
  }
  return bestScore >= 6 ? best : null;
}

function alarmConsoleUrl(name) {
  const region = state.status_?.region || "us-east-1";
  return `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#alarmsV2:alarm/${encodeURIComponent(name)}`;
}

function renderExplain({ title, badge, level, what, why, todo, actions }) {
  const L = LEVELS[level] || LEVELS.warning;
  $("explainTitle").textContent = title;
  $("explainBadge").innerHTML = badge || "";
  $("explainLevel").className = `explain-level ${L.cls}`;
  $("explainLevel").innerHTML = `<span class="ic" aria-hidden="true">${L.icon}</span>${esc(L.label)}`;
  $("explainWhat").textContent = what;
  $("explainWhy").innerHTML = why || "";
  $("explainWhyBox").classList.toggle("hidden", !why);
  $("explainTodo").innerHTML = todo.map((t) => `<li>${esc(t)}</li>`).join("");
  $("explainActions").innerHTML = "";
  for (const act of actions) {
    const el = document.createElement(act.href ? "a" : "button");
    el.className = `btn ${act.primary ? "primary" : ""}`;
    el.textContent = act.label;
    if (act.href) { el.href = act.href; el.target = "_blank"; el.rel = "noopener"; }
    else el.addEventListener("click", act.onClick);
    $("explainActions").appendChild(el);
  }
  $("explainModal").classList.add("show");
}

function closeExplain() { $("explainModal").classList.remove("show"); }

function explainCloudWatchAlarm(a) {
  const kb = ALARM_KB.find((k) => k.test(a)) || FALLBACK;
  const level = kb.level(a);
  const svc = guessService(a.name);
  const actions = [];
  if (svc) actions.push({ label: `🔍 Ver erros de ${svc}`, primary: true, onClick: () => { closeExplain(); $("alertOverlay").classList.add("hidden"); state.service = svc; loadAll(); } });
  actions.push({ label: "Abrir no console AWS ↗", href: alarmConsoleUrl(a.name) });
  actions.push({ label: "✨ Gerar prompt para o Claude", onClick: () => alarmPrompt(a, kb) });
  const stateTxt = { ALARM: "em ALARM", OK: "OK", INSUFFICIENT_DATA: "sem dados" }[a.state] || a.state;
  const why = [parseReason(a.reason), a.reason ? `<div class="muted small">Motivo informado pela AWS: ${esc(a.reason)}</div>` : "",
    `<div class="muted small">Métrica: ${esc(a.namespace)} · ${esc(a.metric)} · estado ${esc(stateTxt)} desde ${esc(fmtFull(a.state_updated_at))}</div>`].filter(Boolean).join("");
  renderExplain({
    title: kb.title, badge: `<span class="pill">${esc(a.name)}</span>`, level: a.state === "OK" ? "ok" : level,
    what: kb.what, why, todo: a.state === "OK" ? ["Está tudo normal agora. Nada a fazer."] : kb.todo, actions,
  });
}

function explainMonitorAlert(alert) {
  if (alert.rule_kind === "cw_alarm") {
    const name = (alert.dedupe_key || "").replace(/^cw:/, "").replace(/:\d+$/, "");
    const a = (state.alarmItems || []).find((x) => x.name === name);
    if (a) return explainCloudWatchAlarm(a);
  }
  const r = RULE_EXPLAIN[alert.rule_kind] || { level: "warning", title: "Alarme do monitor", what: "Uma regra de alarme configurada por você disparou.", todo: ["Veja os detalhes do aviso e os erros no painel."] };
  const actions = [];
  if (alert.fingerprint) actions.push({ label: "🔍 Abrir a issue", primary: true, onClick: () => { closeExplain(); viewAlert(); } });
  else if (alert.service) actions.push({ label: `🔍 Ver erros de ${alert.service}`, primary: true, onClick: () => { closeExplain(); viewAlert(); } });
  actions.push({ label: "🔔 Ajustar este alarme", onClick: () => { closeExplain(); openAlarms(); } });
  renderExplain({
    title: r.title, badge: `<span class="pill">${esc(alert.rule_name)}</span>`, level: r.level, what: r.what,
    why: `<strong>${esc(alert.title)}</strong>${alert.detail ? `<div class="muted small">${esc(alert.detail)}</div>` : ""}`,
    todo: r.todo, actions,
  });
}

function alarmPrompt(a, kb) {
  const text = [
    "Você é um engenheiro sênior de SRE/AWS. Um alarme do CloudWatch disparou e preciso entender o impacto e o que fazer.",
    "", "## Alarme",
    `- Nome: ${a.name}`, `- Estado: ${a.state} desde ${fmtFull(a.state_updated_at)}`,
    `- Métrica: ${a.namespace} / ${a.metric}`, `- Motivo informado pela AWS: ${a.reason || "-"}`,
    "", "## Minha leitura inicial", `${kb.title}: ${kb.what}`,
    "", "## O que preciso de você",
    "1. O que este alarme indica e se é um problema real (ou ruído/configuração).",
    "2. A causa mais provável e como confirmar (quais métricas, logs ou telas olhar na AWS).",
    "3. O que fazer agora para mitigar e o que ajustar para evitar falso positivo.",
  ].join("\n");
  closeExplain();
  state.promptSource = { kind: "text", text };
  generatePrompt();
}

function bindExplain() {
  $("alarmsList").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-name]");
    if (!li) return;
    const a = (state.alarmItems || []).find((x) => x.name === li.dataset.name);
    if (a) explainCloudWatchAlarm(a);
  });
  $("alarmsList").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("li[data-name]")) { e.preventDefault(); e.target.click(); }
  });
  $("alertExplain").addEventListener("click", () => alarm.active[0] && explainMonitorAlert(alarm.active[0]));
}

bindExplain();
