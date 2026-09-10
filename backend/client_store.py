"""
ChargeGrid Intelligence — Contas de cliente (demo)
---------------------------------------------------
Cadastro/login simplificado do App do Cliente. Em memória, sem hashing de
senha e sem persistência em disco — mesmo padrão "sem banco de dados" do
resto do sistema (ver payment_status/sim_flags em main.py). NÃO é um
mecanismo de autenticação real; existe só para a demo saber "qual cliente
está logado" e para amarrar isso à sessão de recarga que ele iniciou.

Contrato: normalize_cpf/create_account/check_login/start_session/
end_session/get_account_from_token — main.py só chama essas funções,
nunca mexe nos dicts internos diretamente.
"""

import re
import secrets

# cpf normalizado (só dígitos) -> conta
accounts: dict[str, dict] = {}

# token de sessão (cookie) -> cpf normalizado
sessions: dict[str, str] = {}

SESSION_COOKIE = "cg_client_session"


def normalize_cpf(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")


def account_exists(cpf: str) -> bool:
    return normalize_cpf(cpf) in accounts


def create_account(nome: str, cpf: str, email: str, senha: str) -> dict:
    cpf_norm = normalize_cpf(cpf)
    account = {
        "nome": nome.strip(),
        "cpf": cpf_norm,
        "email": email.strip(),
        "senha": senha,  # plaintext — demo, sem segurança real (definido com o usuário)
        "veiculos": [],  # placas já usadas por essa conta, para o histórico (P1)
        "veiculos_cadastrados": [],  # [{modelo, placa}] cadastrados pela conta (P0-1)
    }
    accounts[cpf_norm] = account
    return account


def check_login(cpf: str, senha: str) -> dict | None:
    account = accounts.get(normalize_cpf(cpf))
    if account and account["senha"] == senha:
        return account
    return None


def start_session(cpf: str) -> str:
    token = secrets.token_urlsafe(24)
    sessions[token] = normalize_cpf(cpf)
    return token


def end_session(token: str | None) -> None:
    if token:
        sessions.pop(token, None)


def get_account_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    cpf = sessions.get(token)
    return accounts.get(cpf) if cpf else None


def register_vehicle(cpf: str, plate: str) -> None:
    """Lembra essa placa na conta — usado pelo histórico (P1)."""
    plate = (plate or "").strip().upper()
    account = accounts.get(normalize_cpf(cpf))
    if account and plate and plate not in account["veiculos"]:
        account["veiculos"].append(plate)


def list_vehicles(cpf: str) -> list[dict]:
    """Veículos cadastrados (modelo + placa) da conta — para a tela de
    'carregar veículo' oferecer seleção em vez de digitar a placa toda
    vez (P0-1)."""
    account = accounts.get(normalize_cpf(cpf))
    return list(account["veiculos_cadastrados"]) if account else []


def add_vehicle(cpf: str, modelo: str, placa: str) -> dict | None:
    """
    Cadastra um veículo (modelo + placa) na conta, inline na tela de
    'carregar veículo' (sem forçar uma etapa de perfil separada). Dedup
    por placa — cadastrar de novo a mesma placa só devolve a existente.
    Também chama register_vehicle() para manter a lista usada pelo
    histórico (P1) em sincronia, sem duplicar esse conceito.
    """
    account = accounts.get(normalize_cpf(cpf))
    if not account:
        return None
    placa = (placa or "").strip().upper()
    if not placa:
        return None
    existing = next((v for v in account["veiculos_cadastrados"] if v["placa"] == placa), None)
    if existing:
        return existing
    vehicle = {"modelo": (modelo or "").strip() or "Veículo", "placa": placa}
    account["veiculos_cadastrados"].append(vehicle)
    register_vehicle(cpf, placa)
    return vehicle
