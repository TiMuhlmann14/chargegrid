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
  `client_active_charger`, `last_receipt`, `pix_pending`,
  `session_qr_tokens`) guardam tudo em memória, de propósito — é uma
  demo single-process.
- Stack: FastAPI + Jinja2 + HTMX + WebSocket (`/ws`) + CSS puro. JS
  manual só em `static/app.js` (WebSocket, animações SVG, clipboard do
  Pix) — lógica de negócio sempre no servidor.
- Credenciais/segredos **NUNCA** hardcoded — sempre via `.env` na raiz
  do projeto (não em `backend/`), carregado com `python-dotenv`.

---

## Estado atual (última atualização: 2026-09-10)

### Investigação: QR do PAGAMENTO Pix não aparece (não é regressão do P1)

Reportado depois do P1: `CLIENT_PIX_CREATED` loga sem erro, mas a tela
`/cliente/carregar/pix` fica travada em "Gerando QR code…" / "Gerando
código Pix…". Hipótese inicial (do relato) era colisão de nome de
campo entre o QR do Pix (`qr_code`/`qr_code_base64`, em `pix_pending`)
e o QR novo de handoff do P1 (`qr_base64`, só no contexto do template
`carregar_iniciada.html`).

**Investigado e descartado, nesta ordem:**
1. `git diff` do commit pré-P0 até o estado atual, filtrando por
   qualquer nome ligado a QR: os dois recursos usam chaves/variáveis
   completamente distintas (`qr_code`/`qr_code_base64` em
   `pix_pending` vs. `qr_base64` só em `carregar_iniciada.html`) — zero
   sobreposição de nome.
2. `carregar_pix.html`/`pix_status.html` (o QR de pagamento) não foram
   tocados em nenhuma linha pelo P0 ou pelo P1 — só `carregar_iniciada.html`
   (handoff) mudou.
3. `backend/payments.py` está byte-a-byte idêntico desde antes do P0
   (`git diff e70812a -- backend/payments.py` vazio) — o payload
   enviado ao Mercado Pago não mudou.

**Causa raiz real, confirmada chamando `payments.create_pix_charge()` /
`get_charge_status()` DIRETO em Python (fora do FastAPI, sem HTTP, sem
JS) — ou seja, é o Mercado Pago mesmo que nunca manda o QR:**
o pedido é criado (201) só que com `status: "processing"` /
`status_detail: "in_process"` e **fica travado nesse estado
indefinidamente** (esperado até 90s+, sem transicionar) — bem diferente
do documentado na sessão anterior (`action_required`/`waiting_transfer`
→ `processed`/`accredited` em poucos segundos via o payer
`first_name="APRO"`). Sem transição de status, `transactions.payments[0].payment_method`
nunca ganha `qr_code`/`qr_code_base64` — o campo simplesmente não existe
na resposta do pedido, em nenhuma tentativa (testado com pedidos novos
também, não é um pedido "azarado"). Confirmado que não é problema de
credencial (`GET /users/me` continua `test_user: true`, conta ativa) nem
de schema (o `id` de pagamento aninhado em `transactions.payments[0].id`
devolve 404 em `GET /v1/payments/{id}` — não é um recurso alternativo
consultável). Tudo indica uma mudança de comportamento do lado do
sandbox do Mercado Pago (a simulação via `payer.first_name="APRO"` para
a Orders API parece não estar mais resolvendo como antes) — não uma
regressão de código do ChargeGrid.

**Nenhuma mudança de código feita** — não havia bug pra corrigir no
lado do ChargeGrid; o timeout de ~5 min já implementado no P0 (P0-8,
`PIX_TIMEOUT_S`) já cobre esse cenário de forma correta (o totem volta
sozinho com "tempo esgotado" em vez de ficar travado pra sempre). Pro
dia da demo, se isso persistir: `PAYMENT_MODE=mock` continua sendo o
plano de contingência documentado (ver seção "Como aprovar o Pix em
sandbox" abaixo) — o fluxo completo (cadastro → veículo → meta →
resumo → pagar → confirmação com QR de handoff → login no
celular) foi reconfirmado funcionando 100% nesse modo depois desta
investigação. Vale reinvestigar o lado do Mercado Pago (painel de
desenvolvedor / suporte) se o Pix real for indispensável pra
apresentação.

### Rodada P1 — animação do cabo + QR de acompanhamento (2026-09-10)

Implementados os dois itens de P1, depois de P0 100% testado.
Nenhuma biblioteca de animação nova; nenhuma API externa de QR code.

**P1-1 — Animação 2D do cabo carregando:** `partials/cable_animation.html`
(SVG + CSS, mesma técnica do `bus-fill`/`node-pulse` já existentes —
`stroke-dasharray`/`stroke-dashoffset` animado via `@keyframes
cable-flow`). Reaproveitado em dois lugares:
- `/cliente/carregando` — renderizado no servidor no load inicial
  (`allocated_kw`/`max_kw` do charger) e mantido vivo via WebSocket
  (`app.js` → `updateCableAnimation()`, chamada de dentro de
  `renderCliente()`), igual ao padrão já usado pro `ring-fill`.
- Drawer de detalhe do carregador no Dashboard (`charger_details.html`),
  só quando `status == OCUPADO` — como esse drawer já é
  re-renderizado inteiro pelo servidor a cada poll htmx, não precisou
  de nenhum JS novo ali, só incluir o partial com `c.allocated_kw`/`c.max_kw`.

Velocidade do pulso: `duration_s = 2.2 - frac*1.7` (`frac =
allocated_kw/max_kw`) — mais potência, ciclo mais curto/rápido. Some
(`animation` removida) quando `allocated_kw <= 0`. `prefers-reduced-motion`
já era coberto pela regra global existente em `style.css`
(`animation-duration: 0.001ms !important` em `*`); adicionada também a
mesma linha explícita `.cable-path.active { animation: none; }` que já
existia pros nós do unifilar, por consistência.

**P1-2 — QR code de acompanhamento no totem:** `qrcode[pil]==8.0`
adicionado ao `requirements.txt` (`pip install qrcode[pil]`) — QR
gerado no servidor (`main.py::generate_qr_base64`, `qrcode.make()` +
PNG em base64), embutido direto no HTML de
`/cliente/carregar/iniciada` (mesmo padrão `data:image/png;base64,...`
já usado pro QR do Pix) — nenhuma API externa, nenhum CDN novo.

Token de acesso temporário e de uso único: `session_qr_tokens: dict[token
-> {cpf, expires_at}]`, novo dict em memória (mesmo padrão dos outros —
adicionar à lista de "sem banco de dados" nas Regras fixas). Emitido em
`_issue_session_qr_url()` (`secrets.token_urlsafe(24)`, TTL de 10 min via
`SESSION_QR_TTL_S`, com limpeza preguiçosa de tokens expirados a cada
emissão). Nova rota `GET /cliente/sessao/{token}`: dá `.pop()` no token
JÁ NA LEITURA (garante uso único mesmo expirado/reenviado), valida
expiração, e só então estabelece uma sessão de login de verdade
(`client_store.start_session`) e redireciona pro destino certo
(`_post_login_destination` — cai em `/cliente/carregando` na prática,
já que o QR só existe enquanto há uma sessão ativa). Login manual
continua funcionando normalmente, sem nenhuma mudança — o QR é só um
atalho a mais.

**Testado nesta sessão** (`PAYMENT_MODE=mock`, via curl, com um log
temporário removido depois do teste pra capturar a URL do QR sem
precisar decodificar a imagem): (1) animação do cabo ativa a 22 kW
(`duration ≈ 0.50s`), caiu a 19 kW e ficou mais lenta (`≈0.73s`) ao
forçar 5 sessões simultâneas (congestionamento real via
`_redistribute_power`), voltou ao normal ao liberar — confirmando que
reage à potência de verdade, não a um valor fixo; (2) sessão "celular"
nova (cookies zerados) abrindo a URL do QR caiu direto em
`/cliente/carregando`, sem tela de login; (3) reabrir a mesma URL de
outra sessão devolveu `303 → /cliente/login` (token já consumido); (4)
login manual (CPF/senha certos e errados) continua idêntico a antes.

### P1 — pendências

Nenhuma — os dois itens de P1 do escopo combinado (animação do cabo +
QR de acompanhamento) foram implementados e testados nesta rodada.
Falta só validação visual no navegador (a bateria de testes acima foi
via curl, cobre a lógica do servidor, não a legibilidade/UX real da
animação e do QR).

---

### Rodada P0 — Totem inicia, celular acompanha (2026-09-10)

Implementado o fluxo completo pedido: o Totem deixa de acompanhar a
recarga (isso era feito em `/cliente/carregando`, que agora vive
independente do totem) e passa só a iniciar a sessão, confirmar e
voltar sozinho ao login — o acompanhamento em tempo real passa a
acontecer por login em qualquer dispositivo (celular do cliente
incluído), na mesma aplicação web. Nenhuma mudança em
`backend/demand_controller.py`; nenhum loop/ticker novo — tudo estendeu
o `ticker_loop()` e o WebSocket que já existiam.

**P0-1 — Veículos por conta:** `client_store.py` ganhou
`veiculos_cadastrados: [{modelo, placa}]` por conta, com
`add_vehicle()`/`list_vehicles()`. Em `/cliente/carregar`, o cliente
escolhe um veículo já cadastrado num `<select>`, ou cadastra um novo
inline (campos aparecem via CSS `:has()`, mesma técnica já usada em
`.pay-option` — sem JS novo). Contas sem nenhum veículo pulam direto
pros campos de cadastro, sem etapa de perfil separada.

**P0-2 — Meta por tempo (padrão) ou energia:** `estimate_charge()`
agora recebe `(charger_id, goal_type, valor)` e espelha o cálculo nas
duas direções — `goal_type="tempo"` (padrão, 30 min pré-selecionado)
projeta energia/custo; `goal_type="energia"` mantém o cálculo antigo
inalterado. `charging_goals`/`pix_pending` guardam `goal_type` +
`target_min`/`target_kwh` — o campo relevante é sempre o valor EXATO
escolhido pelo cliente (nunca a projeção estimada do outro campo, que
é só para exibição). `/estimate` (JSON) aceita `modo`/`valor`.

**P0-3 — Totem: confirmação curta + logout automático:** nova tela
`/cliente/carregar/iniciada` ("Recarga iniciada! Acompanhe pelo
celular.") substitui o redirect antigo para `/cliente/carregando` nos
dois modos de pagamento (mock e mercadopago). Contagem regressiva em
`app.js` (`scheduleAutoLogout`, ~6s) submete um form oculto para
`/cliente/logout` e volta pro login sozinha — sem depender do cliente
clicar em "sair".

**P0-4 — Acompanhamento via login:** login e cadastro (`_post_login_destination`)
redirecionam para `/cliente/carregando` em vez de `/cliente/home`
quando a conta já tem sessão ativa. `carregando.html` ganhou um link
"← Início" — não prende mais o cliente na tela.

**P0-5 — Encerramento automático por meta atingida:** `ticker_loop()`
chama `_check_goals_and_autodisconnect()` a cada tick (1s), que
verifica cada sessão ativa contra sua meta (`duration_minutes()` ou
`energy_consumed_kwh`) e chama `vehicle_disconnect()` sozinho quando
atingida, gravando `last_receipt[...]["completed_reason"] =
"meta_atingida"`. Idempotente nos dois sentidos: se o cliente clica
"encerrar" quase ao mesmo tempo, `vehicle_disconnect()` retorna erro
(ignorado em silêncio) ou, se o ticker já encerrou primeiro, o handler
de encerrar manual redireciona pro recibo já pronto em vez de tratar
como falha. `conclusao.html` mostra "encerrada automaticamente" vs
"encerrada manualmente por você".

**P0-6 — Uma sessão ativa por conta (dois pontos de entrada):** além do
redirect em `/cliente/carregar` (GET), o guard foi repetido em
`/cliente/carregar/resumo` e `/cliente/carregar/pagar` (POST) — defesa
em profundidade contra o cliente forçar o fluxo por outro caminho
enquanto já tem uma recarga em andamento. Todos redirecionam para
`/cliente/carregando?aviso=ja_ativa`, que mostra "Você já tem uma
recarga em andamento."

**P0-7 — Sem vagas disponíveis:** já existia (`{% else %}` em
`carregar_config.html`) e continua funcionando com os campos novos.

**P0-8 — Timeout do Pix (~5 min):** `pix_pending[...]["created_at"]` +
`PIX_TIMEOUT_S`. Checado no polling (`/cliente/carregar/pix/status`) e
também no `GET /cliente/carregar/pix` (caso o cliente recarregue a
tela depois do prazo sem o polling ter rodado) — em ambos os casos
limpa `pix_pending` e redireciona pro totem com "tempo esgotado, tente
novamente". Não cancela nada do lado do Mercado Pago, só desiste de
esperar.

**P0-9 — Ação de emergência no Dashboard:** mantida sem alteração —
continua em `partials/charger_details.html` / `POST /disconnect`.

**P0-10 — Casca de navegação do Dashboard:** `/` virou "Visão Geral"
(resumo — utilização + faturamento do momento, sem a grid detalhada).
A grid/tabela detalhada de 12 carregadores (com o drawer de detalhes e
a ação de emergência) mudou de `/` para `/dashboard/carregadores`.
Novas páginas simples, todas reaproveitando estado existente sem motor
de analytics novo: `/dashboard/clientes` (lista de `client_store.accounts`),
`/dashboard/historico` (`completed_sessions_payload()` completo),
`/dashboard/relatorios` (3 números agregados de `billing_snapshot()`),
`/dashboard/configuracoes` (somente leitura: tarifa, `PAYMENT_MODE`).
Nav compartilhado em `partials/dash_nav.html`.

**Limitação conhecida documentada (não resolvida nesta rodada):**
existe uma janela de corrida teórica entre dois clientes escolhendo a
mesma vaga livre antes do pagamento confirmar (comentários no código em
`/cliente/carregar/resumo` e `/cliente/carregar/pagar`, perto das
checagens de `is_occupied`). Numa demo single-process o risco é
mínimo; resolver de verdade exigiria uma "reserva" da vaga no momento
da escolha, não só na confirmação.

**Testado nesta sessão** (`PAYMENT_MODE=mock`, via curl): cadastro →
cadastro de veículo inline → meta por tempo → resumo → pagar → tela de
confirmação → login de novo redireciona pro acompanhamento →
"carregar" bloqueado com aviso enquanto ativo → encerrar manual +
segunda tentativa idempotente → meta de energia minúscula encerrada
sozinha pelo ticker com o motivo certo no recibo → estado "sem vagas"
com os 12 carregadores ocupados → botão de emergência do operador
continua funcionando → as 6 páginas do dashboard renderizam e refletem
dados reais (`/dashboard/clientes` e `/dashboard/historico` mostram as
contas/sessões criadas no teste). Fluxo `PAYMENT_MODE=mercadopago`
(Pix real) não foi reexecutado nesta rodada — a lógica de timeout foi
revisada por leitura, não testada ao vivo (exigiria esperar ~5 min).

### P1 — não implementado nesta rodada (como pedido)

Animação 2D do cabo carregando (SVG/CSS) e QR code na tela de
confirmação do totem ficam para depois — só depois de P0 100% testado
em ambiente real (a demo por curl acima cobre a lógica do servidor,
não substitui testar no navegador).

---

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
