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

- **Dashboard de Gestão** (operador): http://localhost:8000/ — só observa e reflete
  o que acontece; não inicia sessões.
- **App do Cliente** (motorista): http://localhost:8000/cliente/login — cadastro/login
  por CPF, depois "Carregar veículo" para escolher vaga livre, energia desejada,
  pagar (mock) e acompanhar a recarga.

Use `scripts/seed_demo.py` (com o servidor rodando) para popular várias vagas
ocupadas de uma vez antes de gravar uma demo, sem repetir o fluxo do cliente
manualmente — não é uma feature do produto, é só uma ferramenta de bastidor.

## O que testar

- Faça login/cadastro em `/cliente/login`, escolha "Carregar veículo", selecione
  uma vaga livre e uma energia desejada, veja a estimativa, pague (mock) — a
  recarga começa de verdade (`vehicle_connect` real) e você é levado pra tela de
  acompanhamento.
- Com o Dashboard aberto em outra aba, confirme que a vaga aparece ocupada
  automaticamente via WebSocket, sem precisar mexer em nada no Dashboard.
- Clique num carregador ocupado no Dashboard para ver detalhes e, se precisar,
  forçar o encerramento da sessão como ação operacional de emergência.
- No app do cliente, clique em "Encerrar recarga e pagar" e confira a tela de
  conclusão com os totais.
- Pergunte algo ao Assistente IA no dashboard, ex.: "quantos livres?", "faturamento
  de hoje", "status do CG-03".

## Estrutura

```
backend/
├── main.py                  # app FastAPI, rotas, WebSocket, ticker de energia
├── demand_controller.py     # lógica real de negócio (fornecida, não reescrita)
├── chatbot.py                # assistente por regras
├── client_store.py           # contas de cliente (login/cadastro), em memória
├── templates/
│   ├── dashboard_gestao.html
│   ├── cliente/               # login, cadastro, home, fluxo de recarga
│   └── partials/
├── static/
│   ├── style.css             # tokens do DESIGN_SYSTEM.md
│   └── app.js                # WebSocket + animações SVG (único JS manual)
scripts/
└── seed_demo.py               # ferramenta de bastidor — popula vagas antes da demo
mocks/                        # cenários de referência (não servidos como API)
DESIGN_SYSTEM.md
requirements.txt
```
