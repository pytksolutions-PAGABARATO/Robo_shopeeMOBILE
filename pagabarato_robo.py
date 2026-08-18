"""
====================================================================
ROBÔ PAGABARATO - Automação de Vitrine de Afiliados Shopee
====================================================================
Três formas de coletar produtos, que podem ser usadas juntas:
    1. BUSCA POR PALAVRA-CHAVE (PALAVRAS_CHAVE)
    2. POR LINK DE PRODUTO (LINKS_PRODUTOS_MANUAIS)
    3. MANUAL EXTRA (PRODUTOS_MANUAIS_EXTRAS)

Cada produto é enviado via HTTP POST para o(s) Webhook(s) configurados,
que grava(m) a linha na planilha do Google Sheets (publicada como CSV
e consumida pela vitrine no Vercel).

Extras:
    - LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO: protege seus créditos do
      Make.com, mandando só até X produtos novos/alterados por rodada.
    - ENVIAR_APENAS_ALTERADOS: cache local que evita reenviar produtos
      sem mudança de preço/imagem/título (e por isso também NÃO gera
      postagem em rede social pra eles — só quando o produto é novo
      ou o preço/imagem mudou).
    - Deduplicação de produtos quase-idênticos: quando o mesmo item é
      anunciado por vários vendedores, o robô mantém só o de maior
      desconto (ver remover_produtos_duplicados).
    - TELEGRAM_ATIVO / FACEBOOK_ATIVO / INSTAGRAM_ATIVO: postagem
      opcional em rede social pros produtos novos/alterados enviados
      nesta execução (com limite próprio, pra não virar spam).
      Suporta MÚLTIPLOS grupos/canais de Telegram, MÚLTIPLAS páginas
      de Facebook e MÚLTIPLAS contas de Instagram ao mesmo tempo (a
      mesma postagem vai pra todos os destinos ativos). Todo motivo
      de "não postei" aparece explicado no log — nada falha em
      silêncio. O Instagram também valida a imagem (formato JPEG,
      tamanho, proporção) e checa a cota diária de publicações (limite
      oficial de 25/dia por conta) ANTES de tentar postar, e pula
      automaticamente a conta que estourou o limite, sem travar as
      outras contas nem as outras redes.
    - GROQ_TOKEN (opcional): gera o texto da postagem com IA. Sem
      token, usa um modelo de texto padrão.
    - PARAR_EXECUCAO: um "sinal" (threading.Event) que a interface
      pode acionar para interromper a execução no meio, de forma
      limpa (termina o produto atual e para antes do próximo).

Pré-requisito: conta aprovada no Programa de Afiliados Shopee (Open
Platform), com App ID e App Secret — solicite em
https://affiliate.shopee.com.br (seção Open API / API Explorer).

Instalação de dependências:
    pip install requests beautifulsoup4 lxml Pillow
====================================================================
"""

import requests
from bs4 import BeautifulSoup
import unicodedata
import time
import random
import json
import re
import os
import hashlib
import threading
from datetime import datetime
from io import BytesIO
from PIL import Image


# ====================================================================
# 1. CONFIGURAÇÕES GLOBAIS (edite aqui, ou use a interface gráfica)
# ====================================================================

WEBHOOKS = [
    {"nome": "Google Apps Script (grátis, sem Make)", "url": "SUA_URL_AQUI", "ativo": True},
    {"nome": "Make.com", "url": "", "ativo": False},
    {"nome": "n8n", "url": "", "ativo": False},
    {"nome": "Activepieces", "url": "", "ativo": False},
]

SHOPEE_APP_ID = "SEU_APP_ID_AQUI"
SHOPEE_APP_SECRET = "SEU_APP_SECRET_AQUI"
SHOPEE_API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# --- MODO 1: busca automática por palavra-chave ---
PALAVRAS_CHAVE = [
    "fone bluetooth",
    "carregador turbo",
    "organizador de cabos",
    "luminaria led",
]

LIMITE_POR_PALAVRA = 20
MAX_PAGINAS_POR_PALAVRA = 2
PAGINACAO_COMPLETA = False
LIMITE_SEGURANCA_PAGINAS = 25
ORDENAR_POR = 5  # 1=Relevância 2=Vendidos 3=Maior preço 4=Menor preço 5=Maior comissão

# ====================================================================
# DETECÇÃO AUTOMÁTICA DE CATEGORIA
# O robô olha o TÍTULO do produto e verifica se alguma das palavras-
# chave abaixo aparece nele. A primeira categoria que bater vence.
# Isso alimenta duas coisas ao mesmo tempo:
#   1. A coluna "Categoria" da planilha (usada nos filtros do site)
#   2. O roteamento automático pras páginas de Facebook/Instagram de
#      nicho (cada página escolhe quais categorias aceita — ver
#      FACEBOOK_PAGINAS / INSTAGRAM_CONTAS mais abaixo)
# Edite/adicione categorias à vontade. Produto que não bater em nada
# cai em "Geral".
# ====================================================================
CATEGORIAS_DETECCAO = {
    "PC & Hardware": [
        "placa de video", "placa-mae", "placa mae", "ssd", "processador",
        "ryzen", "intel core", "gabinete gamer", "fonte atx", "memoria ram",
        "water cooler", "teclado mecanico", "mouse gamer", "monitor gamer",
        "placa de rede", "cooler para pc", "hd externo", "pendrive",
    ],
    "Maquiagem & Beleza": [
        "batom", "base facial", "paleta de sombra", "delineador", "rimel",
        "blush", "primer", "po compacto", "mascara de cilios", "gloss labial",
        "corretivo facial", "iluminador", "pincel de maquiagem",
    ],
    "Moda & Vestuario": [
        "camiseta", "camisa social", "calca jeans", "vestido", "jaqueta",
        "tenis", "bone", "short", "blusa feminina", "saia", "moletom",
    ],
    "Carros & Acessorios": [
        "som automotivo", "pneu", "capa de banco", "tapete automotivo",
        "farol led carro", "kit xenon", "cera automotiva", "suporte veicular",
    ],
    "Casa & Decoracao": [
        "luminaria led", "organizador de cabos", "kit de panelas",
        "jogo de cama", "cortina blackout", "tapete para sala", "difusor de aromas",
    ],
    "Eletronicos & Acessorios": [
        "fone bluetooth", "carregador turbo", "power bank", "smartwatch",
        "caixa de som bluetooth", "cabo usb", "suporte para celular",
    ],
}


def detectar_categoria(titulo: str) -> str:
    if not titulo:
        return "Geral"
    titulo_normalizado = unicodedata.normalize("NFD", titulo.lower())
    titulo_normalizado = "".join(c for c in titulo_normalizado if unicodedata.category(c) != "Mn")

    for categoria, palavras_chave in CATEGORIAS_DETECCAO.items():
        for palavra in palavras_chave:
            palavra_normalizada = unicodedata.normalize("NFD", palavra.lower())
            palavra_normalizada = "".join(c for c in palavra_normalizada if unicodedata.category(c) != "Mn")
            # \b garante palavra/frase INTEIRA — evita "bone" casar
            # dentro de "boneco", por exemplo.
            padrao = r"\b" + re.escape(palavra_normalizada) + r"\b"
            if re.search(padrao, titulo_normalizado):
                return categoria
    return "Geral"


# --- MODO 2: colar links de produtos ---
LINKS_PRODUTOS_MANUAIS = [
    # "https://shopee.com.br/produto-exemplo-i.123456.789012",
]

# --- MODO 3: produtos 100% manuais ---
PRODUTOS_MANUAIS_EXTRAS = [
    # {"id_produto": "9999", "link_afiliado": "...", "preco_original": 99.90,
    #  "preco_desconto": 59.90, "taxa_comissao": "10%"},
]

HEADERS_SCRAPING = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

INTERVALO_MINIMO = 1.5
INTERVALO_MAXIMO = 3.5
TIMEOUT_REQUISICAO = 15

PADRAO_ID_SHOPEE = re.compile(r"i\.(\d+)\.(\d+)")

# --- Cache de envios: evita gastar crédito do Make.com reenviando ---
# --- produtos que não mudaram desde a última execução.             ---
ENVIAR_APENAS_ALTERADOS = True
ARQUIVO_CACHE_ENVIOS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cache_envios_pagabarato.json"
)

# --- Limite de envios por execução: PROTEGE SEUS CRÉDITOS DO MAKE ---
LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO = 50

# --- Redes sociais (opcional) ---
TELEGRAM_ATIVO = False
TELEGRAM_BOT_TOKEN = ""
# Múltiplos grupos/canais: cada item = {"nome": "...", "chat_id": "...", "ativo": True}
TELEGRAM_DESTINOS = []

FACEBOOK_ATIVO = False
# Múltiplas páginas: cada item = {"nome": "...", "page_id": "...", "page_token": "...", "ativo": True}
FACEBOOK_PAGINAS = []

# Instagram usa a mesma API do Facebook (Graph API), mas precisa da
# conta profissional/comercial vinculada a uma Página do Facebook.
# Suporta MÚLTIPLAS contas, igual ao Facebook: cada item =
# {"nome": "...", "business_id": "...", "access_token": "...", "ativo": True}
INSTAGRAM_ATIVO = False
INSTAGRAM_CONTAS = []

# Guarda quais contas de Instagram já bateram no limite diário de
# publicações NESTA execução — evita ficar tentando postar (e
# falhando) pros produtos seguintes na mesma conta. É resetado no
# início de cada chamada de executar_robo().
_INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO = set()

# Groq (opcional) — gera o texto da postagem com IA. Token grátis em
# console.groq.com. Sem token, usa um modelo de texto padrão.
GROQ_TOKEN = ""
GROQ_MODEL = "llama-3.3-70b-versatile"

# Quantas postagens em rede social por execução (evita spam / flood).
# Lembre-se: o Instagram só aceita até 25 publicações por dia, POR
# CONTA — esse limite aqui é geral (soma Telegram + Facebook +
# Instagram por produto), a checagem de cota do Instagram é feita à
# parte, por conta, dentro de postar_instagram().
LIMITE_POSTAGENS_REDES_SOCIAIS = 5

# --- Controle de parada (acionado pela interface gráfica) ---
PARAR_EXECUCAO = threading.Event()


# ====================================================================
# 2. NÚCLEO DE COMUNICAÇÃO COM A API SHOPEE
# ====================================================================

def _gerar_assinatura_shopee(payload_str: str, timestamp: int) -> str:
    base = f"{SHOPEE_APP_ID}{timestamp}{payload_str}{SHOPEE_APP_SECRET}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _chamar_api_shopee(query: str, variables: dict) -> dict:
    payload = {"query": query, "variables": variables}
    payload_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    timestamp = int(time.time())
    assinatura = _gerar_assinatura_shopee(payload_str, timestamp)

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"SHA256 Credential={SHOPEE_APP_ID}, "
            f"Timestamp={timestamp}, Signature={assinatura}"
        ),
    }

    try:
        resposta = requests.post(
            SHOPEE_API_URL, data=payload_str.encode("utf-8"),
            headers=headers, timeout=TIMEOUT_REQUISICAO,
        )
        resposta.raise_for_status()
        corpo = resposta.json()
        if "errors" in corpo:
            print(f"[ERRO API SHOPEE] {corpo['errors']}")
            return None
        return corpo
    except requests.exceptions.RequestException as erro:
        print(f"[ERRO] Falha de conexão com a API da Shopee: {erro}")
        return None


def converter_item_api_para_produto(item: dict) -> dict:
    preco_atual = float(item.get("priceMin") or 0)
    taxa_desconto = float(item.get("priceDiscountRate") or 0)

    if taxa_desconto > 0:
        preco_original = round(preco_atual / (1 - (taxa_desconto / 100)), 2)
    else:
        preco_original = preco_atual

    return {
        "id_produto": str(item.get("itemId")),
        "link_afiliado": item.get("offerLink"),
        "link_produto_shopee": item.get("productLink"),
        "titulo_api": item.get("productName"),
        "url_imagem_api": item.get("imageUrl"),
        "preco_original": preco_original,
        "preco_desconto": round(preco_atual, 2),
        "taxa_comissao": item.get("commissionRate"),
    }


# ====================================================================
# 3. MODO 1 — BUSCA AUTOMÁTICA POR PALAVRA-CHAVE
# ====================================================================

def _extrair_lista_json(texto: str) -> list:
    """
    Tenta interpretar a resposta da IA como uma lista JSON de strings.
    Lida com casos comuns de "sujeira" na resposta (blocos ```json,
    texto antes/depois da lista) tentando extrair só o trecho [...] .
    """
    texto = texto.strip()
    texto = re.sub(r"^```(json)?", "", texto).strip()
    texto = re.sub(r"```$", "", texto).strip()

    try:
        dados = json.loads(texto)
        if isinstance(dados, list):
            return dados
    except json.JSONDecodeError:
        pass

    correspondencia = re.search(r"\[.*\]", texto, re.S)
    if correspondencia:
        try:
            dados = json.loads(correspondencia.group(0))
            if isinstance(dados, list):
                return dados
        except json.JSONDecodeError:
            pass

    raise ValueError("Não foi possível interpretar a resposta da IA como uma lista de palavras-chave.")


def gerar_palavras_chave_com_ia(categoria: str, quantidade: int = 10) -> list:
    """
    Usa o Groq para sugerir termos de busca (palavras-chave) pra uma
    categoria de produto. Retorna uma lista de strings — a pessoa
    ainda revisa/edita essa lista na tela antes de rodar a busca de
    verdade, nada é buscado automaticamente por essa função.
    """
    if not GROQ_TOKEN:
        raise ValueError("Token Groq não configurado (aba Redes Sociais).")
    if quantidade < 1:
        quantidade = 1

    prompt = (
        f"Gere exatamente {quantidade} termos de busca curtos (2 a 4 palavras cada) que uma "
        f"pessoa digitaria na Shopee para encontrar produtos em promoção da categoria "
        f"\"{categoria}\". Use termos genéricos de produto (sem marcas específicas), em "
        "português do Brasil, sem acentuação incomum, cada termo variando o tipo de produto "
        "dentro dessa categoria (não repita o mesmo produto com sinônimos). "
        "Responda APENAS com uma lista em formato JSON de strings, sem nenhum texto antes ou "
        'depois. Exemplo de formato exato esperado: ["fone bluetooth", "carregador turbo", "power bank"]'
    )

    resposta = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_TOKEN}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0.7,
        },
        timeout=30,
    )
    resposta.raise_for_status()
    texto_resposta = resposta.json()["choices"][0]["message"]["content"].strip()

    palavras_brutas = _extrair_lista_json(texto_resposta)
    palavras = [str(p).strip() for p in palavras_brutas if str(p).strip()]
    if not palavras:
        raise ValueError("A IA não retornou nenhuma palavra-chave válida.")
    return palavras[:quantidade]


def buscar_produtos_por_palavra_chave(palavra_chave: str) -> list:
    query = """
    query BuscarOfertas($keyword: String, $sortType: Int, $page: Int, $limit: Int) {
      productOfferV2(keyword: $keyword, sortType: $sortType, page: $page, limit: $limit) {
        nodes {
          itemId productName productLink offerLink imageUrl
          priceMin priceMax priceDiscountRate sales ratingStar
          commissionRate shopId shopName
        }
        pageInfo { page limit hasNextPage }
      }
    }
    """
    produtos_encontrados = []
    pagina_atual = 1
    limite_paginas = LIMITE_SEGURANCA_PAGINAS if PAGINACAO_COMPLETA else MAX_PAGINAS_POR_PALAVRA

    while pagina_atual <= limite_paginas:
        variables = {
            "keyword": palavra_chave, "sortType": ORDENAR_POR,
            "page": pagina_atual, "limit": LIMITE_POR_PALAVRA,
        }
        print(f"[API] Buscando '{palavra_chave}' — página {pagina_atual}...")
        resposta = _chamar_api_shopee(query, variables)
        if not resposta:
            break
        try:
            bloco = resposta["data"]["productOfferV2"]
            produtos_encontrados.extend(bloco.get("nodes", []))
            if not bloco.get("pageInfo", {}).get("hasNextPage", False):
                break
        except (KeyError, TypeError) as erro:
            print(f"[ERRO] Resposta inesperada da API para '{palavra_chave}': {erro}")
            break
        pagina_atual += 1
        aguardar_intervalo_humano()

    print(f"[OK] '{palavra_chave}': {len(produtos_encontrados)} produto(s) encontrado(s).")
    return produtos_encontrados


# ====================================================================
# 4. MODO 2 — COLETA A PARTIR DE UM LINK DE PRODUTO
# ====================================================================

def resolver_url_final(url: str) -> str:
    try:
        resposta = requests.get(
            url, headers=HEADERS_SCRAPING, allow_redirects=True,
            timeout=TIMEOUT_REQUISICAO,
        )
        return resposta.url
    except requests.exceptions.RequestException as erro:
        print(f"   [ERRO] Não foi possível resolver o link {url}: {erro}")
        return url


def extrair_shop_e_item_id(url: str):
    correspondencia = PADRAO_ID_SHOPEE.search(url)
    if correspondencia:
        return correspondencia.group(1), correspondencia.group(2)
    return None, None


def consultar_produto_por_id(shop_id: str, item_id: str) -> dict:
    query = """
    query BuscarProdutoPorId($itemId: Int64, $shopId: Int64) {
      productOfferV2(itemId: $itemId, shopId: $shopId) {
        nodes {
          itemId productName productLink offerLink imageUrl
          priceMin priceMax priceDiscountRate sales ratingStar
          commissionRate shopId shopName
        }
      }
    }
    """
    variables = {"itemId": int(item_id), "shopId": int(shop_id)}
    resposta = _chamar_api_shopee(query, variables)
    if not resposta:
        return None
    try:
        nodes = resposta["data"]["productOfferV2"]["nodes"]
        return nodes[0] if nodes else None
    except (KeyError, TypeError, IndexError):
        return None


def gerar_link_afiliado(url_original: str, sub_ids=None) -> str:
    mutation = """
    mutation GerarLink($input: ShortLinkInput!) {
      generateShortLink(input: $input) { shortLink }
    }
    """
    variables = {"input": {"originUrl": url_original, "subIds": sub_ids or []}}
    resposta = _chamar_api_shopee(mutation, variables)
    if not resposta:
        return None
    try:
        return resposta["data"]["generateShortLink"]["shortLink"]
    except (KeyError, TypeError):
        return None


def processar_link_de_produto(url_bruta: str) -> dict:
    print(f"[LINK] Processando: {url_bruta}")
    url_final = resolver_url_final(url_bruta)
    shop_id, item_id = extrair_shop_e_item_id(url_final)

    if shop_id and item_id:
        node = consultar_produto_por_id(shop_id, item_id)
        if node:
            produto = converter_item_api_para_produto(node)
            print(f"   [OK] Encontrado via API: {produto.get('titulo_api')}")
            return produto
        print(f"   [AVISO] API não retornou oferta para itemId={item_id}. Gerando só o link de afiliado.")
    else:
        print(f"   [AVISO] Não achei o padrão shopId/itemId nessa URL. Gerando só o link de afiliado.")

    link_afiliado = gerar_link_afiliado(url_final) or url_final
    dados_scraping = raspar_imagem_e_titulo(url_final)

    return {
        "id_produto": item_id or hashlib.md5(url_final.encode()).hexdigest()[:10],
        "link_afiliado": link_afiliado,
        "link_produto_shopee": url_final,
        "titulo_api": dados_scraping.get("titulo_limpo"),
        "url_imagem_api": dados_scraping.get("url_imagem"),
        "preco_original": 0,
        "preco_desconto": 0,
        "taxa_comissao": "",
    }


# ====================================================================
# 5. SCRAPING DE FALLBACK
# ====================================================================

def raspar_imagem_e_titulo(url_produto: str) -> dict:
    dados = {"url_imagem": None, "titulo_limpo": None}
    if not url_produto:
        return dados
    try:
        resposta = requests.get(url_produto, headers=HEADERS_SCRAPING, timeout=TIMEOUT_REQUISICAO)
        resposta.raise_for_status()
        soup = BeautifulSoup(resposta.text, "lxml")
        tag_imagem = soup.find("meta", property="og:image")
        tag_titulo = soup.find("meta", property="og:title")
        if tag_imagem and tag_imagem.get("content"):
            dados["url_imagem"] = tag_imagem["content"].strip()
        if tag_titulo and tag_titulo.get("content"):
            dados["titulo_limpo"] = tag_titulo["content"].strip()
    except requests.exceptions.RequestException as erro:
        print(f"   [ERRO] Falha no scraping de fallback ({url_produto}): {erro}")
    except Exception as erro:
        print(f"   [ERRO] Falha inesperada no scraping de fallback: {erro}")
    return dados


def aguardar_intervalo_humano():
    time.sleep(round(random.uniform(INTERVALO_MINIMO, INTERVALO_MAXIMO), 1))


# ====================================================================
# 6. CACHE DE ENVIOS
# ====================================================================

def carregar_cache_envios() -> dict:
    if os.path.exists(ARQUIVO_CACHE_ENVIOS):
        try:
            with open(ARQUIVO_CACHE_ENVIOS, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def salvar_cache_envios(cache: dict) -> None:
    try:
        with open(ARQUIVO_CACHE_ENVIOS, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError as erro:
        print(f"[ERRO] Não foi possível salvar o cache de envios: {erro}")


def calcular_hash_produto(payload: dict) -> str:
    campos_relevantes = {
        "Descricao": payload.get("Descricao"),
        "Preco_Original": payload.get("Preco_Original"),
        "Preco_Desconto": payload.get("Preco_Desconto"),
        "Link_Afiliado": payload.get("Link_Afiliado"),
        "URL_Imagem": payload.get("URL_Imagem"),
    }
    texto = json.dumps(campos_relevantes, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(texto.encode("utf-8")).hexdigest()


# ====================================================================
# REMOÇÃO DE PRODUTOS QUASE-DUPLICADOS
# Quando o mesmo produto é vendido por vários lojistas, os títulos
# ficam bem parecidos. Comparamos as 5 palavras mais relevantes do
# título (ignorando "com", "de", "para" etc.) — se dois produtos têm
# a mesma "assinatura", ficamos só com o de maior desconto.
# ====================================================================

PALAVRAS_IGNORADAS_DEDUP = {
    "de", "da", "do", "das", "dos", "com", "em", "para", "e", "a", "o",
    "as", "os", "no", "na", "por", "kit", "novo", "nova", "original",
}


def _normalizar_titulo_para_dedup(titulo: str) -> str:
    if not titulo:
        return ""
    texto = unicodedata.normalize("NFD", titulo.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    palavras = [p for p in texto.split() if p and p not in PALAVRAS_IGNORADAS_DEDUP and not p.isdigit()]
    return " ".join(sorted(palavras[:5]))


def _calcular_desconto_simples(produto: dict) -> float:
    original = produto.get("preco_original") or 0
    atual = produto.get("preco_desconto") or 0
    if not original or original <= atual:
        return 0
    return round((1 - (atual / original)) * 100, 2)


def remover_produtos_duplicados(produtos: list) -> list:
    melhor_por_assinatura = {}
    sem_assinatura = []

    for produto in produtos:
        assinatura = _normalizar_titulo_para_dedup(produto.get("titulo_api"))
        if not assinatura:
            sem_assinatura.append(produto)
            continue

        atual = melhor_por_assinatura.get(assinatura)
        if atual is None:
            melhor_por_assinatura[assinatura] = produto
            continue

        desconto_atual = _calcular_desconto_simples(atual)
        desconto_novo = _calcular_desconto_simples(produto)

        if desconto_novo > desconto_atual or (
            desconto_novo == desconto_atual and produto["preco_desconto"] < atual["preco_desconto"]
        ):
            melhor_por_assinatura[assinatura] = produto

    resultado = list(melhor_por_assinatura.values()) + sem_assinatura
    removidos = len(produtos) - len(resultado)
    if removidos > 0:
        print(f"[DEDUP] {removidos} produto(s) quase-duplicado(s) removido(s) (mesmo item, vendedores diferentes).")
    return resultado


# ====================================================================
# 7. COLETA GERAL (junta os 3 modos, sem duplicar por ID)
# ====================================================================

def coletar_links_shopee() -> list:
    if SHOPEE_APP_ID in ("", "SEU_APP_ID_AQUI") or SHOPEE_APP_SECRET in ("", "SEU_APP_SECRET_AQUI"):
        print("[ERRO CRÍTICO] SHOPEE_APP_ID / SHOPEE_APP_SECRET não configurados.")
        print("[INFO] Solicite acesso em https://affiliate.shopee.com.br")
        return list(PRODUTOS_MANUAIS_EXTRAS)

    produtos_por_id = {}

    if PALAVRAS_CHAVE:
        for palavra in PALAVRAS_CHAVE:
            if PARAR_EXECUCAO.is_set():
                print("[PARADO] Execução interrompida pelo usuário durante a busca.")
                return list(produtos_por_id.values())
            for item in buscar_produtos_por_palavra_chave(palavra):
                produto = converter_item_api_para_produto(item)
                produtos_por_id[produto["id_produto"]] = produto
            aguardar_intervalo_humano()
        print(f"[OK] {len(produtos_por_id)} produto(s) via busca por palavra-chave.")

    if LINKS_PRODUTOS_MANUAIS:
        for link in LINKS_PRODUTOS_MANUAIS:
            if PARAR_EXECUCAO.is_set():
                print("[PARADO] Execução interrompida pelo usuário durante o processamento de links.")
                return list(produtos_por_id.values())
            try:
                produto = processar_link_de_produto(link)
                produtos_por_id[produto["id_produto"]] = produto
            except Exception as erro:
                print(f"[ERRO] Falha ao processar o link {link}: {erro}")
            aguardar_intervalo_humano()

    for extra in PRODUTOS_MANUAIS_EXTRAS:
        produtos_por_id[extra["id_produto"]] = extra

    return remover_produtos_duplicados(list(produtos_por_id.values()))


# ====================================================================
# 8. ENVIO PARA O(S) WEBHOOK(S)
# ====================================================================

def _enviar_para_url(url: str, payload: dict, nome_destino: str) -> bool:
    try:
        resposta = requests.post(
            url, json=payload,
            headers={"Content-Type": "application/json"}, timeout=15,
        )
        resposta.raise_for_status()
        print(f"   [{nome_destino}] Enviado com sucesso.")
        return True
    except requests.exceptions.HTTPError as erro:
        corpo_erro = erro.response.text[:400] if erro.response is not None else ""
        print(f"   [{nome_destino}] ERRO: {erro}")
        if corpo_erro:
            print(f"   [{nome_destino}] Resposta: {corpo_erro}")
        return False
    except requests.exceptions.RequestException as erro:
        print(f"   [{nome_destino}] ERRO de conexão: {erro}")
        return False


def enviar_para_webhook(produto_final: dict) -> bool:
    webhooks_ativos = [
        w for w in WEBHOOKS
        if w.get("ativo") and w.get("url") and w["url"] != "SUA_URL_AQUI"
    ]
    if not webhooks_ativos:
        print("[ERRO CRÍTICO] Nenhum webhook ativo configurado (Make.com / n8n / Activepieces / Apps Script).")
        return False

    todos_sucesso = True
    for webhook in webhooks_ativos:
        sucesso = _enviar_para_url(webhook["url"], produto_final, webhook.get("nome", webhook["url"]))
        if not sucesso:
            todos_sucesso = False
    return todos_sucesso


# ====================================================================
# 9. REDES SOCIAIS (Telegram / Facebook / Instagram) — opcional
#    Suporta MÚLTIPLOS destinos: vários grupos/canais de Telegram,
#    várias páginas de Facebook e várias contas de Instagram ao mesmo
#    tempo. TODO motivo de "não postei" agora é explicado no log —
#    nada falha em silêncio.
# ====================================================================

def _gerar_texto_com_ia(payload: dict) -> str:
    prompt = (
        "Crie uma mensagem curta e persuasiva (máximo 4 linhas) divulgando esta "
        "oferta da Shopee. Use emojis, destaque o desconto e crie senso de urgência. "
        "NÃO inclua link (ele é adicionado depois da sua resposta).\n"
        f"Produto: {payload['Descricao']}\n"
        f"De: R$ {payload['Preco_Original']:.2f}  Por: R$ {payload['Preco_Desconto']:.2f}"
    )
    resposta = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_TOKEN}", "Content-Type": "application/json"},
        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200},
        timeout=20,
    )
    resposta.raise_for_status()
    return resposta.json()["choices"][0]["message"]["content"].strip()


def _gerar_texto_padrao(payload: dict) -> str:
    desconto_pct = 0
    if payload["Preco_Original"] > payload["Preco_Desconto"] > 0:
        desconto_pct = round((1 - payload["Preco_Desconto"] / payload["Preco_Original"]) * 100)
    linha_desconto = f"  ({desconto_pct}% OFF)" if desconto_pct > 0 else ""
    return (
        f"🔥 {payload['Descricao']}\n"
        f"💰 De R$ {payload['Preco_Original']:.2f} por R$ {payload['Preco_Desconto']:.2f}{linha_desconto}\n"
        f"👉 Corre que é por tempo limitado!"
    )


def gerar_texto_rede_social(payload: dict) -> str:
    if GROQ_TOKEN:
        try:
            return _gerar_texto_com_ia(payload)
        except Exception as erro:
            print(f"   [AVISO] Falha ao gerar texto com IA, usando modelo padrão: {erro}")
    return _gerar_texto_padrao(payload)


# ====================================================================
# HASHTAGS AUTOMÁTICAS
# Gera hashtags a partir das palavras relevantes do título do produto
# (ignorando "com", "de", "para" etc.), somadas a algumas fixas.
# ====================================================================

HASHTAGS_FIXAS = ["#Shopee", "#Promocao", "#Achadinhos", "#OfertaDoDia"]


def gerar_hashtags(titulo: str, maximo_do_titulo: int = 4) -> str:
    if not titulo:
        return " ".join(HASHTAGS_FIXAS)

    texto = unicodedata.normalize("NFD", titulo)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Za-z0-9\s]", " ", texto)

    palavras = [
        p for p in texto.split()
        if p.lower() not in PALAVRAS_IGNORADAS_DEDUP and not p.isdigit() and len(p) > 2
    ]
    hashtags_titulo = ["#" + p.capitalize() for p in palavras[:maximo_do_titulo]]

    todas = hashtags_titulo + HASHTAGS_FIXAS
    vistas = set()
    resultado = []
    for h in todas:
        if h.lower() not in vistas:
            vistas.add(h.lower())
            resultado.append(h)
    return " ".join(resultado)


def destino_aceita_categoria(destino: dict, categoria: str) -> bool:
    """
    Decide se um destino (grupo do Telegram, página do Facebook ou conta
    do Instagram) aceita a categoria do produto atual. Um destino sem a
    chave "categorias" ou com lista vazia aceita QUALQUER categoria
    (comportamento padrão, compatível com destinos já cadastrados antes
    dessa funcionalidade existir). "Tudo" na lista também libera geral.
    """
    categorias_aceitas = destino.get("categorias") or []
    if not categorias_aceitas:
        return True
    categorias_normalizadas = [c.strip().lower() for c in categorias_aceitas]
    if "tudo" in categorias_normalizadas:
        return True
    return (categoria or "").strip().lower() in categorias_normalizadas


def enviar_telegram(texto: str, categoria: str = "") -> bool:
    """
    Manda a mesma mensagem pra TODOS os destinos de Telegram ativos QUE
    ACEITAM a categoria do produto (grupos/canais). Retorna True se
    pelo menos um destino recebeu. Explica exatamente por que não
    enviou, em vez de falhar em silêncio: token vazio, sem destino
    ativo, categoria fora do filtro, ou erro específico de cada
    grupo/canal (ex: bot não é admin, chat_id errado, etc).
    """
    if not TELEGRAM_BOT_TOKEN:
        print("   [TELEGRAM] Não enviado: Token do Bot está vazio (aba Redes Sociais).")
        return False

    destinos_ativos = [d for d in TELEGRAM_DESTINOS if d.get("ativo") and d.get("chat_id")]
    if not destinos_ativos:
        print("   [TELEGRAM] Não enviado: nenhum grupo/canal ativo cadastrado (aba Redes Sociais).")
        return False

    algum_sucesso = False
    for destino in destinos_ativos:
        nome_destino = destino.get("nome", destino["chat_id"])

        if not destino_aceita_categoria(destino, categoria):
            print(f"   [TELEGRAM] Pulado em '{nome_destino}': categoria '{categoria}' fora do filtro desse destino.")
            continue

        try:
            resposta = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": destino["chat_id"], "text": texto, "parse_mode": "Markdown"},
                timeout=15,
            )
            if resposta.ok:
                print(f"   [TELEGRAM] Postado em '{nome_destino}'.")
                algum_sucesso = True
            else:
                # Mostra a resposta EXATA do Telegram — geralmente diz o
                # motivo real: "chat not found", "bot was blocked",
                # "not enough rights to send text messages", etc.
                print(f"   [TELEGRAM] Falha em '{nome_destino}' (HTTP {resposta.status_code}): {resposta.text[:300]}")
        except requests.exceptions.RequestException as erro:
            print(f"   [TELEGRAM] ERRO de conexão em '{nome_destino}': {erro}")
    return algum_sucesso




def postar_facebook(texto: str, link: str, categoria: str = "") -> bool:
    """Publica a mesma postagem em TODAS as páginas de Facebook ativas
    QUE ACEITAM a categoria do produto. Retorna True se pelo menos uma
    página recebeu a postagem."""
    paginas_ativas = [p for p in FACEBOOK_PAGINAS if p.get("ativo") and p.get("page_id") and p.get("page_token")]
    if not paginas_ativas:
        print("   [FACEBOOK] Não enviado: nenhuma página ativa com Page ID + Token cadastrados (aba Redes Sociais).")
        return False

    algum_sucesso = False
    for pagina in paginas_ativas:
        nome_pagina = pagina.get("nome", pagina["page_id"])

        if not destino_aceita_categoria(pagina, categoria):
            print(f"   [FACEBOOK] Pulado em '{nome_pagina}': categoria '{categoria}' fora do filtro dessa página.")
            continue

        try:
            url = f"https://graph.facebook.com/v19.0/{pagina['page_id']}/feed"
            resposta = requests.post(
                url, data={"message": texto, "link": link, "access_token": pagina["page_token"]},
                timeout=30,
            )
            resposta.raise_for_status()
            print(f"   [FACEBOOK] Postado em '{nome_pagina}'.")
            algum_sucesso = True
        except requests.exceptions.RequestException as erro:
            corpo = ""
            if getattr(erro, "response", None) is not None:
                corpo = erro.response.text[:300]
            print(f"   [FACEBOOK] Falha em '{nome_pagina}': {erro} {corpo}")
    return algum_sucesso


def validar_imagem_instagram(url_imagem: str) -> dict:
    """
    Baixa a imagem (só o necessário) e confere se ela atende às regras
    do Instagram ANTES de gastar uma chamada de API:
      - Content-Type precisa ser image/jpeg
      - Tamanho até 8 MB
      - Proporção (largura/altura) entre 4:5 (0.8) e 1.91:1
    Retorna {"valida": True/False, "motivo": "..."} — nunca lança exceção
    pra fora, só reporta o motivo no dict.
    """
    try:
        resposta = requests.get(url_imagem, headers=HEADERS_SCRAPING, timeout=TIMEOUT_REQUISICAO, stream=True)
        resposta.raise_for_status()

        content_type = resposta.headers.get("Content-Type", "").lower()
        if "jpeg" not in content_type and "jpg" not in content_type:
            return {"valida": False, "motivo": f"Content-Type '{content_type}' não é JPEG (Instagram só aceita JPEG)."}

        conteudo = resposta.content
        tamanho_mb = len(conteudo) / (1024 * 1024)
        if tamanho_mb > 8:
            return {"valida": False, "motivo": f"Imagem tem {tamanho_mb:.1f} MB — acima do limite de 8 MB do Instagram."}

        imagem = Image.open(BytesIO(conteudo))
        largura, altura = imagem.size
        proporcao = largura / altura

        if proporcao < 0.8:
            return {"valida": False, "motivo": f"Imagem muito estreita/alta (proporção {proporcao:.2f}) — mínimo aceito é 0.80 (4:5)."}
        if proporcao > 1.91:
            return {"valida": False, "motivo": f"Imagem muito larga (proporção {proporcao:.2f}) — máximo aceito é 1.91 (1.91:1)."}

        return {"valida": True, "motivo": "OK"}

    except requests.exceptions.RequestException as erro:
        return {"valida": False, "motivo": f"Não foi possível baixar a imagem para validar: {erro}"}
    except Exception as erro:
        return {"valida": False, "motivo": f"Falha ao ler a imagem (arquivo corrompido ou formato inválido): {erro}"}


def verificar_limite_publicacao_instagram(business_id: str, access_token: str) -> dict:
    """
    Consulta quantas publicações uma conta específica do Instagram
    ainda permite nas últimas 24h (limite oficial: 25 publicações por
    dia, por conta). Retorna
    {"disponivel": True/False, "usado": X, "total": Y, "motivo": "..."}.
    Se a consulta falhar, assume disponível (deixa o robô tentar
    normalmente em vez de travar por causa da checagem em si).
    """
    if not business_id or not access_token:
        return {"disponivel": False, "motivo": "Business Account ID ou Access Token vazios."}

    try:
        resposta = requests.get(
            f"https://graph.facebook.com/v19.0/{business_id}/content_publishing_limit",
            params={"fields": "config,quota_usage", "access_token": access_token},
            timeout=15,
        )
        resposta.raise_for_status()
        dados = resposta.json().get("data", [])
        if not dados:
            return {"disponivel": True, "motivo": "API não retornou dados de cota — seguindo normalmente."}

        info = dados[0]
        usado = info.get("quota_usage", 0)
        total = info.get("config", {}).get("quota_total", 25)

        if usado >= total:
            return {
                "disponivel": False, "usado": usado, "total": total,
                "motivo": f"Limite diário atingido ({usado}/{total} publicações nas últimas 24h).",
            }
        return {"disponivel": True, "usado": usado, "total": total, "motivo": "OK"}

    except requests.exceptions.RequestException as erro:
        return {"disponivel": True, "motivo": f"Não foi possível checar a cota (seguindo mesmo assim): {erro}"}


def postar_instagram(texto: str, url_imagem: str, categoria: str = "") -> bool:
    """
    Publica a mesma imagem/legenda em TODAS as contas de Instagram
    ativas QUE ACEITAM a categoria do produto (igual postar_facebook,
    mas com validação de imagem e checagem de cota diária por conta).
    Retorna True se pelo menos uma conta recebeu a postagem. Contas
    que baterem no limite diário são puladas automaticamente, sem
    travar as demais.
    """
    global _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO

    contas_ativas = [c for c in INSTAGRAM_CONTAS if c.get("ativo") and c.get("business_id") and c.get("access_token")]
    if not contas_ativas:
        print("   [INSTAGRAM] Não enviado: nenhuma conta ativa com Business ID + Token cadastrados (aba Redes Sociais).")
        return False

    if not url_imagem:
        print("   [INSTAGRAM] Não enviado: produto sem imagem (Instagram exige mídia).")
        return False

    validacao = validar_imagem_instagram(url_imagem)
    if not validacao["valida"]:
        print(f"   [INSTAGRAM] Não enviado: {validacao['motivo']}")
        return False

    algum_sucesso = False
    for conta in contas_ativas:
        nome_conta = conta.get("nome", conta["business_id"])
        business_id = conta["business_id"]
        access_token = conta["access_token"]

        if not destino_aceita_categoria(conta, categoria):
            print(f"   [INSTAGRAM] Pulado em '{nome_conta}': categoria '{categoria}' fora do filtro dessa conta.")
            continue

        if business_id in _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO:
            print(f"   [INSTAGRAM] Pulado em '{nome_conta}': limite diário já atingido nesta execução.")
            continue

        checagem = verificar_limite_publicacao_instagram(business_id, access_token)
        if not checagem["disponivel"]:
            print(f"   [INSTAGRAM] Pulado em '{nome_conta}': {checagem['motivo']}")
            _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO.add(business_id)
            continue
        if "usado" in checagem:
            print(f"   [INSTAGRAM] '{nome_conta}' — cota do dia: {checagem['usado']}/{checagem['total']} publicações usadas.")

        try:
            url_container = f"https://graph.facebook.com/v19.0/{business_id}/media"
            resposta_container = requests.post(
                url_container,
                data={"image_url": url_imagem, "caption": texto, "access_token": access_token},
                timeout=30,
            )
            resposta_container.raise_for_status()
            creation_id = resposta_container.json().get("id")
            if not creation_id:
                print(f"   [INSTAGRAM] '{nome_conta}': API não retornou o ID do container de mídia.")
                continue

            url_publicar = f"https://graph.facebook.com/v19.0/{business_id}/media_publish"
            resposta_publicar = requests.post(
                url_publicar,
                data={"creation_id": creation_id, "access_token": access_token},
                timeout=30,
            )
            resposta_publicar.raise_for_status()
            print(f"   [INSTAGRAM] Postado em '{nome_conta}'.")
            algum_sucesso = True

        except requests.exceptions.RequestException as erro:
            corpo = ""
            subcodigo = None
            if getattr(erro, "response", None) is not None:
                corpo = erro.response.text[:300]
                try:
                    subcodigo = erro.response.json().get("error", {}).get("error_subcode")
                except ValueError:
                    pass

            # Subcode 2207042 = limite diário de publicações do Instagram.
            if subcodigo == 2207042:
                print(f"   [INSTAGRAM] Limite diário atingido em '{nome_conta}' — pulando essa conta pelo resto da execução.")
                _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO.add(business_id)
            else:
                print(f"   [INSTAGRAM] Falha em '{nome_conta}': {erro} {corpo}")

    return algum_sucesso


# ====================================================================
# 10. ORQUESTRAÇÃO PRINCIPAL
# ====================================================================

def montar_payload_final(produto: dict) -> dict:
    titulo = produto.get("titulo_api")
    imagem = produto.get("url_imagem_api")

    if not titulo or not imagem:
        url_para_scraping = produto.get("link_produto_shopee") or produto.get("link_afiliado")
        print(f"   [FALLBACK] Imagem/título ausente — tentando scraping em: {url_para_scraping}")
        dados_scraping = raspar_imagem_e_titulo(url_para_scraping)
        titulo = titulo or dados_scraping.get("titulo_limpo")
        imagem = imagem or dados_scraping.get("url_imagem")

    titulo_final = titulo or f"Produto {produto['id_produto']}"

    return {
        "ID_Produto": produto["id_produto"],
        "Descricao": titulo_final,
        "Preco_Original": produto["preco_original"],
        "Preco_Desconto": produto["preco_desconto"],
        "Taxa_Comissao": produto.get("taxa_comissao", ""),
        "Link_Afiliado": produto["link_afiliado"],
        "URL_Imagem": imagem or "",
        "Categoria": detectar_categoria(titulo_final),
        "Data_Atualizacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def executar_robo():
    global _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO

    PARAR_EXECUCAO.clear()
    _INSTAGRAM_CONTAS_COM_LIMITE_ATINGIDO = set()

    print("=" * 70)
    print("ROBÔ PAGABARATO — Iniciando execução")
    print(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    if PAGINACAO_COMPLETA:
        print("Paginação: COMPLETA (buscando todas as páginas disponíveis)")
    webhooks_ativos_nomes = [w["nome"] for w in WEBHOOKS if w.get("ativo") and w.get("url") and w["url"] != "SUA_URL_AQUI"]
    if webhooks_ativos_nomes:
        print(f"Webhooks ativos: {', '.join(webhooks_ativos_nomes)}")
    else:
        print("[AVISO] Nenhum webhook ativo configurado!")
    if LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO:
        print(f"Limite de envios por execução: {LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO} (protege seus créditos)")

    # --- Diagnóstico de redes sociais: sempre mostra o status real, ---
    # --- mesmo quando está "ativo" mas mal configurado.              ---
    destinos_tg_ativos = [d for d in TELEGRAM_DESTINOS if d.get("ativo") and d.get("chat_id")]
    paginas_fb_ativas = [p for p in FACEBOOK_PAGINAS if p.get("ativo") and p.get("page_id") and p.get("page_token")]
    contas_ig_ativas = [c for c in INSTAGRAM_CONTAS if c.get("ativo") and c.get("business_id") and c.get("access_token")]

    if TELEGRAM_ATIVO:
        if not TELEGRAM_BOT_TOKEN:
            print("[AVISO] Telegram está ATIVO mas o Token do Bot não foi preenchido — nenhuma postagem vai sair.")
        elif not destinos_tg_ativos:
            print("[AVISO] Telegram está ATIVO mas não há nenhum grupo/canal ativo cadastrado — nenhuma postagem vai sair.")
        else:
            print(f"Telegram ativo: {len(destinos_tg_ativos)} destino(s) — {', '.join(d.get('nome', d['chat_id']) for d in destinos_tg_ativos)}")
    else:
        print("Telegram: desativado.")

    if FACEBOOK_ATIVO:
        if not paginas_fb_ativas:
            print("[AVISO] Facebook está ATIVO mas não há nenhuma página ativa com Page ID + Token — nenhuma postagem vai sair.")
        else:
            print(f"Facebook ativo: {len(paginas_fb_ativas)} página(s) — {', '.join(p.get('nome', p['page_id']) for p in paginas_fb_ativas)}")
    else:
        print("Facebook: desativado.")

    if INSTAGRAM_ATIVO:
        if not contas_ig_ativas:
            print("[AVISO] Instagram está ATIVO mas não há nenhuma conta ativa com Business ID + Token — nenhuma postagem vai sair.")
        else:
            print(f"Instagram ativo: {len(contas_ig_ativas)} conta(s) — {', '.join(c.get('nome', c['business_id']) for c in contas_ig_ativas)}")
    else:
        print("Instagram: desativado.")

    if TELEGRAM_ATIVO or FACEBOOK_ATIVO or INSTAGRAM_ATIVO:
        print(f"Limite de postagens em redes sociais por execução: {LIMITE_POSTAGENS_REDES_SOCIAIS}")
        if LIMITE_POSTAGENS_REDES_SOCIAIS <= 0:
            print("[AVISO] Esse limite está em 0 — NENHUMA postagem em rede social vai sair até você aumentar (aba Redes Sociais).")
    print("=" * 70)

    produtos = coletar_links_shopee()
    if not produtos:
        print("[AVISO] Nenhum produto encontrado. Verifique credenciais, palavras-chave e links.")
        return

    cache_envios = carregar_cache_envios() if ENVIAR_APENAS_ALTERADOS else {}

    total = len(produtos)
    sucesso_count = 0
    falha_count = 0
    pulados_count = 0
    postagens_redes_count = 0
    parou_por_limite = False
    parou_pelo_usuario = False

    for indice, produto in enumerate(produtos, start=1):
        if PARAR_EXECUCAO.is_set():
            print(f"\n[PARADO] Execução interrompida pelo usuário em {indice-1}/{total} produtos processados.")
            parou_pelo_usuario = True
            break

        print("\n" + "-" * 70)
        print(f"[{indice}/{total}] Processando produto ID: {produto['id_produto']}")

        payload_final = montar_payload_final(produto)

        # Se não achou preço NEM imagem, o link não foi resolvido de verdade
        # (comum em links curtos s.shopee.com.br que a Shopee bloqueia no
        # scraping). Em vez de sujar a planilha com "Produto XXXX" a R$0,00,
        # pula esse produto e avisa o motivo no log.
        sem_preco = payload_final["Preco_Desconto"] <= 0 and payload_final["Preco_Original"] <= 0
        sem_imagem = not payload_final["URL_Imagem"]
        if sem_preco and sem_imagem:
            print(f"   [PULADO] Link não resolvido — sem preço e sem imagem. Não enviado à planilha.")
            print(f"   [DICA] Se possível, use o link COMPLETO do produto (com i.NUMERO.NUMERO na URL)")
            print(f"          em vez do link curto s.shopee.com.br/... — resolve direto pela API.")
            pulados_count += 1
            continue

        hash_atual = calcular_hash_produto(payload_final)

        if ENVIAR_APENAS_ALTERADOS and cache_envios.get(produto["id_produto"]) == hash_atual:
            print("   [PULADO] Sem mudanças desde o último envio — não gasta crédito, e também NÃO gera postagem em rede social.")
            pulados_count += 1
            continue

        if LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO and sucesso_count >= LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO:
            restantes = total - indice + 1
            print(f"\n[LIMITE] Atingido o limite de {LIMITE_MAXIMO_ENVIOS_POR_EXECUCAO} envios desta execução.")
            print(f"[LIMITE] {restantes} produto(s) restante(s) ficam para a próxima execução.")
            parou_por_limite = True
            break

        print(f"   Título   : {payload_final['Descricao']}")
        print(f"   De R$ {payload_final['Preco_Original']:.2f}  por  R$ {payload_final['Preco_Desconto']:.2f}")
        print(f"   Imagem   : {'OK' if payload_final['URL_Imagem'] else 'NÃO ENCONTRADA'}")

        if enviar_para_webhook(payload_final):
            print("   [SUCESSO] Enviado ao Google Sheets.")
            sucesso_count += 1
            cache_envios[produto["id_produto"]] = hash_atual

            tem_rede_configurada = TELEGRAM_ATIVO or FACEBOOK_ATIVO or INSTAGRAM_ATIVO
            if tem_rede_configurada:
                if postagens_redes_count >= LIMITE_POSTAGENS_REDES_SOCIAIS:
                    print(f"   [REDES SOCIAIS] Pulado: limite de {LIMITE_POSTAGENS_REDES_SOCIAIS} postagem(ns) por execução já atingido.")
                else:
                    texto = gerar_texto_rede_social(payload_final)
                    hashtags = gerar_hashtags(payload_final["Descricao"])
                    texto_com_hashtags = f"{texto}\n\n{hashtags}"
                    link = payload_final["Link_Afiliado"]
                    imagem = payload_final["URL_Imagem"]
                    categoria = payload_final.get("Categoria", "Geral")
                    print(f"   Categoria : {categoria}")

                    if TELEGRAM_ATIVO:
                        enviar_telegram(f"{texto_com_hashtags}\n\n🛒 {link}", categoria)

                    if FACEBOOK_ATIVO:
                        postar_facebook(texto_com_hashtags, link, categoria)

                    if INSTAGRAM_ATIVO:
                        texto_instagram = f"{texto_com_hashtags}\n\n🛒 Link na bio ou stories!"
                        postar_instagram(texto_instagram, imagem, categoria)

                    postagens_redes_count += 1
        else:
            print("   [FALHA] Não foi enviado.")
            falha_count += 1

        if indice < total:
            aguardar_intervalo_humano()

    if ENVIAR_APENAS_ALTERADOS:
        salvar_cache_envios(cache_envios)

    print("\n" + "=" * 70)
    print("RESUMO DA EXECUÇÃO")
    print(f"   Total de produtos       : {total}")
    print(f"   Enviados com sucesso    : {sucesso_count}")
    print(f"   Pulados (sem mudança)   : {pulados_count}")
    print(f"   Falhas                  : {falha_count}")
    if TELEGRAM_ATIVO or FACEBOOK_ATIVO or INSTAGRAM_ATIVO:
        print(f"   Postagens em redes soc. : {postagens_redes_count}")
    if parou_por_limite:
        print("   [!] Execução parou por causa do limite de envios — rode de novo mais tarde ou aumente o limite.")
    if parou_pelo_usuario:
        print("   [!] Execução interrompida manualmente pelo usuário.")
    print("=" * 70)


if __name__ == "__main__":
    executar_robo()