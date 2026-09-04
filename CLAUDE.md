# ChargeGrid Intelligence — CLAUDE.md

> Contexto para sessões do Claude Code neste projeto. Complementa
> `DESIGN_SYSTEM.md` (tokens visuais, contrato de dados) e `README.md`
> (como rodar, o que testar). Releia este arquivo por completo antes de
> mexer no código — principalmente a seção "Estado atual" abaixo, que é
> atualizada a cada rodada de trabalho para não perder contexto entre
> sessões.

## Regras fixas do projeto

- `backend/demand_controller.py` é **INTOCÁVEL** — lógica de negócio
  fornecida, só é importada/chamada, nunca reescrita.
- Sem banco de dados: `client_store.py` (contas), e os dicts em
  `main.py` (`payment_status`, `sim_flags`, `charging_goals`,
  `client_active_charger`, `last_receipt`, `pix_pending`) guardam tudo
  em memória, de propósito — é uma demo single-process.
- Stack: FastAPI + Jinja2 + HTMX + WebSocket (`/ws`) + CSS puro. JS
  manual só em `static/app.js` (WebSocket, animações SVG, clipboard do
  Pix) — lógica de negócio sempre no servidor.
- Credenciais/segredos **NUNCA** hardcoded — sempre via `.env` na raiz
  do projeto (não em `backend/`), carregado com `python-dotenv`.

---

## Estado atual (última atualização: 2026-09-04)

### Credenciais de teste — Mercado Pago (sandbox)

O `.env` na raiz do projeto já tem:

```
MP_ACCESS_TOKEN=APP_USR-2473341312052785-090318-28790e4cb85987426ac0741393800343-3664275756
MP_PUBLIC_KEY=APP_USR-acea648e-0eef-4f94-8ae0-561539f7b848
PAYMENT_MODE=mercadopago
```

Confirmado via `GET /users/me` (autenticado com esse Access Token) que a
conta associada é uma conta de **teste de verdade**:
`nickname: "TESTUSER4547016381362252808"`, `"test_data": {"test_user":
true}`. Ou seja: **o prefixo `APP_USR-` está correto para credencial de
teste** neste modelo atual do Mercado Pago — não é sinal de credencial
de produção por si só (isso foi verificado ao vivo nesta sessão depois
de uma suspeita inicial errada baseada só no formato do prefixo; ver
"Investigação" abaixo).

Conta de teste **COMPRADORA** (criada pelo painel oficial — Contas de
teste), guardada aqui só para referência, mas **não é usada por nenhum
fluxo do código**, ver "Como aprovar o Pix em sandbox" abaixo:

```
Usuário: TESTUSER4011597490322683505
Senha:   yzJHC6LmvE
```

`.env` está no `.gitignore` (confirmado com `git check-ignore -v .env`).

### Pagamento via Mercado Pago Pix (sandbox) — implementado

Substitui a aprovação instantânea mockada no fluxo do cliente
(`/cliente/carregar/pagar`) por uma cobrança Pix real via Mercado Pago,
controlada pela variável `PAYMENT_MODE`:

- **`mercadopago`** (padrão): cria uma cobrança Pix real via
  `backend/payments.py`, mostra QR + copia-e-cola, confirma por
  polling (nunca webhook) e só então chama `vehicle_connect()`.
- **`mock`**: caminho antigo, inalterado — aprovação instantânea, zero
  chamada externa. Modo de contingência para a demo ao vivo: no dia,
  se a rede da sala falhar, troca só `PAYMENT_MODE=mock` no `.env` e
  reinicia o servidor (`uvicorn`, não `--reload` sozinho — variável de
  ambiente só é lida na inicialização do processo).

**Arquivo novo:** `backend/payments.py` — encapsula todo o SDK do
Mercado Pago (`pip install mercadopago==3.5.0`, já em
`requirements.txt`, junto com `python-dotenv==1.2.3`). `main.py` só
chama `payments.create_pix_charge()` / `payments.get_charge_status()` /
lê `payments.PAYMENT_MODE` — nenhuma outra rota fala com o SDK direto.

**Rotas novas em `main.py`:**
- `GET /cliente/carregar/pix` — tela com QR code, copia-e-cola e botão
  "Já paguei" (opcional — só antecipa a checagem, não confirma nada
  sozinho).
- `GET /cliente/carregar/pix/status` — alvo do polling HTMX (`hx-trigger:
  load, every 3s`, ~3s), consulta `payments.get_charge_status()`. Se
  aprovado: chama `vehicle_connect()` de verdade e responde com header
  `HX-Redirect: /cliente/carregando`. Se rejeitado: `HX-Redirect:
  /cliente/carregar?pix_erro=1`. Se ainda pendente: devolve o partial
  `partials/pix_status.html`.

**Estado novo em memória:** `pix_pending: dict[cpf, dict]` (fora do
`DemandController`, mesmo padrão dos outros dicts de demo) — guarda a
cobrança pendente entre a criação do Pix e a confirmação.

#### DECISÃO DE API — Orders API (`/v1/orders`), não a Payments API clássica

A doc oficial (`developers.mercadopago.com.br/pt/docs/checkout-api-orders/
integration-test/pix`) recomenda a **Orders API** para testar Pix — não
a Payments API clássica (`/v1/payments`) que era o plano original.
Confirmado ao vivo nesta sessão: `sdk.payment().create()` devolveu 401
("Unauthorized use of live credentials", causado pelo e-mail de teste
inventado, não por credencial de produção); `sdk.order().create()` com
o mesmo token funcionou de primeira (201) e devolveu QR + copia-e-cola
normalmente. Por isso `payments.py` usa `sdk.order()`.

#### Como o Pix é "aprovado" em sandbox — SEM login da conta compradora

O Pix de sandbox não está conectado à rede Pix real — não existe QR
escaneável nem um jeito de aprovar manualmente pelo app de um banco. O
Mercado Pago documenta nomes de pagador especiais que simulam o
resultado quando usados em `payer.first_name`; `"APRO"` = aprovado.
Confirmado ao vivo: um pedido criado com `payer.first_name="APRO"`
evolui sozinho de `action_required`/`waiting_transfer` para
`processed`/`accredited` em poucos segundos, sem nenhuma ação manual.

Esse valor **só é usado no payload enviado à API do Mercado Pago**
(dentro de `payments.create_pix_charge`, ver comentário no código) —
nunca aparece em nenhuma tela do ChargeGrid. Resumo da recarga, tela de
carregamento, log de eventos e recibo sempre mostram o nome/placa reais
da conta logada (confirmado via teste automatizado: `grep "APRO"` em
todo HTML servido durante o fluxo não encontra nada).

Consequência prática: a conta de teste **COMPRADORA** listada acima
**não precisa logar em lugar nenhum** para testar o fluxo — o Pix
aprova sozinho em ~3-10s via polling. Ela fica documentada aqui só
para o caso de precisar simular o fluxo hospedado (Checkout Pro), que
não é o que este projeto usa.

#### Investigação desta sessão (para não repetir o mesmo erro)

1ª tentativa: chamei `sdk.payment().create()` (API clássica) com um
e-mail de pagador inventado (`...@testuser.com`) → 401 "Unauthorized
use of live credentials". Cheguei a suspeitar que o token fosse de
produção. `GET /users/me` provou o contrário (`test_user: true`). A
causa real era outra: API errada + e-mail de teste mal formado. Trocar
para `sdk.order().create()` (Orders API) resolveu de primeira. Lição:
não confiar no formato do prefixo do token (`APP_USR-` vs `TEST-`)
como diagnóstico sozinho — confirmar via `GET /users/me`.

### Testes realizados nesta sessão (ambos os modos, fluxo completo)

- **`PAYMENT_MODE=mock`**: cadastro → resumo → pagar → redireciona pra
  `/cliente/carregando` direto (sem tela de Pix) → dashboard reflete
  vaga ocupada via WebSocket → encerrar recarga → recibo. Idêntico ao
  comportamento anterior.
- **`PAYMENT_MODE=mercadopago`**: cadastro → resumo (sem opção
  "Cartão salvo", só Pix) → pagar → cria cobrança real via
  `sdk.order().create()` → tela `/cliente/carregar/pix` com QR +
  copia-e-cola reais → polling em `/cliente/carregar/pix/status`
  aprova sozinho (~3-10s) → `HX-Redirect` pra `/cliente/carregando` →
  `vehicle_connect()` chamado de verdade → dashboard reflete a sessão
  → encerrar → recibo. Nome/placa reais em todas as telas; log de
  eventos mostra `CLIENT_PIX_CREATED` e `CLIENT_PAYMENT_APPROVED` com o
  nome real do cliente.

### Correção pós-entrega: QR aparecendo como "None" na tela

Depois da primeira entrega, houve um relato de o QR não aparecer e o
campo copia-e-cola mostrar literalmente o texto `None`. Investigado com
log temporário da resposta crua de `sdk.order().create()`: rodei o
fluxo completo de novo e mais 6 criações seguidas direto na API — em
7/7 tentativas o QR veio pronto já na criação, sem reproduzir o
problema. Ou seja, não achei um bug de "campo errado" no código (o
caminho `transactions.payments[0].payment_method.qr_code` está correto
e confirmado repetidas vezes).

Mesmo sem reproduzir, blindei `payments.py`/`main.py`/os templates
contra as duas hipóteses da tarefa, porque são robustez válida de
qualquer forma:
- `create_pix_charge()` agora faz um `GET` de retry imediato se o QR
  vier ausente na resposta de criação.
- `get_charge_status()` (usado pelo polling) agora também devolve
  `qr_code`/`qr_code_base64` — se ainda estiverem faltando, o polling
  que já roda a cada ~3s completa sozinho via um `hx-swap-oob` que
  atualiza só a imagem/o campo, sem precisar recarregar a página.
- Os templates nunca mais interpolam `None` cru: se o campo ainda não
  chegou, mostram "Gerando QR code…" / "Gerando código Pix…" em vez do
  valor Python bruto. Testado renderizando os templates diretamente com
  `qr_code=None` — confirma ausência de `None`/`>None<` no HTML.

Se o "None" voltar a aparecer, o próximo passo é capturar o
`external_reference` daquela tentativa específica e consultar
`GET /v1/orders/{id}` na hora — pode ser algo específico da conta/rede
naquele momento, não reproduzido aqui.

### O que NÃO foi implementado (fora de escopo combinado)

- Webhook (`notification_url`) — explicitamente descartado, localhost
  não recebe sem infraestrutura extra (ngrok).
  - Recomendado o botão "Já paguei" existir "opcional", mas a confirmação
    real vem sempre do polling.
- Estorno automático se o Pix aprovar mas a vaga ficar ocupada
  nesse meio-tempo — só loga um evento `CLIENT_PIX_APPROVED_BUT_
  CHARGER_TAKEN` pro operador tratar manualmente.
- Caminho de pagamento com cartão no modo `mercadopago` — só Pix, como
  pedido; a opção "Cartão salvo" fica escondida na tela de resumo
  quando `PAYMENT_MODE=mercadopago`.
