# PagaBarato — Painel Web (versão celular)

Isso é a mesma coisa que o `gui_pagabarato.py` (Tkinter), só que roda no
navegador — inclusive no celular, de qualquer lugar.

**Como funciona:** o robô (`pagabarato_robo.py`) roda no servidor onde
você publicar este app. O celular só abre uma página pra ligar/desligar
o robô, mexer nas configurações e ver o log em tempo real.

## 1. Preparar a pasta

Coloque o **seu `pagabarato_robo.py` já existente** dentro desta mesma
pasta (`pagabarato_web/`), do lado do `app.py`. Não precisa mudar
nada nele — o painel usa exatamente as mesmas variáveis e funções que
o `gui_pagabarato.py` já usava.

Se você já tinha usado o painel Tkinter antes, pode copiar também os
arquivos `config_pagabarato.json`, `palavras_chave_pagabarato.json`,
`links_produtos_pagabarato.json`, `produtos_extras_pagabarato.json`,
`redes_sociais_pagabarato.json` e `agendamento_pagabarato.json` — o
painel web lê os mesmos arquivos, então tudo que você já cadastrou
continua valendo.

Estrutura final:
```
pagabarato_web/
├── app.py
├── pagabarato_robo.py      <- coloque o seu aqui
├── requirements.txt
├── templates/
│   ├── index.html
│   └── login.html
└── static/
    ├── style.css
    └── app.js
```

## 2. Testar no seu próprio PC primeiro

```bash
pip install -r requirements.txt
```

Defina uma senha para proteger o painel (troque `minhasenha123`):

- Windows (PowerShell): `$env:PAINEL_SENHA="minhasenha123"`
- Linux/Mac: `export PAINEL_SENHA="minhasenha123"`

Depois rode:

```bash
python app.py
```

Abra `http://localhost:5000` no navegador do PC pra conferir se está
tudo certo. Pra abrir do **celular na mesma rede Wi-Fi**, descubra o
IP do seu PC (`ipconfig` no Windows, procure "IPv4") e acesse do
celular `http://SEU-IP:5000` — sem precisar publicar na internet.

## 3. Publicar de graça na internet (pra acessar de qualquer lugar)

A forma mais simples e gratuita é o **Render.com** (plano Free):

1. Suba esta pasta pro GitHub (o mesmo jeito que você já usa nos
   outros projetos, `gui13Python`).
2. Em [render.com](https://render.com), crie um **New → Web Service**
   e aponte pro repositório.
3. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
4. Em **Environment**, adicione as variáveis:
   - `PAINEL_SENHA` = a senha que você quiser
   - `FLASK_SECRET_KEY` = qualquer texto aleatório
5. Deploy. Você recebe uma URL tipo `https://pagabarato.onrender.com`
   — abra essa URL no celular, de qualquer lugar do mundo.

**Importante sobre o plano grátis do Render:** ele "dorme" depois de
15 minutos sem acesso, e demora uns 30-50 segundos pra "acordar" na
próxima vez que você abrir. Pra controlar manualmente do celular isso
não atrapalha em nada. Só afeta o **Agendamento automático** (a aba
⏰): se o servidor estiver dormindo no horário marcado, ele não
dispara sozinho.

**Solução gratuita pro agendamento:** use um serviço de "cron" externo
e grátis, como o [cron-job.org](https://cron-job.org), pra chamar a
URL `https://SEU-APP.onrender.com/api/executar` (com a sua sessão
logada — veja abaixo) nos horários que quiser. Isso "acorda" o
servidor e já dispara a execução ao mesmo tempo, sem depender do
agendamento interno.

## 4. Instalar como "app" na tela do celular (sem loja, sem complicação)

No **Android (Chrome):** abra a URL do painel → menu (⋮) → **"Adicionar
à tela inicial"**. No **iPhone (Safari):** abra a URL → botão de
compartilhar → **"Adicionar à Tela de Início"**. Vira um ícone que abre
em tela cheia, como um app de verdade — sem precisar de loja de
aplicativos nem instalar nada.

## Segurança

O painel guarda tokens e senhas (Shopee, Telegram, Facebook, Groq)
então:
- Nunca deixe a variável `PAINEL_SENHA` com o valor padrão em produção.
- Sempre acesse por `https://` (o Render já entrega isso pronto).
- Não compartilhe a URL/senha publicamente.
