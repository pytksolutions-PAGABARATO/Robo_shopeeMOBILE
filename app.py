"""
====================================================================
PAINEL DE CONTROLE - ROBÔ PAGABARATO (versão Web / Mobile)
====================================================================
Faz a MESMA coisa do gui_pagabarato.py (Tkinter), mas roda num
navegador -- inclusive no celular. O robô continua rodando no
servidor onde este app.py estiver hospedado; o celular só acessa
a página pra controlar e ver o log.

Requisito: este arquivo deve estar na MESMA PASTA do
"pagabarato_robo.py" (o robô em si -- não foi alterado).

Instalação:
    pip install flask requests beautifulsoup4 lxml Pillow

Como rodar localmente:
    set PAINEL_SENHA=suasenha   (Windows)  /  export PAINEL_SENHA=suasenha (Linux/Mac)
    python app.py
    -> abra http://localhost:5000 no navegador (ou do celular, veja README.md)

Como publicar de graça (Render.com): veja README.md
====================================================================
"""

import os
import sys
import json
import threading
import functools
from datetime import datetime

from flask import Flask, request, jsonify, session, redirect, url_for, render_template

DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
if DIRETORIO_ATUAL not in sys.path:
    sys.path.insert(0, DIRETORIO_ATUAL)

import pagabarato_robo as robo  # noqa: E402

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "troque-esta-chave-em-producao")

SENHA_PAINEL = os.environ.get("PAINEL_SENHA", "pagabarato123")

ARQUIVO_CONFIG = os.path.join(DIRETORIO_ATUAL, "config_pagabarato.json")
ARQUIVO_PALAVRAS = os.path.join(DIRETORIO_ATUAL, "palavras_chave_pagabarato.json")
ARQUIVO_LINKS_PRODUTOS = os.path.join(DIRETORIO_ATUAL, "links_produtos_pagabarato.json")
ARQUIVO_PRODUTOS_EXTRAS = os.path.join(DIRETORIO_ATUAL, "produtos_extras_pagabarato.json")
ARQUIVO_REDES_SOCIAIS = os.path.join(DIRETORIO_ATUAL, "redes_sociais_pagabarato.json")
ARQUIVO_AGENDAMENTO = os.path.join(DIRETORIO_ATUAL, "agendamento_pagabarato.json")

OPCOES_ORDENACAO = {
    "Maior comissão (recomendado)": 5,
    "Mais vendidos": 2,
    "Relevância": 1,
    "Maior preço": 3,
    "Menor preço": 4,
}

# ----------------------------------------------------------------
# Estado em memória (compartilhado entre as requisições)
# ----------------------------------------------------------------
ESTADO = {
    "robo_em_execucao": False,
    "log": [],  # lista de strings
    "agendamento_ativo": False,
    "ultimo_minuto_executado": None,
}
TRAVA = threading.Lock()


def carregar_json(caminho, valor_padrao):
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return valor_padrao


def salvar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def log(mensagem: str):
    with TRAVA:
        ESTADO["log"].append(mensagem)
        # evita crescer pra sempre
        if len(ESTADO["log"]) > 3000:
            ESTADO["log"] = ESTADO["log"][-3000:]


class RedirecionadorDeSaida:
    """Redireciona os print() do robô para o log do painel web."""

    def write(self, texto):
        if texto.strip() != "":
            log(texto.rstrip("\n"))

    def flush(self):
        pass


# ----------------------------------------------------------------
# Login simples (uma senha só, pra proteger o painel na internet)
# ----------------------------------------------------------------
def login_obrigatorio(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logado"):
            if request.path.startswith("/api/"):
                return jsonify({"erro": "não autenticado"}), 401
            return redirect(url_for("tela_login"))
        return func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def tela_login():
    erro = None
    if request.method == "POST":
        if request.form.get("senha") == SENHA_PAINEL:
            session["logado"] = True
            session.permanent = True
            return redirect(url_for("pagina_inicial"))
        erro = "Senha incorreta."
    return render_template("login.html", erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("tela_login"))


@app.route("/")
@login_obrigatorio
def pagina_inicial():
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "PagaBarato - Painel",
        "short_name": "PagaBarato",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f1115",
        "theme_color": "#0f1115",
        "icons": [],
    })


# ----------------------------------------------------------------
# API - Configuração geral (Shopee + webhooks + limite)
# ----------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
@login_obrigatorio
def api_get_config():
    config = carregar_json(ARQUIVO_CONFIG, {})
    palavras = carregar_json(ARQUIVO_PALAVRAS, robo.PALAVRAS_CHAVE)
    links = carregar_json(ARQUIVO_LINKS_PRODUTOS, list(robo.LINKS_PRODUTOS_MANUAIS))
    extras = carregar_json(ARQUIVO_PRODUTOS_EXTRAS, [])
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    agendamento = carregar_json(ARQUIVO_AGENDAMENTO, {})

    return jsonify({
        "geral": {
            "app_id": config.get("app_id", ""),
            "app_secret": config.get("app_secret", ""),
            "limite_envios": config.get("limite_envios", robo.LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO or 0),
            "webhooks": config.get("webhooks", {
                nome: {"ativo": nome == "Google Apps Script (grátis, sem Make)", "url": ""}
                for nome in ["Google Apps Script (grátis, sem Make)", "Make.com", "n8n", "Activepieces"]
            }),
        },
        "busca": {
            "palavras": palavras,
            "ordenacao": config.get("ordenacao", "Maior comissão (recomendado)"),
            "limite_por_palavra": config.get("limite_por_palavra", robo.LIMITE_POR_PALAVRA),
            "max_paginas": config.get("max_paginas", robo.MAX_PAGINAS_POR_PALAVRA),
            "paginacao_completa": config.get("paginacao_completa", robo.PAGINACAO_COMPLETA),
            "categorias_disponiveis": list(robo.CATEGORIAS_DETECCAO.keys()),
        },
        "links": links,
        "extras": extras,
        "redes": {
            "telegram_ativo": redes.get("telegram_ativo", False),
            "telegram_token": redes.get("telegram_token", ""),
            "telegram_destinos": redes.get("telegram_destinos", []),
            "facebook_ativo": redes.get("facebook_ativo", False),
            "facebook_paginas": redes.get("facebook_paginas", []),
            "instagram_ativo": redes.get("instagram_ativo", False),
            "instagram_contas": redes.get("instagram_contas", []),
            "groq_token": redes.get("groq_token", ""),
            "limite_postagens": redes.get("limite_postagens", robo.LIMITE_POSTAGENS_REDES_SOCIAIS),
        },
        "agendamento": {
            "horarios": agendamento.get("horarios", []),
            "ativo": ESTADO["agendamento_ativo"],
        },
        "status": {
            "executando": ESTADO["robo_em_execucao"],
        },
    })


@app.route("/api/config/geral", methods=["POST"])
@login_obrigatorio
def api_salvar_geral():
    dados = request.get_json(force=True)
    config = carregar_json(ARQUIVO_CONFIG, {})
    config["app_id"] = dados.get("app_id", "").strip()
    config["app_secret"] = dados.get("app_secret", "").strip()
    config["limite_envios"] = int(dados.get("limite_envios", 0) or 0)
    config["webhooks"] = dados.get("webhooks", {})
    salvar_json(ARQUIVO_CONFIG, config)
    return jsonify({"ok": True})


@app.route("/api/config/testar-webhooks", methods=["POST"])
@login_obrigatorio
def api_testar_webhooks():
    dados = request.get_json(force=True)
    webhooks = dados.get("webhooks", {})
    ativos = [{"nome": n, **v} for n, v in webhooks.items() if v.get("ativo") and v.get("url")]
    if not ativos:
        return jsonify({"erro": "Nenhum webhook ativo com URL preenchida."}), 400

    payload_teste = {
        "ID_Produto": "TESTE_CONEXAO",
        "Descricao": "Produto de teste do painel PagaBarato",
        "Preco_Original": 0,
        "Preco_Desconto": 0,
        "Taxa_Comissao": "0%",
        "Link_Afiliado": "https://exemplo.com",
        "URL_Imagem": "",
    }
    import requests
    resultados = []
    for w in ativos:
        try:
            resp = requests.post(w["url"], json=payload_teste, timeout=10)
            resp.raise_for_status()
            resultados.append(f"✅ {w['nome']}: status {resp.status_code}")
        except Exception as erro:
            resultados.append(f"❌ {w['nome']}: {erro}")
    return jsonify({"resultados": resultados})


# ----------------------------------------------------------------
# API - Busca automática (palavras-chave + IA)
# ----------------------------------------------------------------
@app.route("/api/config/busca", methods=["POST"])
@login_obrigatorio
def api_salvar_busca():
    dados = request.get_json(force=True)
    palavras = [p.strip() for p in dados.get("palavras", []) if p.strip()]
    salvar_json(ARQUIVO_PALAVRAS, palavras)

    config = carregar_json(ARQUIVO_CONFIG, {})
    config["ordenacao"] = dados.get("ordenacao", "Maior comissão (recomendado)")
    config["limite_por_palavra"] = int(dados.get("limite_por_palavra", robo.LIMITE_POR_PALAVRA))
    config["max_paginas"] = int(dados.get("max_paginas", robo.MAX_PAGINAS_POR_PALAVRA))
    config["paginacao_completa"] = bool(dados.get("paginacao_completa", False))
    salvar_json(ARQUIVO_CONFIG, config)
    return jsonify({"ok": True, "total": len(palavras)})


@app.route("/api/gerar-palavras-ia", methods=["POST"])
@login_obrigatorio
def api_gerar_palavras_ia():
    dados = request.get_json(force=True)
    categoria = dados.get("categoria", "").strip()
    quantidade = int(dados.get("quantidade", 8))
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    token = redes.get("groq_token", "").strip()

    if not categoria:
        return jsonify({"erro": "Informe a categoria."}), 400
    if not token:
        return jsonify({"erro": "Configure o Token Groq na aba Redes Sociais antes de gerar com IA."}), 400

    robo.GROQ_TOKEN = token
    try:
        palavras = robo.gerar_palavras_chave_com_ia(categoria, quantidade)
        return jsonify({"palavras": palavras})
    except Exception as erro:
        return jsonify({"erro": str(erro)}), 500


# ----------------------------------------------------------------
# API - Links de produtos
# ----------------------------------------------------------------
@app.route("/api/config/links", methods=["POST"])
@login_obrigatorio
def api_salvar_links():
    dados = request.get_json(force=True)
    links = [l.strip() for l in dados.get("links", []) if l.strip()]
    salvar_json(ARQUIVO_LINKS_PRODUTOS, links)
    return jsonify({"ok": True, "total": len(links)})


# ----------------------------------------------------------------
# API - Produtos extras (manual)
# ----------------------------------------------------------------
@app.route("/api/extras", methods=["POST"])
@login_obrigatorio
def api_adicionar_extra():
    produto = request.get_json(force=True)
    campos_obrigatorios = ["id_produto", "link_afiliado", "preco_original", "preco_desconto"]
    if not all(produto.get(c) not in (None, "") for c in campos_obrigatorios):
        return jsonify({"erro": "Preencha ID, link, preço original e preço com desconto."}), 400

    extras = carregar_json(ARQUIVO_PRODUTOS_EXTRAS, [])
    if any(p["id_produto"] == produto["id_produto"] for p in extras):
        return jsonify({"erro": "Já existe um produto extra com esse ID."}), 400

    try:
        produto["preco_original"] = float(produto["preco_original"])
        produto["preco_desconto"] = float(produto["preco_desconto"])
    except ValueError:
        return jsonify({"erro": "Preços devem ser numéricos."}), 400

    extras.append(produto)
    salvar_json(ARQUIVO_PRODUTOS_EXTRAS, extras)
    return jsonify({"ok": True, "extras": extras})


@app.route("/api/extras/<id_produto>", methods=["DELETE"])
@login_obrigatorio
def api_remover_extra(id_produto):
    extras = carregar_json(ARQUIVO_PRODUTOS_EXTRAS, [])
    extras = [p for p in extras if p["id_produto"] != id_produto]
    salvar_json(ARQUIVO_PRODUTOS_EXTRAS, extras)
    return jsonify({"ok": True, "extras": extras})


# ----------------------------------------------------------------
# API - Redes sociais (Telegram / Facebook / Instagram / Groq)
# ----------------------------------------------------------------
def _api_destino_generico(campo_lista):
    """Helper que cria as rotas add/remove/toggle pra telegram, facebook e instagram."""
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    return redes, redes.get(campo_lista, [])


@app.route("/api/redes/<tipo>", methods=["POST"])
@login_obrigatorio
def api_adicionar_destino(tipo):
    mapa_campo = {
        "telegram": "telegram_destinos",
        "facebook": "facebook_paginas",
        "instagram": "instagram_contas",
    }
    if tipo not in mapa_campo:
        return jsonify({"erro": "Tipo inválido."}), 400

    novo = request.get_json(force=True)
    novo["ativo"] = True
    novo.setdefault("categorias", [])

    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    lista = redes.get(mapa_campo[tipo], [])
    lista.append(novo)
    redes[mapa_campo[tipo]] = lista
    salvar_json(ARQUIVO_REDES_SOCIAIS, redes)
    return jsonify({"ok": True, "lista": lista})


@app.route("/api/redes/<tipo>/<int:indice>", methods=["DELETE"])
@login_obrigatorio
def api_remover_destino(tipo, indice):
    mapa_campo = {
        "telegram": "telegram_destinos",
        "facebook": "facebook_paginas",
        "instagram": "instagram_contas",
    }
    if tipo not in mapa_campo:
        return jsonify({"erro": "Tipo inválido."}), 400
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    lista = redes.get(mapa_campo[tipo], [])
    if 0 <= indice < len(lista):
        lista.pop(indice)
    redes[mapa_campo[tipo]] = lista
    salvar_json(ARQUIVO_REDES_SOCIAIS, redes)
    return jsonify({"ok": True, "lista": lista})


@app.route("/api/redes/<tipo>/<int:indice>/alternar", methods=["POST"])
@login_obrigatorio
def api_alternar_destino(tipo, indice):
    mapa_campo = {
        "telegram": "telegram_destinos",
        "facebook": "facebook_paginas",
        "instagram": "instagram_contas",
    }
    if tipo not in mapa_campo:
        return jsonify({"erro": "Tipo inválido."}), 400
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    lista = redes.get(mapa_campo[tipo], [])
    if 0 <= indice < len(lista):
        lista[indice]["ativo"] = not lista[indice].get("ativo", True)
    redes[mapa_campo[tipo]] = lista
    salvar_json(ARQUIVO_REDES_SOCIAIS, redes)
    return jsonify({"ok": True, "lista": lista})


@app.route("/api/redes/opcoes", methods=["POST"])
@login_obrigatorio
def api_salvar_opcoes_redes():
    dados = request.get_json(force=True)
    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})
    redes["telegram_ativo"] = bool(dados.get("telegram_ativo", False))
    redes["telegram_token"] = dados.get("telegram_token", "").strip()
    redes["facebook_ativo"] = bool(dados.get("facebook_ativo", False))
    redes["instagram_ativo"] = bool(dados.get("instagram_ativo", False))
    redes["groq_token"] = dados.get("groq_token", "").strip()
    redes["limite_postagens"] = int(dados.get("limite_postagens", robo.LIMITE_POSTAGENS_REDES_SOCIAIS))
    salvar_json(ARQUIVO_REDES_SOCIAIS, redes)
    return jsonify({"ok": True})


# ----------------------------------------------------------------
# API - Agendamento
# ----------------------------------------------------------------
def _horarios_validos(lista):
    validos = []
    for h in lista:
        h = h.strip()
        try:
            datetime.strptime(h, "%H:%M")
            validos.append(h)
        except ValueError:
            pass
    return validos


@app.route("/api/agendamento", methods=["POST"])
@login_obrigatorio
def api_salvar_agendamento():
    dados = request.get_json(force=True)
    horarios = _horarios_validos(dados.get("horarios", []))
    salvar_json(ARQUIVO_AGENDAMENTO, {"horarios": horarios})
    return jsonify({"ok": True, "horarios": horarios})


@app.route("/api/agendamento/alternar", methods=["POST"])
@login_obrigatorio
def api_alternar_agendamento():
    agendamento = carregar_json(ARQUIVO_AGENDAMENTO, {})
    horarios = _horarios_validos(agendamento.get("horarios", []))

    if ESTADO["agendamento_ativo"]:
        ESTADO["agendamento_ativo"] = False
        return jsonify({"ok": True, "ativo": False})

    if not horarios:
        return jsonify({"erro": "Salve ao menos um horário válido (HH:MM) antes de ativar."}), 400

    ESTADO["agendamento_ativo"] = True
    return jsonify({"ok": True, "ativo": True, "horarios": horarios})


def _thread_agendador():
    """Roda em paralelo desde que o servidor esteja de pé, checando os horários."""
    import time
    while True:
        time.sleep(20)
        if not ESTADO["agendamento_ativo"] or ESTADO["robo_em_execucao"]:
            continue
        agendamento = carregar_json(ARQUIVO_AGENDAMENTO, {})
        horarios = _horarios_validos(agendamento.get("horarios", []))
        agora = datetime.now().strftime("%H:%M")
        if agora in horarios and agora != ESTADO["ultimo_minuto_executado"]:
            ESTADO["ultimo_minuto_executado"] = agora
            log(f"[AGENDAMENTO] Disparando execução automática ({agora})...")
            _iniciar_robo_thread()


# ----------------------------------------------------------------
# API - Executar / Parar / Log
# ----------------------------------------------------------------
def _iniciar_robo_thread():
    if ESTADO["robo_em_execucao"]:
        return False, "O robô já está rodando."

    config = carregar_json(ARQUIVO_CONFIG, {})
    app_id = config.get("app_id", "").strip()
    app_secret = config.get("app_secret", "").strip()
    webhooks_dict = config.get("webhooks", {})
    webhooks = [{"nome": n, "url": v.get("url", ""), "ativo": v.get("ativo", False)} for n, v in webhooks_dict.items()]
    webhooks_ativos = [w for w in webhooks if w["ativo"] and w["url"]]

    if not webhooks_ativos:
        return False, "Configure e ative ao menos um webhook na aba Configuração."
    if not app_id or not app_secret:
        return False, "Preencha App ID e App Secret na aba Configuração."

    palavras = carregar_json(ARQUIVO_PALAVRAS, [])
    links_produtos = carregar_json(ARQUIVO_LINKS_PRODUTOS, [])
    extras = carregar_json(ARQUIVO_PRODUTOS_EXTRAS, [])

    if not palavras and not links_produtos and not extras:
        return False, "Informe ao menos uma palavra-chave, um link de produto ou um item manual."

    redes = carregar_json(ARQUIVO_REDES_SOCIAIS, {})

    robo.WEBHOOKS = webhooks
    robo.SHOPEE_APP_ID = app_id
    robo.SHOPEE_APP_SECRET = app_secret
    robo.PALAVRAS_CHAVE = palavras
    robo.LIMITE_POR_PALAVRA = int(config.get("limite_por_palavra", robo.LIMITE_POR_PALAVRA))
    robo.MAX_PAGINAS_POR_PALAVRA = int(config.get("max_paginas", robo.MAX_PAGINAS_POR_PALAVRA))
    robo.PAGINACAO_COMPLETA = bool(config.get("paginacao_completa", False))
    robo.ORDENAR_POR = OPCOES_ORDENACAO.get(config.get("ordenacao", "Maior comissão (recomendado)"), 5)
    robo.LINKS_PRODUTOS_MANUAIS = links_produtos
    robo.PRODUTOS_MANUAIS_EXTRAS = extras
    robo.LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO = int(config.get("limite_envios", 0)) or None

    robo.TELEGRAM_ATIVO = redes.get("telegram_ativo", False)
    robo.TELEGRAM_BOT_TOKEN = redes.get("telegram_token", "")
    robo.TELEGRAM_DESTINOS = redes.get("telegram_destinos", [])
    robo.FACEBOOK_ATIVO = redes.get("facebook_ativo", False)
    robo.FACEBOOK_PAGINAS = redes.get("facebook_paginas", [])
    robo.INSTAGRAM_ATIVO = redes.get("instagram_ativo", False)
    robo.INSTAGRAM_CONTAS = redes.get("instagram_contas", [])
    robo.GROQ_TOKEN = redes.get("groq_token", "")
    robo.LIMITE_POSTAGENS_REDES_SOCIAIS = int(redes.get("limite_postagens", robo.LIMITE_POSTAGENS_REDES_SOCIAIS))

    robo.PARAR_EXECUCAO.clear()
    ESTADO["robo_em_execucao"] = True

    thread = threading.Thread(target=_executar_robo_em_thread, daemon=True)
    thread.start()
    return True, "Execução iniciada."


def _executar_robo_em_thread():
    saida_original = sys.stdout
    sys.stdout = RedirecionadorDeSaida()
    try:
        robo.executar_robo()
    except Exception as erro:
        print(f"[ERRO CRÍTICO] O robô parou inesperadamente: {erro}")
    finally:
        sys.stdout = saida_original
        ESTADO["robo_em_execucao"] = False


@app.route("/api/executar", methods=["POST"])
@login_obrigatorio
def api_executar():
    ok, mensagem = _iniciar_robo_thread()
    if not ok:
        return jsonify({"erro": mensagem}), 400
    return jsonify({"ok": True, "mensagem": mensagem})


@app.route("/api/parar", methods=["POST"])
@login_obrigatorio
def api_parar():
    if not ESTADO["robo_em_execucao"]:
        return jsonify({"erro": "O robô não está rodando."}), 400
    robo.PARAR_EXECUCAO.set()
    log("[PARAR] Pedido de parada enviado — o robô vai encerrar assim que terminar o produto atual.")
    return jsonify({"ok": True})


@app.route("/api/log", methods=["GET"])
@login_obrigatorio
def api_log():
    desde = int(request.args.get("desde", 0))
    with TRAVA:
        novas_linhas = ESTADO["log"][desde:]
        total = len(ESTADO["log"])
    return jsonify({
        "linhas": novas_linhas,
        "total": total,
        "executando": ESTADO["robo_em_execucao"],
    })


@app.route("/api/log/limpar", methods=["POST"])
@login_obrigatorio
def api_limpar_log():
    with TRAVA:
        ESTADO["log"] = []
    return jsonify({"ok": True})


# ----------------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=_thread_agendador, daemon=True).start()
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
