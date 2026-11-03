<div align="center">

# 📈 CloudWatch Monitor

**Painel local e leve de erros do AWS CloudWatch — uma alternativa gratuita ao Datadog para acompanhar erros.**

Roda na sua máquina · lê seus logs do CloudWatch a cada poucos minutos · agrupa erros em issues ·
gráficos, alertas, busca em todos os logs, relatório em PNG e um botão **✨ Gerar Prompt** que entrega
o contexto completo do erro para o Claude (ou outra IA) diagnosticar.

[![Licença: MIT](https://img.shields.io/badge/Licen%C3%A7a-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Plataformas](https://img.shields.io/badge/plataforma-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![AWS somente leitura](https://img.shields.io/badge/AWS-somente%20leitura-orange)
![Sem servidor](https://img.shields.io/badge/servidor-n%C3%A3o%20precisa-brightgreen)
![Sem telemetria](https://img.shields.io/badge/telemetria-nenhuma-brightgreen)

[🇺🇸 English](README.md) · 🇧🇷 Português

<img src="docs/screenshots/dashboard-dark.png" alt="Painel" width="900">

</div>

---

## Por quê

Pagar uma plataforma completa de observabilidade só para *ver os erros* das suas Lambdas e containers
é exagero para muitos times. O CloudWatch já tem os logs — só não deixa fácil acompanhá-los.

O CloudWatch Monitor transforma os logs que você já tem num painel de erros para deixar aberto numa
segunda tela. **Nada é instalado na AWS**, não há servidor para hospedar e só precisa de credenciais
**somente leitura**.

> 🔒 **Seus dados nunca saem da sua máquina.** Nenhum servidor externo, nenhuma telemetria, nenhum
> analytics, nenhum cadastro. A única conexão de rede que o monitor faz é com a **API da AWS, na sua
> própria conta**, para ler seus logs. Veja [Privacidade](#-privacidade--seus-dados-ficam-com-você).

## Funcionalidades

| | |
|---|---|
| 🔄 **Coleta automática** | A cada N minutos (1–60, ajustável no painel) via CloudWatch Logs Insights em **todos** os log groups — Lambda, ECS, API Gateway, RDS, Glue… |
| 🧩 **Agrupamento inteligente** | Erros parecidos viram uma **issue** (ids, números e horários são ignorados). Entende tracebacks Python quebrados em várias linhas, logs JSON (pino, structlog…), HTTP 5xx e timeouts |
| 📊 **Gráficos interativos** | Erros no tempo por serviço, serviços com mais erros, categorias. Clique na barra ou na legenda para filtrar; período de 1h a 30 dias |
| 🚨 **Alarmes do CloudWatch** | Mostra o estado (OK / ALARME / sem dados) dos alarmes que já existem na conta |
| ✨ **Gerar Prompt** | Monta um prompt de diagnóstico pronto para colar: erro completo, a **invocação inteira da Lambda** (ou as linhas ao redor no container), estatísticas e erros relacionados. Dados sensíveis mascarados |
| 🔎 **Busca** | Instantânea e sem custo no texto completo dos erros coletados — ou, marcando uma caixa, em **todas** as linhas de log na AWS para achar de onde veio um id, pedido, e-mail ou código de erro |
| 🖼 **Relatório PNG** | Um clique exporta KPIs, gráficos, alarmes e os principais erros com mensagens — ótimo para o chat da equipe ou um ticket |
| 🚨 **Alarmes na tela** | Regras que você escolhe (pico, anomalia, issue nova, regressão, texto específico, alarme do CloudWatch, silêncio, coleta parada) mostram um aviso grande, com borda vermelha piscando, no meio da tela até você reconhecer — mais notificação do sistema e som opcionais. 100% local |
| ✅ **Fluxo de trabalho** | Resolver (reabre como *Regressão* se voltar) ou Ignorar (ruído conhecido) |
| 🚫 **Lista de bloqueio** | *Ignorar sempre* um falso alarme (pela issue ou por texto), com justificativa obrigatória: é descartado na coleta, não aparece nem conta como erro, até você remover da lista |
| ⏪ **Recuperação de lacunas** | O painel ficou fechado? Até 24h é recuperado sozinho; períodos maiores perguntam o que buscar — em segundo plano |
| 🧙 **Instalação guiada** | `iniciar.bat` / `iniciar.sh` verificam e instalam Python, dependências e AWS CLI; depois um assistente configura as credenciais (mesmo sem AWS CLI) |
| 🧪 **Modo demonstração** | `python app.py --demo` — experimente tudo com dados fictícios, sem conta AWS |

## Telas

| Detalhe da issue | Gerar Prompt |
|---|---|
| <img src="docs/screenshots/issue-detail.png" alt="Detalhe da issue" width="440"> | <img src="docs/screenshots/prompt.png" alt="Prompt" width="440"> |
| **Busca nos logs** | **Visão filtrada** |
| <img src="docs/screenshots/search.png" alt="Busca nos logs" width="440"> | <img src="docs/screenshots/filtered.png" alt="Filtrado" width="440"> |
| **Tema claro** | **Relatório PNG** |
| <img src="docs/screenshots/dashboard-light.png" alt="Tema claro" width="440"> | <img src="docs/screenshots/report.png" alt="Relatório PNG" width="440"> |

> Todas as telas vêm do **modo demonstração** (uma loja fictícia) — nenhum dado real.

---

## Início rápido

```bash
git clone https://github.com/Saulorj/claudeWatch.git
cd claudeWatch
```

| Sistema | Rode |
|---|---|
| **Windows** | dois cliques em **`iniciar.bat`** |
| **macOS / Linux** | `./iniciar.sh` |

O inicializador faz tudo, explicando cada passo:

1. **Python 3.10+** — confere a versão; instala se faltar (winget / Homebrew / apt, dnf, pacman, zypper).
2. **Dependências** — confere e instala o `requirements.txt` (só o `boto3`).
3. **AWS CLI** — opcional (só para login SSO); oferece instalar.
4. **Assistente de configuração** — só na 1ª vez (veja abaixo).
5. Abre o painel em **http://127.0.0.1:8765/**

### Só quer dar uma olhada?

```bash
python app.py --demo
```

Abre o painel com uma loja fictícia cheia de erros realistas. Não precisa de conta AWS.

---

## O assistente de configuração

Roda sozinho na 1ª vez (ou quando quiser, com `python configurar.py`) e grava as respostas num
arquivo local **`.env`**.

**1. Acesso à AWS** — escolha uma opção:

| Opção | Quando usar |
|---|---|
| Usar um profile existente | lista todos os profiles do AWS CLI encontrados na máquina |
| **Cadastrar uma chave de acesso** | o mais comum. Cole o *Access Key ID* e o *Secret*; o assistente grava o profile em `~/.aws` (igual ao `aws configure`). **Não precisa do AWS CLI** |
| SSO / IAM Identity Center | instala o AWS CLI se faltar e abre o login no navegador |
| Variáveis de ambiente | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` já definidas |
| “Não tenho chave” | passo a passo no console da AWS + salva a política IAM em `iam-policy.json` para o administrador |

**2. Teste de conexão** — conecta na hora e mostra a conta e quantos log groups encontrou. Se algo
falhar, explica o motivo e oferece a correção: erro de SSL causado por antivírus/proxy é
**corrigido sozinho no Windows**; sessão SSO expirada pode logar de novo; falta de permissão é
explicada; chave ou região erradas podem ser trocadas e testadas de novo.

**3. Preferências** — nome do painel, nome da empresa/sistema (vai nos prompts), intervalo de coleta,
quanto buscar na 1ª vez, dias de histórico local, quais log groups monitorar e a porta.

---

## Requisitos

| | |
|---|---|
| Sistema | Windows 10/11, macOS ou Linux, com internet |
| AWS | uma conta com CloudWatch Logs |
| Credenciais | **somente leitura** (chave de acesso, profile existente ou SSO) |
| Python | 3.10+ (o inicializador instala se faltar) |
| AWS CLI | opcional (só SSO) |

Não precisa de servidor de banco, Docker nem Node.js — o SQLite vem com o Python, e a biblioteca de
gráficos (Chart.js) vem dentro do projeto.

### Permissões IAM

Anexe a política gerenciada da AWS **`CloudWatchReadOnlyAccess`** ou esta política mínima:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchMonitorReadOnly",
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogGroups",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:GetLogEvents",
        "cloudwatch:DescribeAlarms",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

**Criando a chave de acesso (para quem administra a AWS):** IAM → Usuários → *Criar usuário* (sem
acesso ao console) → anexe a política acima → *Credenciais de segurança* → *Criar chave de acesso* →
caso de uso *Aplicação executada fora da AWS* → copie o Access Key ID e o Secret (aparece só uma vez).

### Custo

O CloudWatch Logs Insights cobra **US$ 0,005 por GB varrido**. Em contas com dezenas de MB de log por
dia, coletar a cada 5 minutos custa **centavos por mês**. Uma busca manual de 30 dias numa conta grande
pode custar alguns centavos.

---

## Configuração (`.env`)

Criado pelo assistente; dá para editar à mão (modelo comentado: [`.env.example`](.env.example)).
Variáveis de ambiente do sistema têm prioridade.

| Variável | Padrão | Para quê |
|---|---|---|
| `MONITOR_AWS_PROFILE` | `AWS_PROFILE` ou credenciais padrão | profile do AWS CLI |
| `MONITOR_AWS_REGION` | região do profile, senão `us-east-1` | região dos logs |
| `MONITOR_CA_BUNDLE` | — | `.pem` extra para antivírus/proxy que inspeciona HTTPS |
| `MONITOR_APP_NAME` | `CloudWatch Monitor` | nome no painel e no relatório |
| `MONITOR_SYSTEM_NAME` | — | nome da empresa/sistema citado nos prompts |
| `MONITOR_INTERVAL_MIN` | `5` | intervalo de coleta inicial (min) |
| `MONITOR_FIRST_LOOKBACK_HOURS` | `6` | horas buscadas na 1ª coleta |
| `MONITOR_RETENTION_DAYS` | `30` | dias de histórico local |
| `MONITOR_GROUP_INCLUDE` | — (todos) | regex: monitorar só log groups que casam |
| `MONITOR_GROUP_EXCLUDE` | warmup, containerinsights… | regex: log groups ignorados |
| `MONITOR_PORT` | `8765` | porta do painel |
| `MONITOR_AUTO_RELOAD` | `1` | quando o código muda (ex.: depois de um `git pull`), reinicia o servidor e recarrega a página sozinho — só quando aberto pelo `iniciar.bat`/`iniciar.sh` |
| `MONITOR_DB` | pasta de dados do usuário | caminho do banco SQLite |

Os valores de coleta são só os **iniciais** — depois, use **⚙ Configurações** no painel.

**Várias contas AWS?** Clone o projeto uma vez por conta, cada um com seu `.env`
(`MONITOR_PORT` e `MONITOR_DB` diferentes).

| Sistema | Local padrão do banco |
|---|---|
| Windows | `%LOCALAPPDATA%\cloudwatch-monitor\monitor.db` |
| macOS | `~/Library/Application Support/cloudwatch-monitor/monitor.db` |
| Linux | `~/.local/share/cloudwatch-monitor/monitor.db` |

Fica fora da pasta do projeto de propósito: OneDrive, Dropbox e iCloud travam ou corrompem arquivos
SQLite em uso.

---

## Usando o painel

- **Topo**: estado da coleta, intervalo, *Coletar agora*, **🔎 Buscar nos logs** (tecla `/`), tema, ⚙.
- **Filtros**: período, serviço e categoria. Clique numa barra ou na legenda para filtrar; clique de
  novo para limpar. O filtro ativo aparece em destaque.
- **Issues**: clique para ver o log completo, as ocorrências e as ações **✨ Gerar Prompt**,
  **Resolver**, **Ignorar** e **AWS Console**.
- **🔎 Busca** (tecla `/`), com dois escopos:

  | Escopo | Onde | Velocidade | Custo |
  |---|---|---|---|
  | **Erros coletados** (padrão) | texto completo dos erros guardados localmente + nome do serviço/log group | instantânea | grátis |
  | **Todos os logs (AWS)** — caixa de seleção | **todas** as linhas de todos os log groups, via Logs Insights | 5 s a 2 min | ~US$ 0,005 por GB varrido (a tela avisa) |

  Opções: período, log groups contendo um trecho do nome, palavra inteira (`387` não acha `13870`),
  maiúsculas e regex. Cada resultado tem **Abrir issue**, **Ver contexto**, **✨ Gerar Prompt**,
  AWS Console e copiar.
- **🖼 Gerar relatório**: baixa um PNG com o que está na tela (respeitando os filtros).
- **Alarmes**: os alarmes já existentes na conta (5xx, latência, fila de erro…); o monitor só lê o estado.

### Quando o painel ficou fechado

| Situação | O que acontece |
|---|---|
| 1ª execução | busca as últimas `MONITOR_FIRST_LOOKBACK_HOURS` (padrão 6h) |
| Fechado por até 24h | recupera tudo sozinho |
| Fechado por mais tempo | **pergunta**: tudo desde a última execução, últimas N horas, a partir de uma data, ou nada |
| Falha de rede/AWS | não avança; a próxima coleta tenta a mesma janela de novo |

---

## 🚨 Alarmes na tela

Deixe o painel numa segunda tela: quando uma regra dispara, um **aviso grande com borda vermelha
piscando** ocupa o meio da tela até você reconhecer (o título da aba também pisca). Notificação do
sistema e som são opcionais. **Nada sai da sua máquina** — os alarmes funcionam com o monitor aberto.

Abra **🔔 Alarmes** no topo para ligar/desligar regras, ajustar os números, **testar** como o aviso
aparece e ver o histórico. Regras:

| Regra | Exemplo | Já vem ligada |
|---|---|---|
| Issue nova | um erro que nunca tinha acontecido | ✅ |
| Regressão | um erro marcado como resolvido voltou | ✅ |
| Alarme do CloudWatch | um alarme da sua conta entrou em ALARM | ✅ |
| Pico | ≥ 20 erros em 10 min num serviço | — |
| Anomalia | 3× acima da média do serviço nas últimas 24h | — |
| Texto específico | um erro contendo `payment failed` | — |
| Silêncio | um log group sem receber logs há 60 min (serviço parado não gera erro) | — |
| Coleta parada | o monitor não consegue coletar há 20 min | — |

**Clique em qualquer alarme** (no cartão de alarmes do CloudWatch ou em “ℹ O que significa?” no aviso) para ver,
em linguagem simples, **o que é, por que disparou (valor medido × limite), se é grave e o que fazer** — com
botões para ver os erros do serviço relacionado, abrir no console da AWS ou gerar um prompt para o Claude.
Alarmes de auto scaling (`TargetTracking-*`), que ficam em ALARM boa parte do tempo por design, aparecem com
selo neutro e não disparam aviso na tela (dá para incluir por regra).

Cada regra pode ser limitada a serviços/categorias, tem gravidade (crítico = vermelho, aviso = laranja)
e um “não repetir por N minutos”. Issues ignoradas nunca disparam alarme.

## 🔒 Privacidade — seus dados ficam com você

**Nenhuma informação é enviada para nenhum servidor externo.** Na prática:

| | |
|---|---|
| 🚫 **Nenhum servidor de terceiros** | sem backend, sem telemetria, sem analytics, sem rastreamento, sem relatório de erros, sem conta ou cadastro |
| ☁️ **Só a sua AWS** | o único destino de rede do monitor é a **API da AWS da sua conta**, com as suas credenciais, para *ler* logs e alarmes (somente leitura — nunca grava nada na AWS) |
| 💻 **Tudo local** | o painel é servido em `127.0.0.1` (não fica acessível na rede); todo arquivo que ele carrega — inclusive a biblioteca de gráficos — vem da pasta do projeto, sem CDN |
| 🗄️ **Dados no seu disco** | os erros coletados ficam num arquivo SQLite na sua pasta de usuário (veja a tabela de local do banco acima) |
| 🔑 **Credenciais intocadas** | ficam nos arquivos padrão `~/.aws`; o projeto guarda só o *nome* do profile (`.env`, ignorado pelo git) |
| 🤖 **Nenhuma chamada a IA** | nada é enviado ao Claude ou a outra IA — o prompt só sai da sua máquina se **você** copiar e colar. Tokens, senhas, e-mails, CPF/CNPJ e strings de conexão são mascarados por padrão |

O único outro acesso à rede acontece **uma vez, na instalação**: o inicializador pode baixar o Python,
o `boto3` (PyPI) e o AWS CLI das fontes oficiais (winget, Homebrew, gerenciador de pacotes do Linux,
awscli.amazonaws.com). Nenhum dado seu é enviado. Clicar num link *AWS Console* apenas abre o console
da AWS no seu navegador.

### Segurança

- Proteção contra CSRF: a API local recusa requisições vindas de outros sites.
- Política IAM somente leitura; prefira um usuário IAM dedicado ou SSO, e troque as chaves periodicamente.
- Repositório protegido com secret scanning + push protection e Dependabot.

## Iniciar junto com o computador (opcional)

Rode o inicializador manualmente uma vez antes, para o assistente criar o `.env`.

**Windows** — `Win + R` → `shell:startup` → crie ali um atalho para o `iniciar.bat`
(em *Propriedades*, *Executar: Minimizada*).

**macOS** — crie `~/Library/LaunchAgents/com.cloudwatch-monitor.plist` (ajuste o caminho):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.cloudwatch-monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/SEU_USUARIO/caminho/claudeWatch/iniciar.sh</string>
    <string>--no-browser</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/cloudwatch-monitor.log</string>
  <key>StandardErrorPath</key><string>/tmp/cloudwatch-monitor.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.cloudwatch-monitor.plist
```

**Linux (systemd, por usuário)** — crie `~/.config/systemd/user/cloudwatch-monitor.service`:

```ini
[Unit]
Description=CloudWatch Monitor

[Service]
WorkingDirectory=%h/caminho/claudeWatch
ExecStart=%h/caminho/claudeWatch/iniciar.sh --no-browser
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now cloudwatch-monitor
journalctl --user -u cloudwatch-monitor -f     # ver o log
```

## Problemas comuns

| Sintoma | Solução |
|---|---|
| “Não consegui acessar a AWS” ao iniciar | rode `python configurar.py` — ele testa e diz o que corrigir (`python app.py --check` só testa) |
| `SSL: CERTIFICATE_VERIFY_FAILED` | antivírus/proxy inspecionando HTTPS: `python configurar.py` (corrige sozinho no Windows) ou `MONITOR_CA_BUNDLE` |
| `AccessDenied` | faltam permissões — veja *Permissões IAM* |
| Sessão SSO expirada | `aws sso login --profile SEU_PROFILE` |
| Porta ocupada | mude `MONITOR_PORT` no `.env` |
| Painel vazio | aguarde a 1ª coleta (“coleta ok” no topo) ou aumente o período |
| Python não encontrado após instalar | feche e abra o terminal e rode o inicializador de novo |
| Linux: `ensurepip is not available` | `sudo apt install python3-venv` (o inicializador oferece) |

## Como funciona

A cada intervalo, o coletor roda consultas do **CloudWatch Logs Insights** em todos os log groups
(50 por consulta), filtrando por uma regex de erro editável. As janelas se sobrepõem 3 minutos para
pegar logs que chegam atrasados; duplicados são descartados. Tracebacks em várias linhas são juntados
e buscados completos no log stream; linhas INFO/DEBUG são ignoradas, exceto quando indicam 5xx ou
timeout. De logs JSON, o tipo e a mensagem do erro viram o título. Erros parecidos viram uma issue
(Timeout / HTTP 5xx / Fatal / Exceção / Erro), guardada em SQLite e servida por um pequeno servidor
HTTP da biblioteca padrão.

```
iniciar.bat / iniciar.sh   inicializadores (requisitos + assistente + painel)
configurar.py              assistente de configuração  →  .env
app.py                     servidor local + API  (--demo, --check, --port, --no-browser)
collector.py               coleta Logs Insights, alarmes do CloudWatch, recuperação de lacunas, busca
alerts.py                  regras de alarmes na tela (avaliadas localmente a cada 30 s)
fingerprint.py             classificação e agrupamento de erros
prompt.py                  gerador de prompts (com mascaramento)
demo.py                    dados fictícios do modo demonstração
db.py · config.py          banco SQLite · configuração
web/                       painel (HTML/CSS/JS puro + Chart.js incluso, MIT)
```

## Contribuindo

Issues e pull requests são bem-vindos. O projeto é mantido no esquema “quando der”.

## Licença

[MIT](LICENSE) © Saulo Oliveira

---

<sub>Sem vínculo, endosso ou patrocínio da Amazon Web Services ou da Anthropic. “AWS” e “CloudWatch”
são marcas da Amazon.com, Inc.; “Claude” é marca da Anthropic, PBC — citadas apenas para descrever
compatibilidade.</sub>
