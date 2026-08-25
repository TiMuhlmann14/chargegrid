# ChargeGrid Intelligence

Sistema de gestão de energia para uma rede de 12 carregadores de carro elétrico
(GoodWe × FIAP EV Challenge 2026). FastAPI + Jinja2 + HTMX + WebSocket + CSS puro
— sem React/Vue/Node. A lógica de negócio real está em `backend/demand_controller.py`
e é usada de verdade pelas duas telas (não há dados mockados servidos como resposta
estática; os arquivos em `mocks/` ficam só como referência/roteiro de cenários).

## Como rodar

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

pip install -r requirements.txt

cd backend
uvicorn main:app --reload --port 8000
```

Depois abra:

- **Dashboard de Gestão** (operador): http://localhost:8000/
- **App do Cliente** (motorista), para qualquer um dos 12 carregadores: http://localhost:8000/cliente/CG-07

## O que testar

- Botões **Chegada gradual / Pico (12 carros) / Alívio / Vazio** no topo do dashboard
  disparam sequências reais no `DemandController` (conectam/desconectam veículos um a
  um, com redistribuição de potência de verdade) — acompanhe o diagrama unifilar, a
  tabela e o console de eventos mudando ao vivo.
- Clique em qualquer carregador (nó do diagrama ou linha da tabela) para expandir os
  detalhes da sessão, com opção de conectar/desconectar manualmente.
- Abra `/cliente/CG-0X` numa segunda aba (ou no celular, mesma rede) enquanto mexe no
  dashboard — kWh, tempo e custo do carregador escolhido atualizam sozinhos via
  WebSocket, sem recarregar a página.
- Pergunte algo ao Assistente IA no dashboard, ex.: "quantos livres?", "faturamento
  de hoje", "status do CG-03".

## Estrutura

```
backend/
├── main.py                  # app FastAPI, rotas, WebSocket, ticker de energia
├── demand_controller.py     # lógica real de negócio (fornecida, não reescrita)
├── chatbot.py                # assistente por regras
├── templates/
│   ├── dashboard_gestao.html
│   ├── app_cliente.html
│   └── partials/
├── static/
│   ├── style.css             # tokens do DESIGN_SYSTEM.md
│   └── app.js                # WebSocket + animações SVG (único JS manual)
mocks/                        # cenários de referência (não servidos como API)
DESIGN_SYSTEM.md
requirements.txt
```
