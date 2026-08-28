"""
ChargeGrid Intelligence — FastAPI app
--------------------------------------
Serve o Dashboard de Gestão e o App do Cliente, ambos ligados ao mesmo
DemandController real (não mockado) e sincronizados em tempo real via
WebSocket. HTMX cuida das interações de clique/formulário; o JS próprio
(static/app.js) cuida só da conexão WebSocket e das animações SVG do
diagrama unifilar / anel de energia — como definido no DESIGN_SYSTEM.md.
"""

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from demand_controller import DemandController
import chatbot
import client_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chargegrid.main")

# ----------------------------------------------------------------------
# Configuração do site / tarifa
# ----------------------------------------------------------------------

SITE_NAME = "Shopping Paulista — Estacionamento"
CONTRACTED_LIMIT_KW = 100.0
TARIFF_RS_PER_KWH = 4.26

CHARGER_LAYOUT = [
    ("CG-01", "Vaga A1", 22.0), ("CG-02", "Vaga A2", 22.0),
    ("CG-03", "Vaga A3", 22.0), ("CG-04", "Vaga A4", 22.0),
    ("CG-05", "Vaga B1", 22.0), ("CG-06", "Vaga B2", 22.0),
    ("CG-07", "Vaga B3", 22.0), ("CG-08", "Vaga B4", 22.0),
    ("CG-09", "Vaga C1", 22.0), ("CG-10", "Vaga C2", 22.0),
    ("CG-11", "Vaga C3", 22.0), ("CG-12", "Vaga C4", 22.0),
]

TICK_INTERVAL_S = 1.0

# ----------------------------------------------------------------------
# Estado global (demo single-process, em memória)
# ----------------------------------------------------------------------

controller = DemandController(SITE_NAME, CONTRACTED_LIMIT_KW)


class ConnectionManager:
    """Gerencia os WebSockets ativos e faz broadcast do estado."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()


# ----------------------------------------------------------------------
# Camada de demo — SEM alterar demand_controller.py
# ----------------------------------------------------------------------
# payment_status e sim_flags vivem aqui, fora do Charger/ChargingSession.
# O DemandController continua sabendo só de OCUPADO/LIVRE; isto é uma
# camada visual/demo por cima, mapeando charger_id -> estado mockado.
#
#   payment_status: "pago" | "pendente"           (mock do item P2-13)
#   sim_flags:       None | "manutencao" | "erro"  (mock do item P2-12,
#                     nunca entra no algoritmo de _redistribute_power)

payment_status: dict[str, str] = {}
sim_flags: dict[str, str | None] = {}

# ----------------------------------------------------------------------
# Camada do fluxo do cliente (login → carregar → pagar) — também fora do
# DemandController, pelo mesmo motivo: são dados de UI/demo (meta de kWh
# escolhida, qual cliente está em qual carregador), não estado elétrico.
#
#   charging_goals:      charger_id -> {"target_kwh", "cpf"} da sessão ativa
#   client_active_charger: cpf -> charger_id (para achar "minha recarga")
#   last_receipt:         cpf -> resumo da última recarga concluída (para a
#                          tela de conclusão sobreviver a um F5)
# ----------------------------------------------------------------------

charging_goals: dict[str, dict] = {}
client_active_charger: dict[str, str] = {}
last_receipt: dict[str, dict] = {}


def utilization_level(pct: float) -> str:
    """'ok' | 'warning' (>=90%) | 'critical' (>100%) — cores do DESIGN_SYSTEM.md."""
    if pct > 100:
        return "critical"
    if pct >= 90:
        return "warning"
    return "ok"


# ----------------------------------------------------------------------
# Faturamento / curva de lucro
# ----------------------------------------------------------------------

def format_brl(value: float) -> str:
    """Formata um valor como moeda no padrão brasileiro: 'R$ 1.234,56'."""
    s = f"{value:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {s}"


def profit_at(p: float) -> float:
    return -35 * p ** 2 + 298.5 * p - 406


def profit_curve(p_min: float = 0.0, p_max: float = 8.6, step: float = 0.2) -> list[tuple[float, float]]:
    points = []
    p = p_min
    while p <= p_max + 1e-9:
        points.append((round(p, 2), round(profit_at(p), 2)))
        p += step
    return points


def profit_curve_svg(points: list[tuple[float, float]], optimal_p: float, optimal_L: float,
                      width: int = 300, height: int = 90, pad: int = 8) -> dict:
    p_values = [p for p, _ in points]
    l_values = [l for _, l in points]
    p_min, p_max = min(p_values), max(p_values)
    l_min, l_max = min(l_values), max(l_values)
    l_span = (l_max - l_min) or 1.0

    def scale(p: float, l: float) -> tuple[float, float]:
        x = pad + (p - p_min) / (p_max - p_min) * (width - 2 * pad)
        y = pad + (1 - (l - l_min) / l_span) * (height - 2 * pad)
        return round(x, 1), round(y, 1)

    coords = [scale(p, l) for p, l in points]
    path = "M " + " L ".join(f"{x},{y}" for x, y in coords)
    marker_x, marker_y = scale(optimal_p, optimal_L)
    baseline_y = round(pad + (1 - (0 - l_min) / l_span) * (height - 2 * pad), 1)

    # Área preenchida sob a curva (technique "area chart") — mesmo path da
    # linha, fechado descendo até a baseline e voltando ao ponto inicial.
    area_path = f"{path} L {coords[-1][0]},{baseline_y} L {coords[0][0]},{baseline_y} Z"

    return {
        "path": path, "area_path": area_path, "width": width, "height": height,
        "marker_x": marker_x, "marker_y": marker_y, "baseline_y": baseline_y,
    }


def billing_snapshot() -> dict:
    active_kwh = sum(s.energy_consumed_kwh for s in controller.active_sessions)
    completed_kwh = sum(s.energy_consumed_kwh for s in controller.completed_sessions)
    total_kwh = active_kwh + completed_kwh
    optimal_p = 298.5 / 70
    optimal_l = profit_at(optimal_p)
    curve = profit_curve()

    # Ticket médio e duração média — só sobre completed_sessions (sessões já
    # finalizadas), que é o dado confiável para uma "média"; sessões ativas
    # ainda estão em andamento e distorceriam a métrica.
    completed = controller.completed_sessions
    n_completed = len(completed)
    avg_ticket_rs = (completed_kwh * TARIFF_RS_PER_KWH / n_completed) if n_completed else None
    avg_duration_min = (sum(s.duration_minutes() for s in completed) / n_completed) if n_completed else None

    return {
        "tariff_rs_per_kwh": TARIFF_RS_PER_KWH,
        "kwh_today": round(total_kwh, 3),
        "revenue_today_rs": round(total_kwh * TARIFF_RS_PER_KWH, 2),
        "sessions_today": len(controller.active_sessions) + len(controller.completed_sessions),
        "completed_sessions_count": n_completed,
        "avg_ticket_rs": round(avg_ticket_rs, 2) if avg_ticket_rs is not None else None,
        "avg_duration_min": round(avg_duration_min, 1) if avg_duration_min is not None else None,
        "profit_curve": curve,
        "optimal_p": round(optimal_p, 2),
        "optimal_profit_rs": round(optimal_l, 2),
        "curve_svg": profit_curve_svg(curve, optimal_p, optimal_l),
    }


def completed_sessions_payload() -> list[dict]:
    return [
        {
            "charger_id": s.charger_id,
            "vehicle_id": s.vehicle_id,
            "started_at": s.started_at.isoformat(),
            "duration_min": round(s.duration_minutes(), 1),
            "energy_kwh": round(s.energy_consumed_kwh, 3),
            "revenue_rs": round(s.energy_consumed_kwh * TARIFF_RS_PER_KWH, 2),
        }
        for s in controller.completed_sessions
    ]


def enrich_charger(c: dict) -> dict:
    """Anexa as flags de demo (fora do DemandController) ao dict de um carregador."""
    c = dict(c)
    c["payment_status"] = payment_status.get(c["id"], "pago")
    c["sim_flag"] = sim_flags.get(c["id"])
    return c


def build_payload() -> dict:
    state = controller._state_snapshot()
    state["clock"] = datetime.now().strftime("%H:%M:%S")
    state["billing"] = billing_snapshot()
    state["utilization_level"] = utilization_level(state["utilization_pct"])
    state["chargers"] = [enrich_charger(c) for c in state["chargers"]]
    return state


def random_plate() -> str:
    letters = lambda n: "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=n))
    return f"{letters(3)}-{random.randint(0,9)}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{random.randint(10,99)}"


# ----------------------------------------------------------------------
# Estimativa de recarga (App do Cliente) — NÃO toca no DemandController
# ----------------------------------------------------------------------

def estimate_charge(charger_id: str, target_kwh: float) -> dict:
    """
    Estimativa simples de tempo/custo para uma recarga que AINDA NÃO
    começou, baseada na condição ATUAL da rede (kW ainda disponíveis
    agora). NÃO é um modelo de previsão de horário de pico nem simula a
    redistribuição real que aconteceria ao conectar — é só uma projeção
    ingênua assumindo potência constante, para dar uma noção ao cliente
    antes de pagar. A tela deixa isso explícito para não parecer uma
    previsão de verdade.
    """
    charger = controller.chargers[charger_id]
    estimated_power_kw = round(min(charger.max_kw, max(0.0, controller.available_kw)), 2)

    if estimated_power_kw > 0:
        estimated_time_min = round((target_kwh / estimated_power_kw) * 60, 1)
    else:
        estimated_time_min = None  # rede sem folga agora — não dá pra estimar tempo

    return {
        "charger_id": charger_id,
        "target_kwh": round(target_kwh, 2),
        "estimated_power_kw": estimated_power_kw,
        "estimated_time_min": estimated_time_min,
        "estimated_cost_rs": round(target_kwh * TARIFF_RS_PER_KWH, 2),
    }


# ----------------------------------------------------------------------
# Ticker: acumula energia das sessões ativas e transmite o estado a cada 1s
# ----------------------------------------------------------------------

async def ticker_loop() -> None:
    while True:
        await asyncio.sleep(TICK_INTERVAL_S)
        for session in controller.active_sessions:
            session.update_energy(TICK_INTERVAL_S)
        if controller.active_sessions or manager.active:
            await manager.broadcast(build_payload())


# ----------------------------------------------------------------------
# App / lifespan
# ----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    for charger_id, location, max_kw in CHARGER_LAYOUT:
        controller.register_charger(charger_id, max_kw, location)
        payment_status[charger_id] = "pago"
        sim_flags[charger_id] = None
    task = asyncio.create_task(ticker_loop())
    logger.info("ChargeGrid Intelligence iniciado — %d carregadores registrados", len(controller.chargers))
    yield
    task.cancel()


app = FastAPI(title="ChargeGrid Intelligence", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["brl"] = format_brl


# ----------------------------------------------------------------------
# Rotas — páginas
# ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    payload = build_payload()
    return templates.TemplateResponse(
        "dashboard_gestao.html",
        {"request": request, "state": payload, "tariff": TARIFF_RS_PER_KWH},
    )


# ----------------------------------------------------------------------
# App do Cliente — autenticação (CPF + senha, demo sem segurança real)
# ----------------------------------------------------------------------

def _current_account(request: Request) -> dict | None:
    token = request.cookies.get(client_store.SESSION_COOKIE)
    return client_store.get_account_from_token(token)


@app.get("/cliente/login", response_class=HTMLResponse)
async def cliente_login_form(request: Request):
    if _current_account(request):
        return RedirectResponse("/cliente/home", status_code=303)
    return templates.TemplateResponse(
        "cliente/login.html",
        {"request": request, "error": None, "cpf": "", "show_forgot": False},
    )


@app.post("/cliente/login", response_class=HTMLResponse)
async def cliente_login(request: Request, cpf: str = Form(...), senha: str = Form(...)):
    if not client_store.account_exists(cpf):
        return RedirectResponse(f"/cliente/cadastro?cpf={client_store.normalize_cpf(cpf)}", status_code=303)
    account = client_store.check_login(cpf, senha)
    if not account:
        return templates.TemplateResponse(
            "cliente/login.html",
            {"request": request, "error": "Senha incorreta.", "cpf": cpf, "show_forgot": True},
            status_code=401,
        )
    token = client_store.start_session(account["cpf"])
    response = RedirectResponse("/cliente/home", status_code=303)
    response.set_cookie(client_store.SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@app.get("/cliente/cadastro", response_class=HTMLResponse)
async def cliente_cadastro_form(request: Request, cpf: str = ""):
    return templates.TemplateResponse(
        "cliente/cadastro.html",
        {"request": request, "error": None, "form": {"nome": "", "cpf": cpf, "email": ""}},
    )


@app.post("/cliente/cadastro", response_class=HTMLResponse)
async def cliente_cadastro(
    request: Request,
    nome: str = Form(...),
    cpf: str = Form(...),
    email: str = Form(...),
    senha: str = Form(...),
    confirmar_senha: str = Form(...),
):
    form = {"nome": nome, "cpf": cpf, "email": email}
    cpf_norm = client_store.normalize_cpf(cpf)

    if len(cpf_norm) != 11:
        return templates.TemplateResponse(
            "cliente/cadastro.html",
            {"request": request, "error": "CPF inválido — deve ter 11 dígitos.", "form": form},
            status_code=400,
        )
    if client_store.account_exists(cpf_norm):
        return templates.TemplateResponse(
            "cliente/cadastro.html",
            {"request": request, "error": "Já existe uma conta com esse CPF. Faça login.", "form": form},
            status_code=409,
        )
    if not senha or senha != confirmar_senha:
        return templates.TemplateResponse(
            "cliente/cadastro.html",
            {"request": request, "error": "As senhas não coincidem (ou estão vazias).", "form": form},
            status_code=400,
        )

    account = client_store.create_account(nome, cpf_norm, email, senha)
    token = client_store.start_session(account["cpf"])
    response = RedirectResponse("/cliente/home", status_code=303)
    response.set_cookie(client_store.SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@app.get("/cliente/esqueci-senha", response_class=HTMLResponse)
async def cliente_esqueci_senha(request: Request):
    """Só a mensagem/link — sem fluxo de recuperação real (definido com o professor)."""
    return templates.TemplateResponse("cliente/esqueci_senha.html", {"request": request})


@app.post("/cliente/logout")
async def cliente_logout(request: Request):
    client_store.end_session(request.cookies.get(client_store.SESSION_COOKIE))
    response = RedirectResponse("/cliente/login", status_code=303)
    response.delete_cookie(client_store.SESSION_COOKIE)
    return response


# ----------------------------------------------------------------------
# App do Cliente — páginas autenticadas
# ----------------------------------------------------------------------

@app.get("/cliente/home", response_class=HTMLResponse)
async def cliente_home(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)
    active_charger_id = client_active_charger.get(account["cpf"])
    return templates.TemplateResponse(
        "cliente/home.html",
        {"request": request, "account": account, "active_charger_id": active_charger_id},
    )


@app.get("/cliente/carregar", response_class=HTMLResponse)
async def cliente_carregar_form(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)
    if account["cpf"] in client_active_charger:
        return RedirectResponse("/cliente/carregando", status_code=303)

    payload = build_payload()
    free_chargers = [c for c in payload["chargers"] if c["status"] == "LIVRE"]
    return templates.TemplateResponse(
        "cliente/carregar_config.html",
        {"request": request, "free_chargers": free_chargers, "error": None,
         "form": {"placa": "", "charger_id": "", "energia_kwh": ""}},
    )


@app.post("/cliente/carregar/resumo", response_class=HTMLResponse)
async def cliente_carregar_resumo(
    request: Request,
    placa: str = Form(...),
    charger_id: str = Form(...),
    energia_kwh: float = Form(...),
):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)

    charger_id = charger_id.upper()
    placa = placa.strip().upper()
    payload = build_payload()
    free_chargers = [c for c in payload["chargers"] if c["status"] == "LIVRE"]

    if charger_id not in controller.chargers or energia_kwh <= 0 or not placa:
        return templates.TemplateResponse(
            "cliente/carregar_config.html",
            {"request": request, "free_chargers": free_chargers,
             "error": "Preencha placa, vaga e energia desejada (kWh) corretamente.",
             "form": {"placa": placa, "charger_id": charger_id, "energia_kwh": energia_kwh}},
            status_code=400,
        )

    if controller.chargers[charger_id].is_occupied:
        return templates.TemplateResponse(
            "cliente/carregar_config.html",
            {"request": request, "free_chargers": free_chargers,
             "error": f"A vaga {charger_id} acabou de ficar ocupada — escolha outra.",
             "form": {"placa": placa, "charger_id": "", "energia_kwh": energia_kwh}},
            status_code=409,
        )

    charger_info = next(c for c in payload["chargers"] if c["id"] == charger_id)
    estimate = estimate_charge(charger_id, energia_kwh)
    return templates.TemplateResponse(
        "cliente/carregar_resumo.html",
        {"request": request, "placa": placa, "charger": charger_info,
         "energia_kwh": energia_kwh, "estimate": estimate, "site": SITE_NAME},
    )


@app.post("/cliente/carregar/pagar", response_class=HTMLResponse)
async def cliente_carregar_pagar(
    request: Request,
    placa: str = Form(...),
    charger_id: str = Form(...),
    energia_kwh: float = Form(...),
    metodo_pagamento: str = Form(...),
):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)

    charger_id = charger_id.upper()
    placa = placa.strip().upper()

    # Regra de teste combinada: todo pagamento é aprovado instantaneamente —
    # não há gateway de pagamento real nesta demo.
    result = controller.vehicle_connect(charger_id, placa)
    if isinstance(result, dict) and "error" in result:
        payload = build_payload()
        free_chargers = [c for c in payload["chargers"] if c["status"] == "LIVRE"]
        return templates.TemplateResponse(
            "cliente/carregar_config.html",
            {"request": request, "free_chargers": free_chargers,
             "error": f"Não foi possível iniciar a recarga: {result['error']}",
             "form": {"placa": placa, "charger_id": "", "energia_kwh": energia_kwh}},
            status_code=409,
        )

    charging_goals[charger_id] = {"target_kwh": energia_kwh, "cpf": account["cpf"]}
    client_active_charger[account["cpf"]] = charger_id
    client_store.register_vehicle(account["cpf"], placa)
    controller._log_event(
        "CLIENT_PAYMENT_APPROVED", charger_id,
        f"[DEMO] Pagamento ({metodo_pagamento}) aprovado — recarga iniciada pelo cliente {account['nome']}",
    )
    await manager.broadcast(build_payload())

    return RedirectResponse("/cliente/carregando", status_code=303)


@app.get("/cliente/carregando", response_class=HTMLResponse)
async def cliente_carregando(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)

    charger_id = client_active_charger.get(account["cpf"])
    if not charger_id:
        return RedirectResponse("/cliente/home", status_code=303)

    payload = build_payload()
    charger_state = next((c for c in payload["chargers"] if c["id"] == charger_id), None)

    # Se um operador forçou a parada pelo Dashboard enquanto o cliente estava
    # nesta tela, a vaga já não está mais OCUPADA — encerra a associação e
    # manda o cliente de volta pro início em vez de mostrar uma tela travada.
    if not charger_state or charger_state["status"] != "OCUPADO":
        client_active_charger.pop(account["cpf"], None)
        charging_goals.pop(charger_id, None)
        return RedirectResponse("/cliente/home", status_code=303)

    target_kwh = charging_goals.get(charger_id, {}).get("target_kwh", 0.0)
    return templates.TemplateResponse(
        "cliente/carregando.html",
        {"request": request, "charger": charger_state, "tariff": TARIFF_RS_PER_KWH,
         "site": SITE_NAME, "target_kwh": target_kwh},
    )


@app.post("/cliente/carregando/encerrar", response_class=HTMLResponse)
async def cliente_carregando_encerrar(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)

    charger_id = client_active_charger.get(account["cpf"])
    if not charger_id:
        return RedirectResponse("/cliente/home", status_code=303)

    charger = controller.chargers[charger_id]
    session = charger.session  # referência — vehicle_disconnect() atualiza e arquiva esse mesmo objeto
    location = charger.location

    result = controller.vehicle_disconnect(charger_id)
    await manager.broadcast(build_payload())

    charging_goals.pop(charger_id, None)
    client_active_charger.pop(account["cpf"], None)

    if session is not None and not (isinstance(result, dict) and "error" in result):
        # Dados já existem em completed_sessions — só formata pra tela de conclusão.
        last_receipt[account["cpf"]] = {
            "vehicle_id": session.vehicle_id,
            "charger_id": charger_id,
            "location": location,
            "energy_kwh": round(session.energy_consumed_kwh, 3),
            "duration_min": round(session.duration_minutes(), 1),
            "revenue_rs": round(session.energy_consumed_kwh * TARIFF_RS_PER_KWH, 2),
        }

    return RedirectResponse("/cliente/carregando/concluido", status_code=303)


@app.get("/cliente/carregando/concluido", response_class=HTMLResponse)
async def cliente_carregando_concluido(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)
    receipt = last_receipt.get(account["cpf"])
    if not receipt:
        return RedirectResponse("/cliente/home", status_code=303)
    return templates.TemplateResponse("cliente/conclusao.html", {"request": request, "r": receipt, "site": SITE_NAME})


@app.get("/cliente/perfil", response_class=HTMLResponse)
async def cliente_perfil(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)
    return templates.TemplateResponse("cliente/perfil.html", {"request": request, "account": account})


@app.get("/cliente/historico", response_class=HTMLResponse)
async def cliente_historico(request: Request):
    account = _current_account(request)
    if not account:
        return RedirectResponse("/cliente/login", status_code=303)
    plates = set(account.get("veiculos", []))
    sessions = [s for s in completed_sessions_payload() if s["vehicle_id"] in plates]
    sessions.reverse()
    return templates.TemplateResponse("cliente/historico.html", {"request": request, "account": account, "sessions": sessions})


# ----------------------------------------------------------------------
# App do Cliente — estimativa de recarga (NÃO toca no DemandController)
# ----------------------------------------------------------------------

@app.post("/estimate")
async def estimate(charger_id: str = Form(...), target_kwh: float = Form(...)):
    charger_id = charger_id.upper()
    if charger_id not in controller.chargers:
        raise HTTPException(status_code=404, detail="Carregador não encontrado.")
    if target_kwh <= 0:
        raise HTTPException(status_code=400, detail="Energia desejada deve ser maior que zero.")
    return JSONResponse(estimate_charge(charger_id, target_kwh))


# ----------------------------------------------------------------------
# Rotas — HTMX partials
# ----------------------------------------------------------------------

def _require_charger(charger_id: str) -> str:
    charger_id = charger_id.upper()
    if charger_id not in controller.chargers:
        raise HTTPException(status_code=404, detail="Carregador não encontrado.")
    return charger_id


def charger_details_response(request: Request, charger_id: str) -> HTMLResponse:
    payload = build_payload()
    charger_state = next(c for c in payload["chargers"] if c["id"] == charger_id)
    return templates.TemplateResponse(
        "partials/charger_details.html",
        {"request": request, "c": charger_state, "tariff": TARIFF_RS_PER_KWH},
    )


@app.get("/charger/{charger_id}/details", response_class=HTMLResponse)
async def charger_details(request: Request, charger_id: str):
    charger_id = _require_charger(charger_id)
    return charger_details_response(request, charger_id)


@app.get("/charger/{charger_id}/events", response_class=HTMLResponse)
async def charger_events(request: Request, charger_id: str):
    """Log de eventos completo (não só os últimos 5 globais), filtrado por carregador."""
    charger_id = _require_charger(charger_id)
    events = [e for e in controller.events if e["source"] == charger_id][-20:]
    events.reverse()
    return templates.TemplateResponse(
        "partials/charger_events.html",
        {"request": request, "events": events, "charger_id": charger_id},
    )


@app.post("/charger/{charger_id}/demo/payment", response_class=HTMLResponse)
async def set_payment_status(request: Request, charger_id: str, status: str = Form(...)):
    """[DEMO] Marca o status de pagamento mockado — não é uma integração real (ver P2-13)."""
    charger_id = _require_charger(charger_id)
    if status not in ("pago", "pendente"):
        raise HTTPException(status_code=400, detail="Status de pagamento inválido.")
    payment_status[charger_id] = status
    controller._log_event("DEMO_PAYMENT_FLAG", charger_id, f"[DEMO] pagamento marcado como '{status}'")
    await manager.broadcast(build_payload())
    return charger_details_response(request, charger_id)


@app.post("/charger/{charger_id}/demo/flag", response_class=HTMLResponse)
async def set_sim_flag(request: Request, charger_id: str, flag: str = Form(...)):
    """[DEMO] Liga/desliga a flag visual de manutenção/erro — camada separada do
    DemandController, nunca entra no algoritmo de redistribuição (ver P2-12)."""
    charger_id = _require_charger(charger_id)
    if flag not in ("none", "manutencao", "erro"):
        raise HTTPException(status_code=400, detail="Flag de simulação inválida.")
    sim_flags[charger_id] = None if flag == "none" else flag
    label = "removida" if flag == "none" else f"marcada como '{flag}'"
    controller._log_event("DEMO_SIM_FLAG", charger_id, f"[DEMO] flag de simulação {label}")
    await manager.broadcast(build_payload())
    return charger_details_response(request, charger_id)


@app.post("/chat", response_class=HTMLResponse)
async def chat(request: Request, message: str = Form(...)):
    reply = chatbot.answer(message, controller)
    return templates.TemplateResponse(
        "partials/chat_message.html",
        {"request": request, "user_message": message, "reply": reply},
    )


# ----------------------------------------------------------------------
# Rotas — ações reais no DemandController
# ----------------------------------------------------------------------

@app.post("/connect")
async def connect(charger_id: str = Form(...), vehicle_id: str | None = Form(None)):
    charger_id = charger_id.upper()
    if charger_id not in controller.chargers:
        raise HTTPException(status_code=404, detail="Carregador não encontrado.")
    plate = (vehicle_id or "").strip() or random_plate()
    result = controller.vehicle_connect(charger_id, plate)
    await manager.broadcast(build_payload())
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return JSONResponse({"ok": True})


@app.post("/disconnect")
async def disconnect(charger_id: str = Form(...)):
    charger_id = charger_id.upper()
    if charger_id not in controller.chargers:
        raise HTTPException(status_code=404, detail="Carregador não encontrado.")
    result = controller.vehicle_disconnect(charger_id)
    await manager.broadcast(build_payload())
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return JSONResponse({"ok": True})


@app.get("/sessions")
async def sessions():
    return JSONResponse(completed_sessions_payload())


# ----------------------------------------------------------------------
# WebSocket — broadcast de estado em tempo real
# ----------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json(build_payload())
        while True:
            # Mantém a conexão viva; o cliente não precisa enviar nada.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
