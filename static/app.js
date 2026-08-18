let ESTADO = null; // último snapshot vindo de /api/config
let ultimaLinhaLog = 0;
let pollingLog = null;

// ---------------------------------------------------------------
// Abas
// ---------------------------------------------------------------
document.querySelectorAll(".aba-botao").forEach((botao) => {
  botao.addEventListener("click", () => {
    document.querySelectorAll(".aba-botao").forEach((b) => b.classList.remove("ativo"));
    document.querySelectorAll(".painel-aba").forEach((p) => p.classList.add("oculto"));
    botao.classList.add("ativo");
    document.getElementById("aba-" + botao.dataset.aba).classList.remove("oculto");
    botao.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  });
});

// ---------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------
async function chamarAPI(url, opcoes = {}) {
  const resposta = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opcoes,
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(dados.erro || "Erro na requisição.");
  return dados;
}

function linhasTextarea(id) {
  return document.getElementById(id).value.split("\n").map((l) => l.trim()).filter(Boolean);
}

// ---------------------------------------------------------------
// Carregar tudo ao abrir a página
// ---------------------------------------------------------------
async function carregarTudo() {
  ESTADO = await chamarAPI("/api/config");
  preencherGeral();
  preencherBusca();
  preencherLinks();
  preencherExtras();
  preencherRedes();
  preencherAgenda();
  atualizarStatus(ESTADO.status.executando);
}

// ---------------------------------------------------------------
// ABA CONFIGURAÇÃO
// ---------------------------------------------------------------
function preencherGeral() {
  const g = ESTADO.geral;
  document.getElementById("cfg-app-id").value = g.app_id;
  document.getElementById("cfg-app-secret").value = g.app_secret;
  document.getElementById("cfg-limite-envios").value = g.limite_envios;

  const container = document.getElementById("lista-webhooks");
  container.innerHTML = "";
  Object.entries(g.webhooks).forEach(([nome, dados]) => {
    const linha = document.createElement("div");
    linha.className = "cartao-item";
    linha.innerHTML = `
      <label class="linha-check" style="margin:0;flex:0 0 auto;">
        <input type="checkbox" data-webhook-ativo="${nome}" ${dados.ativo ? "checked" : ""}>
      </label>
      <div class="cartao-info">
        <div class="cartao-nome">${nome}</div>
        <input type="text" data-webhook-url="${nome}" placeholder="URL do webhook" value="${dados.url || ""}" style="margin-top:6px;">
      </div>`;
    container.appendChild(linha);
  });
}

function coletarWebhooks() {
  const webhooks = {};
  document.querySelectorAll("[data-webhook-ativo]").forEach((chk) => {
    const nome = chk.dataset.webhookAtivo;
    const url = document.querySelector(`[data-webhook-url="${nome}"]`).value.trim();
    webhooks[nome] = { ativo: chk.checked, url };
  });
  return webhooks;
}

async function salvarGeral() {
  await chamarAPI("/api/config/geral", {
    method: "POST",
    body: JSON.stringify({
      app_id: document.getElementById("cfg-app-id").value,
      app_secret: document.getElementById("cfg-app-secret").value,
      limite_envios: document.getElementById("cfg-limite-envios").value,
      webhooks: coletarWebhooks(),
    }),
  });
  alert("Configuração salva.");
}

async function testarWebhooks() {
  const caixa = document.getElementById("resultado-teste-webhook");
  caixa.textContent = "Testando...";
  try {
    const resp = await chamarAPI("/api/config/testar-webhooks", {
      method: "POST",
      body: JSON.stringify({ webhooks: coletarWebhooks() }),
    });
    caixa.textContent = resp.resultados.join("\n");
  } catch (erro) {
    caixa.textContent = "❌ " + erro.message;
  }
}

// ---------------------------------------------------------------
// ABA BUSCA
// ---------------------------------------------------------------
function preencherBusca() {
  const b = ESTADO.busca;
  document.getElementById("busca-palavras").value = b.palavras.join("\n");
  document.getElementById("busca-ordenacao").value = b.ordenacao;
  document.getElementById("busca-limite").value = b.limite_por_palavra;
  document.getElementById("busca-max-paginas").value = b.max_paginas;
  document.getElementById("busca-paginacao-completa").checked = b.paginacao_completa;

  const sel = document.getElementById("ia-categoria");
  sel.innerHTML = "";
  [...b.categorias_disponiveis, "Personalizada..."].forEach((cat) => {
    const op = document.createElement("option");
    op.value = cat; op.textContent = cat;
    sel.appendChild(op);
  });
}

async function salvarBusca() {
  const resp = await chamarAPI("/api/config/busca", {
    method: "POST",
    body: JSON.stringify({
      palavras: linhasTextarea("busca-palavras"),
      ordenacao: document.getElementById("busca-ordenacao").value,
      limite_por_palavra: document.getElementById("busca-limite").value,
      max_paginas: document.getElementById("busca-max-paginas").value,
      paginacao_completa: document.getElementById("busca-paginacao-completa").checked,
    }),
  });
  alert(resp.total + " palavra(s)-chave salva(s).");
}

async function gerarPalavrasIA() {
  const status = document.getElementById("ia-status");
  let categoria = document.getElementById("ia-categoria").value;
  if (categoria === "Personalizada...") {
    categoria = prompt("Digite o nome da categoria personalizada:");
    if (!categoria) return;
  }
  const quantidade = document.getElementById("ia-quantidade").value;
  status.textContent = "Gerando palavras-chave com IA...";
  try {
    const resp = await chamarAPI("/api/gerar-palavras-ia", {
      method: "POST",
      body: JSON.stringify({ categoria, quantidade }),
    });
    const campo = document.getElementById("busca-palavras");
    const existentes = new Set(linhasTextarea("busca-palavras").map((p) => p.toLowerCase()));
    const novas = resp.palavras.filter((p) => !existentes.has(p.toLowerCase()));
    campo.value = (campo.value.trim() + "\n" + novas.join("\n")).trim();
    status.textContent = novas.length ? `✅ ${novas.length} palavra(s) nova(s) adicionada(s).` : "A IA só sugeriu palavras que já estavam na lista.";
  } catch (erro) {
    status.textContent = "❌ " + erro.message;
  }
}

// ---------------------------------------------------------------
// ABA LINKS
// ---------------------------------------------------------------
function preencherLinks() {
  document.getElementById("links-texto").value = ESTADO.links.join("\n");
}

async function salvarLinks() {
  const resp = await chamarAPI("/api/config/links", {
    method: "POST",
    body: JSON.stringify({ links: linhasTextarea("links-texto") }),
  });
  alert(resp.total + " link(s) salvo(s).");
}

// ---------------------------------------------------------------
// ABA MANUAL (extras)
// ---------------------------------------------------------------
function preencherExtras() {
  const container = document.getElementById("lista-extras");
  container.innerHTML = "";
  if (!ESTADO.extras.length) {
    container.innerHTML = `<p class="dica">Nenhum produto manual cadastrado.</p>`;
    return;
  }
  ESTADO.extras.forEach((p) => {
    const item = document.createElement("div");
    item.className = "cartao-item";
    item.innerHTML = `
      <div class="cartao-info">
        <div class="cartao-nome">${p.id_produto}</div>
        <div class="cartao-sub">R$ ${p.preco_original.toFixed(2)} → R$ ${p.preco_desconto.toFixed(2)} · ${p.taxa_comissao || ""}</div>
      </div>
      <div class="cartao-acoes">
        <button onclick="removerExtra('${p.id_produto}')">🗑️</button>
      </div>`;
    container.appendChild(item);
  });
}

async function removerExtra(id) {
  if (!confirm("Remover este produto?")) return;
  ESTADO.extras = (await chamarAPI("/api/extras/" + encodeURIComponent(id), { method: "DELETE" })).extras;
  preencherExtras();
}

function abrirModalExtra() {
  abrirModal("Adicionar produto extra", [
    { id: "id_produto", rotulo: "ID do Produto" },
    { id: "link_afiliado", rotulo: "Link de Afiliado" },
    { id: "preco_original", rotulo: "Preço Original (R$)", tipo: "number" },
    { id: "preco_desconto", rotulo: "Preço com Desconto (R$)", tipo: "number" },
    { id: "taxa_comissao", rotulo: "Taxa de Comissão (ex: 8%)" },
  ], async (dados) => {
    const resp = await chamarAPI("/api/extras", { method: "POST", body: JSON.stringify(dados) });
    ESTADO.extras = resp.extras;
    preencherExtras();
  });
}

// ---------------------------------------------------------------
// ABA REDES SOCIAIS
// ---------------------------------------------------------------
function preencherRedes() {
  const r = ESTADO.redes;
  document.getElementById("tg-ativo").checked = r.telegram_ativo;
  document.getElementById("tg-token").value = r.telegram_token;
  document.getElementById("fb-ativo").checked = r.facebook_ativo;
  document.getElementById("ig-ativo").checked = r.instagram_ativo;
  document.getElementById("groq-token").value = r.groq_token;
  document.getElementById("limite-postagens").value = r.limite_postagens;

  renderizarDestinos("telegram", r.telegram_destinos, "chat_id");
  renderizarDestinos("facebook", r.facebook_paginas, "page_id");
  renderizarDestinos("instagram", r.instagram_contas, "business_id");
}

const CAMPO_ID_POR_TIPO = { telegram: "chat_id", facebook: "page_id", instagram: "business_id" };
const CONTAINER_POR_TIPO = { telegram: "lista-telegram", facebook: "lista-facebook", instagram: "lista-instagram" };
const CHAVE_LISTA_POR_TIPO = { telegram: "telegram_destinos", facebook: "facebook_paginas", instagram: "instagram_contas" };

function renderizarDestinos(tipo, lista, campoId) {
  const container = document.getElementById(CONTAINER_POR_TIPO[tipo]);
  container.innerHTML = "";
  if (!lista.length) {
    container.innerHTML = `<p class="dica">Nenhum cadastrado ainda.</p>`;
    return;
  }
  lista.forEach((item, i) => {
    const cartao = document.createElement("div");
    cartao.className = "cartao-item";
    const categorias = (item.categorias || []).length ? item.categorias.join(", ") : "Tudo";
    cartao.innerHTML = `
      <div class="cartao-info">
        <div class="cartao-nome">${item.nome || item[campoId]}</div>
        <div class="cartao-sub">${item[campoId]} · ${categorias}</div>
      </div>
      <span class="pilula ${item.ativo ? "ativa" : ""}">${item.ativo ? "Ativo" : "Pausado"}</span>
      <div class="cartao-acoes">
        <button onclick="alternarDestino('${tipo}', ${i})">${item.ativo ? "⛔" : "✅"}</button>
        <button onclick="removerDestino('${tipo}', ${i})">🗑️</button>
      </div>`;
    container.appendChild(cartao);
  });
}

async function alternarDestino(tipo, indice) {
  const resp = await chamarAPI(`/api/redes/${tipo}/${indice}/alternar`, { method: "POST" });
  ESTADO.redes[CHAVE_LISTA_POR_TIPO[tipo]] = resp.lista;
  renderizarDestinos(tipo, resp.lista, CAMPO_ID_POR_TIPO[tipo]);
}

async function removerDestino(tipo, indice) {
  if (!confirm("Remover?")) return;
  const resp = await chamarAPI(`/api/redes/${tipo}/${indice}`, { method: "DELETE" });
  ESTADO.redes[CHAVE_LISTA_POR_TIPO[tipo]] = resp.lista;
  renderizarDestinos(tipo, resp.lista, CAMPO_ID_POR_TIPO[tipo]);
}

function abrirModalDestino(tipo) {
  const camposPorTipo = {
    telegram: [
      { id: "nome", rotulo: "Nome (só pra identificar)" },
      { id: "chat_id", rotulo: "Chat ID / Canal (@canal ou ID numérico)" },
      { id: "categorias_texto", rotulo: "Categorias aceitas (vírgula, vazio = tudo)" },
    ],
    facebook: [
      { id: "nome", rotulo: "Nome (só pra identificar)" },
      { id: "page_id", rotulo: "Page ID" },
      { id: "page_token", rotulo: "Page Access Token", tipo: "password" },
      { id: "categorias_texto", rotulo: "Categorias aceitas (vírgula, vazio = tudo)" },
    ],
    instagram: [
      { id: "nome", rotulo: "Nome (só pra identificar)" },
      { id: "business_id", rotulo: "Instagram Business Account ID" },
      { id: "access_token", rotulo: "Access Token", tipo: "password" },
      { id: "categorias_texto", rotulo: "Categorias aceitas (vírgula, vazio = tudo)" },
    ],
  };
  const titulos = { telegram: "Adicionar grupo/canal do Telegram", facebook: "Adicionar página do Facebook", instagram: "Adicionar conta do Instagram" };

  abrirModal(titulos[tipo], camposPorTipo[tipo], async (dados) => {
    const categorias = (dados.categorias_texto || "").split(",").map((c) => c.trim()).filter(Boolean);
    delete dados.categorias_texto;
    dados.categorias = categorias;
    const resp = await chamarAPI(`/api/redes/${tipo}`, { method: "POST", body: JSON.stringify(dados) });
    ESTADO.redes[CHAVE_LISTA_POR_TIPO[tipo]] = resp.lista;
    renderizarDestinos(tipo, resp.lista, CAMPO_ID_POR_TIPO[tipo]);
  });
}

async function salvarRedes() {
  await chamarAPI("/api/redes/opcoes", {
    method: "POST",
    body: JSON.stringify({
      telegram_ativo: document.getElementById("tg-ativo").checked,
      telegram_token: document.getElementById("tg-token").value,
      facebook_ativo: document.getElementById("fb-ativo").checked,
      instagram_ativo: document.getElementById("ig-ativo").checked,
      groq_token: document.getElementById("groq-token").value,
      limite_postagens: document.getElementById("limite-postagens").value,
    }),
  });
  alert("Configurações de redes sociais salvas.");
}

// ---------------------------------------------------------------
// ABA AGENDAMENTO
// ---------------------------------------------------------------
function preencherAgenda() {
  document.getElementById("agenda-horarios").value = ESTADO.agendamento.horarios.join("\n");
  atualizarStatusAgenda(ESTADO.agendamento.ativo, ESTADO.agendamento.horarios);
}

function atualizarStatusAgenda(ativo, horarios) {
  const botao = document.getElementById("botao-toggle-agenda");
  const status = document.getElementById("status-agenda");
  if (ativo) {
    botao.textContent = "⏹ Parar Agendamento";
    status.textContent = "Status: ativo — " + horarios.join(", ");
  } else {
    botao.textContent = "▶️ Ativar Agendamento";
    status.textContent = "Status: inativo";
  }
}

async function salvarAgenda() {
  const resp = await chamarAPI("/api/agendamento", {
    method: "POST",
    body: JSON.stringify({ horarios: linhasTextarea("agenda-horarios") }),
  });
  alert(resp.horarios.length + " horário(s) salvo(s).");
}

async function alternarAgenda() {
  try {
    const resp = await chamarAPI("/api/agendamento/alternar", { method: "POST" });
    atualizarStatusAgenda(resp.ativo, resp.horarios || linhasTextarea("agenda-horarios"));
  } catch (erro) {
    alert(erro.message);
  }
}

// ---------------------------------------------------------------
// ABA LOG / EXECUTAR
// ---------------------------------------------------------------
function atualizarStatus(executando) {
  const bolha = document.getElementById("bolha-status");
  const texto = document.getElementById("texto-status");
  const botaoExec = document.getElementById("botao-executar");
  const botaoParar = document.getElementById("botao-parar");
  bolha.className = "bolha-status " + (executando ? "rodando" : "parado");
  texto.textContent = executando ? "executando" : "parado";
  botaoExec.disabled = executando;
  botaoParar.disabled = !executando;
}

async function executarRobo() {
  try {
    await chamarAPI("/api/executar", { method: "POST" });
    document.querySelector('[data-aba="log"]').click();
    iniciarPollingLog();
  } catch (erro) {
    alert(erro.message);
  }
}

async function pararRobo() {
  try {
    await chamarAPI("/api/parar", { method: "POST" });
  } catch (erro) {
    alert(erro.message);
  }
}

async function limparLog() {
  await chamarAPI("/api/log/limpar", { method: "POST" });
  ultimaLinhaLog = 0;
  document.getElementById("caixa-log").textContent = "";
}

function iniciarPollingLog() {
  if (pollingLog) return;
  pollingLog = setInterval(async () => {
    try {
      const resp = await chamarAPI("/api/log?desde=" + ultimaLinhaLog);
      if (resp.linhas.length) {
        const caixa = document.getElementById("caixa-log");
        caixa.textContent += resp.linhas.join("\n") + "\n";
        caixa.scrollTop = caixa.scrollHeight;
        ultimaLinhaLog = resp.total;
      }
      atualizarStatus(resp.executando);
    } catch (erro) {
      // silencioso — próxima tentativa resolve
    }
  }, 2000);
}

// ---------------------------------------------------------------
// Modal genérico (usado por extras e destinos de redes sociais)
// ---------------------------------------------------------------
function abrirModal(titulo, campos, aoConfirmar) {
  document.getElementById("modal-titulo").textContent = titulo;
  const container = document.getElementById("modal-campos");
  container.innerHTML = "";
  campos.forEach((c) => {
    const rotulo = document.createElement("label");
    rotulo.textContent = c.rotulo;
    const input = document.createElement("input");
    input.type = c.tipo || "text";
    input.id = "modal-campo-" + c.id;
    container.appendChild(rotulo);
    container.appendChild(input);
  });

  const botao = document.getElementById("modal-confirmar");
  botao.onclick = async () => {
    const dados = {};
    campos.forEach((c) => { dados[c.id] = document.getElementById("modal-campo-" + c.id).value.trim(); });
    try {
      await aoConfirmar(dados);
      fecharModal();
    } catch (erro) {
      alert(erro.message);
    }
  };

  document.getElementById("modal-fundo").classList.remove("oculto");
}

function fecharModal() {
  document.getElementById("modal-fundo").classList.add("oculto");
}

// ---------------------------------------------------------------
carregarTudo();
iniciarPollingLog();
