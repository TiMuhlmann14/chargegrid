# ChargeGrid Intelligence — Design System & Contexto para Build

> Este arquivo existe para dar ao Claude Code (rodando localmente) o mesmo contexto de design e dados que já foi definido em sessão anterior. Leia isto antes de gerar qualquer componente visual.

## 1. O que estamos construindo (Fase 1 do plano — Front-end First)

Dois front-ends que compartilham um sistema visual, mas têm layouts diferentes:

1. **Dashboard de Gestão** (`/dashboard-gestao`) — interno, para o operador do estacionamento. Denso em informação: os 12 carregadores, utilização em tempo real, faturamento, assistente de IA.
2. **App do Cliente** (`/app-cliente`) — para o motorista. Simples, uma tela, foco em "status da minha recarga" e "quanto vou pagar".

Ambos consomem o **mesmo contrato de dados** (ver seção 3) — hoje via arquivos JSON mockados (pasta `mocks/`), depois via API real (FastAPI, Fase 2).

## 2. Sistema de Design (tokens)

### Paleta de cores — alinhada à marca GoodWe

Cor extraída diretamente do logo oficial (pixel real: `rgb(255,0,10)` ≈ vermelho puro). Fundo branco, como na logo. Isso substitui a paleta escura anterior.

```css
--bg: #FFFFFF;            /* fundo — branco, como a logo */
--bg-soft: #F7F7F8;       /* fundo alternativo sutil, separa seções */
--surface: #FFFFFF;       /* cards */
--surface-2: #F2F2F3;     /* cards elevados / hover */
--line: #E3E3E5;          /* divisores, grades */
--ink: #17181A;           /* texto principal */
--ink-mid: #55585C;       /* texto secundário */
--ink-low: #8A8D91;       /* texto terciário / desabilitado */

--goodwe-red: #FF0000;    /* vermelho oficial GoodWe — acento primário */
--goodwe-red-deep: #D40000; /* hover, texto sobre fundo claro (mais contraste) */
--available: #00A67A;     /* verde-petróleo — "livre/disponível" (nod à herança solar da marca) */
--amber: #F5A623;         /* aviso, utilização >85% */
--critical: #8C0000;      /* crítico/ultrapassagem — SEMPRE acompanhado de ícone/texto, nunca só a cor */
```

**Por que não é tudo vermelho:** o vermelho da marca vira o acento de "energia ativa/em uso" (barramento preenchido, carregador ocupado). Para "livre/disponível" — que precisa ser visualmente oposto ao vermelho — uso um verde-petróleo (`--available`), que também remete à origem da GoodWe em energia solar, sem introduzir uma cor fora do universo da marca. Aviso e crítico ficam em tons separados do vermelho de marca, para não confundir "isso é a GoodWe" com "isso é um alerta".

### Tipografia
| Papel | Fonte | Uso |
|---|---|---|
| Display / títulos | **Space Grotesk** | Cabeçalhos, nomes de seção |
| Numérico / instrumentação | **IBM Plex Mono** | TODOS os números: kW, kWh, R$, %, tempo, IDs de carregador |
| Corpo | **Inter** | Texto descritivo, mensagens do chat |

Importar via Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
```

### Elemento-assinatura: "Diagrama Unifilar Vivo"

O motivo visual central do **dashboard** vem do próprio vocabulário da engenharia elétrica: um diagrama unifilar (one-line diagram) — um barramento tronco que se ramifica em cargas. Aqui, o barramento representa o limite contratado (100 kW), com o trilho em `--line` e o preenchimento em `--goodwe-red` proporcional à utilização, com 12 ramificações finas descendo para os nós dos carregadores (vermelho = ocupado, contorno verde `--available` = livre).

No **app do cliente**, a mesma linguagem (vermelho = energia fluindo) aparece em escala menor: um anel/círculo de progresso em vez do barramento completo — o carregador individual daquela pessoa.

### Layout — Dashboard de Gestão (wireframe)
```
┌──────────────────────────────────────────────────┐
│ ⚡ ChargeGrid — Shopping Paulista        14:32:07  │
│ [Vazio] [Chegada] [Pico] [Alívio]  ← controle demo│
├──────────────────────────────────────────────────┤
│  BARRAMENTO · 95 / 95 kW · 95%                    │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░           │
│   │ │ │ │ │ │ │ │ │ │ │ │  (ramificações)         │
│  [12 nós de carregador — grid compacta]            │
├───────────────┬───────────────┬────────────────────┤
│ Faturamento   │ Sessões ativas│ Assistente IA       │
│ R$4,26/kWh    │ (tabela)      │ (chat)              │
│ curva de lucro│               │                     │
├───────────────┴───────────────┴────────────────────┤
│ Console de eventos (últimos 5, estilo terminal)     │
└──────────────────────────────────────────────────┘
```

### Layout — App do Cliente (wireframe, mobile-first)
```
┌───────────────────┐
│ ⚡ ChargeGrid       │
│ Vaga B3 · CG-07    │
├───────────────────┤
│                    │
│    ( anel de       │
│      energia )     │
│    7.92 / 22 kW    │
│                    │
├───────────────────┤
│ Energia:  0.42 kWh │
│ Tempo:    00:12:34 │
│ Custo:    R$ 1,79  │
├───────────────────┤
│ Recarga em         │
│ andamento          │
└───────────────────┘
```

## 3. Contrato de dados (JÁ CONFIRMADO — não inventar campos novos)

Os arquivos em `mocks/` são a fonte da verdade, gerados rodando o `demand_controller.py` real:

- `01_idle.json` — estacionamento vazio
- `02_fase1_chegada_gradual.json` — 6 carros, 22 kW cada
- `03_fase2_congestionamento_maximo.json` — 12 carros, ~7,92 kW cada (momento de clímax)
- `03b_cliente_um_carregador_CG-07.json` — recorte de 1 carregador (usar no app do cliente)
- `04_fase3_alivio_final.json` — 3 restantes, 22 kW cheio
- `05_sessoes_concluidas.json` — sessões finalizadas (painel de faturamento)

Formato do estado completo (dashboard):
```
{ site, contracted_limit_kw, usable_limit_kw, total_allocated_kw,
  available_kw, utilization_pct, active_sessions,
  chargers: [{ id, location, max_kw, status: "LIVRE"|"OCUPADO",
               allocated_kw, vehicle_id, duration_min, energy_kwh }],
  events: [{ timestamp, type, source, message }] }
```

Tarifa: **R$ 4,26/kWh** (ótima), lucro projetado **R$ 230,70/dia**, curva de lucro `L(p) = -35p² + 298,5p - 406`.

## 4. O que NÃO fazer nesta fase
- Não implementar conexão Modbus real — está deferido (autorizado pelo professor).
- Não integrar gateway de pagamento real — é só visual/estimado por enquanto.
- Não inventar campos de dados fora do contrato acima.

## 5. Stack técnico e arquitetura (atualizado — produto real, não mockup estático)

**Decisão:** em vez de HTML/JS estático consumindo arquivos mock, o front-end roda como uma aplicação **Python real**, ligada diretamente ao `demand_controller.py` já existente. Isso funde o que seria "Fase 1 (front)" e "Fase 2 (back)" num único produto funcional.

- **FastAPI** — serve as duas telas e a API
- **Jinja2** — templates HTML renderizados pelo servidor (não React/Vue)
- **HTMX** — interatividade (cliques, atualizações parciais) sem escrever JavaScript manual
- **WebSocket** (`/ws`) — empurra o estado atualizado em tempo real para quem estiver com a tela aberta
- **CSS puro** — aplicando os tokens da seção 2, sem framework de CSS
- **JavaScript mínimo** — apenas para as animações do diagrama unifilar/anel de energia (SVG) e para conectar ao WebSocket; toda a lógica de negócio, dados e regras ficam em Python

### Estrutura de pastas esperada
```
chargegrid/
├── backend/
│   ├── main.py                  # app FastAPI, rotas, WebSocket
│   ├── demand_controller.py     # lógica real (já existe — importar, não reescrever)
│   ├── display.py               # mantido (modo terminal), não usado pela web
│   ├── modbus_map.py            # referência
│   ├── chatbot.py                # lógica do assistente (baseado em regras por enquanto)
│   ├── templates/
│   │   ├── dashboard_gestao.html
│   │   └── app_cliente.html
│   └── static/
│       ├── style.css
│       └── app.js
├── mocks/                        # mantido como seed/referência de cenários de demo
├── DESIGN_SYSTEM.md
└── requirements.txt
```

### Endpoints esperados
| Rota | Método | Função |
|---|---|---|
| `/` | GET | Dashboard de gestão (estado atual renderizado) |
| `/cliente/{charger_id}` | GET | App do cliente para um carregador específico |
| `/ws` | WebSocket | Broadcast do estado a cada mudança |
| `/connect` | POST | Conecta um veículo a um carregador (chama `vehicle_connect`) |
| `/disconnect` | POST | Desconecta (chama `vehicle_disconnect`) |
| `/simulate/{cenario}` | POST | Roda uma sequência real (não mock estático) reproduzindo chegada gradual / pico / alívio, usando a lógica de verdade do `demand_controller.py` |
| `/sessions` | GET | Sessões concluídas (painel de faturamento) |
| `/chat` | POST | Mensagem para o assistente IA, retorna resposta |
