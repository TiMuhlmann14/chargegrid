"""
ChargeGrid Intelligence — Integração de pagamento (Mercado Pago Pix)
----------------------------------------------------------------------
Encapsula TODA a lógica de cobrança via Mercado Pago. main.py só chama
create_pix_charge() / get_charge_status() — nenhuma outra rota fala com
o SDK do Mercado Pago diretamente (ver CLAUDE.md).

PAYMENT_MODE (variável de ambiente, ver .env na raiz do projeto) decide
o comportamento em main.py:
  - "mercadopago" (padrão): cria uma cobrança Pix real em sandbox e o
    status vem de verdade, via polling neste módulo.
  - "mock": modo de contingência para a demo ao vivo — main.py mantém o
    caminho antigo (aprovação instantânea) e nem importa/chama nada
    daqui. Ver seção "Modo de contingência" no CLAUDE.md.

Credenciais — SEMPRE via .env na raiz do projeto (NUNCA hardcoded aqui):
  MP_ACCESS_TOKEN, MP_PUBLIC_KEY, PAYMENT_MODE

----------------------------------------------------------------------
DECISÃO DE API — Orders API (/v1/orders), não a Payments API clássica
----------------------------------------------------------------------
A doc oficial (developers.mercadopago.com.br/pt/docs/checkout-api-orders/
integration-test/pix) recomenda a Orders API para testar Pix. Isso foi
confirmado ao vivo nesta sessão de implementação:
  - sdk.payment().create() (POST /v1/payments, API clássica) devolveu
    401 "Unauthorized use of live credentials" mesmo com uma credencial
    de sandbox válida — causado pelo e-mail de pagador inventado, não
    por credencial de produção (confirmado via GET /users/me: a conta
    do token tem "test_data": {"test_user": true}).
  - sdk.order().create() (POST /v1/orders, API nova) com o MESMO token
    funcionou de primeira (201) e devolveu QR code + copia-e-cola
    normalmente.
Por isso este módulo usa sdk.order(), não sdk.payment().
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import mercadopago

logger = logging.getLogger("chargegrid.payments")

# Carrega o .env da RAIZ do projeto (não backend/) — funciona tanto
# rodando `uvicorn main:app` de dentro de backend/ quanto de outro cwd,
# porque o caminho é resolvido a partir deste arquivo, não do cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

PAYMENT_MODE = os.getenv("PAYMENT_MODE", "mercadopago").strip().lower()
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
MP_PUBLIC_KEY = os.getenv("MP_PUBLIC_KEY", "").strip()

_sdk: "mercadopago.SDK | None" = None


def _get_sdk() -> mercadopago.SDK:
    global _sdk
    if _sdk is None:
        if not MP_ACCESS_TOKEN:
            raise RuntimeError(
                "MP_ACCESS_TOKEN não configurado no .env (raiz do projeto) — "
                "necessário para PAYMENT_MODE=mercadopago."
            )
        _sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    return _sdk


# ----------------------------------------------------------------------
# "APRO" — mecanismo OFICIAL de simulação de teste do Mercado Pago
# ----------------------------------------------------------------------
# O Pix gerado em ambiente sandbox NÃO está conectado à rede Pix real —
# não existe QR "escaneável" nem um jeito de aprovar manualmente pelo
# app de um banco de verdade. Para isso, o Mercado Pago documenta nomes
# de pagador especiais que, usados em payer.first_name, simulam o
# resultado do pagamento (ex.: "APRO" = aprovado). Esse gatilho SÓ tem
# efeito com credenciais de conta de TESTE — em produção seria só um
# nome comum, sem efeito nenhum.
#
# Confirmado ao vivo nesta sessão: um pedido criado com
# payer.first_name="APRO" evolui sozinho de "action_required" para
# "processed"/"accredited" em poucos segundos — sem nenhum login da
# conta de teste COMPRADORA. É por isso que o passo a passo de teste no
# README/CLAUDE.md não pede pra logar em lugar nenhum.
#
# IMPORTANTE — isso é só o payload enviado à API do Mercado Pago. NUNCA
# aparece em nenhuma tela do ChargeGrid: o resumo da recarga, a tela de
# carregamento e o recibo sempre mostram o nome/placa reais da conta
# logada (ver main.py — pix_pending guarda "placa"/cpf do cliente de
# verdade; "APRO" só é usado dentro de create_pix_charge, abaixo).
_SANDBOX_TEST_PAYER_NAME = "APRO"


def create_pix_charge(valor_rs: float, descricao: str, external_reference: str) -> dict:
    """
    Cria uma cobrança Pix via Orders API do Mercado Pago (sandbox).

    Args:
        valor_rs: valor a cobrar, em reais (já calculado por main.py —
            reaproveita a mesma tarifa dinâmica de estimate_charge(),
            não inventa um preço novo aqui).
        descricao: texto livre mostrado na cobrança.
        external_reference: referência única desta tentativa de
            cobrança (main.py monta a partir de charger_id + cpf +
            timestamp) — usada também para gerar o e-mail de teste do
            pagador exigido pela API.

    Returns:
        dict com order_id, status ("pending"|"approved"|"rejected"),
        qr_code (Pix copia-e-cola) e qr_code_base64 (imagem do QR).

    Raises:
        RuntimeError: se a API recusar a criação (token ausente, erro de
            rede, payload rejeitado etc.) — main.py decide como mostrar
            isso na tela.
    """
    sdk = _get_sdk()
    amount_str = f"{valor_rs:.2f}"

    order_data = {
        "type": "online",
        "total_amount": amount_str,
        "external_reference": external_reference,
        "description": descricao,
        "payer": {
            # Ver comentário de _SANDBOX_TEST_PAYER_NAME acima — valor de
            # simulação exclusivo do sandbox, nunca mostrado na interface.
            "first_name": _SANDBOX_TEST_PAYER_NAME,
            "email": f"{external_reference}@testuser.com",
        },
        "transactions": {
            "payments": [
                {
                    "amount": amount_str,
                    "payment_method": {"id": "pix", "type": "bank_transfer"},
                }
            ]
        },
    }

    result = sdk.order().create(order_data)
    if result["status"] not in (200, 201):
        raise RuntimeError(
            f"Mercado Pago recusou a criação do Pix (HTTP {result['status']}): {result['response']}"
        )

    resp = result["response"]
    order_id = resp["id"]
    qr_code, qr_code_base64 = _extract_qr(resp)

    if not qr_code or not qr_code_base64:
        # Testado ao vivo várias vezes nesta sessão: o normal é o QR já vir
        # pronto na resposta da criação. Mas a API não garante isso por
        # contrato — se vier faltando, tenta UMA consulta extra na hora
        # (não é o polling de status; é só pra não obrigar o cliente a
        # esperar 3s à toa se o dado já estiver disponível). Se ainda
        # assim não vier, quem finaliza o preenchimento é o polling normal
        # em get_charge_status() — main.py trata "None" como "ainda
        # gerando", nunca mostra o valor cru na tela.
        logger.warning("Pix %s criado sem QR pronto na resposta de create() — tentando GET imediato.", order_id)
        try:
            retry = sdk.order().get(order_id)
            if retry["status"] in (200, 201):
                qr_code, qr_code_base64 = _extract_qr(retry["response"])
        except Exception:
            logger.exception("Falha na consulta imediata de retry do Pix %s", order_id)

    return {
        "order_id": order_id,
        "status": _normalize_status(resp.get("status"), resp.get("status_detail")),
        "qr_code": qr_code,
        "qr_code_base64": qr_code_base64,
        "raw_status": resp.get("status"),
        "raw_status_detail": resp.get("status_detail"),
    }


def get_charge_status(order_id: str) -> dict:
    """
    Consulta o status atual de uma cobrança Pix — chamado pelo polling em
    main.py a cada ~3s. Também devolve qr_code/qr_code_base64 (podem vir
    None): serve pra main.py completar o QR na tela caso ele não tivesse
    vindo pronto na criação (ver create_pix_charge acima).
    """
    sdk = _get_sdk()
    result = sdk.order().get(order_id)
    if result["status"] not in (200, 201):
        raise RuntimeError(
            f"Mercado Pago recusou a consulta do Pix (HTTP {result['status']}): {result['response']}"
        )
    resp = result["response"]
    qr_code, qr_code_base64 = _extract_qr(resp)
    return {
        "order_id": resp["id"],
        "status": _normalize_status(resp.get("status"), resp.get("status_detail")),
        "qr_code": qr_code,
        "qr_code_base64": qr_code_base64,
        "raw_status": resp.get("status"),
        "raw_status_detail": resp.get("status_detail"),
    }


def _extract_qr(resp: dict) -> tuple:
    """
    Caminho único (usado por create_pix_charge e get_charge_status) pra
    extrair o QR de uma resposta da Orders API — evita duplicar esse
    caminho de campos em dois lugares divergindo com o tempo.
    Estrutura confirmada ao vivo: resp["transactions"]["payments"][0]
    ["payment_method"]["qr_code" / "qr_code_base64"].
    """
    payments_list = resp.get("transactions", {}).get("payments") or [{}]
    payment_method = payments_list[0].get("payment_method") or {}
    return payment_method.get("qr_code"), payment_method.get("qr_code_base64")


def _normalize_status(status: "str | None", status_detail: "str | None") -> str:
    """
    Reduz o vocabulário de status da Orders API a três estados que o
    resto do app entende: "approved" | "rejected" | "pending".

    Confirmado ao vivo (ver docstring do módulo): o status inicial de um
    Pix recém-criado é "action_required"/"waiting_transfer"; quando
    aprovado (via o gatilho "APRO"), vira "processed"/"accredited".
    "canceled"/"expired" tratamos como rejeitado. Qualquer outra
    combinação de "processed" sem "accredited" também é tratada como
    rejeitada, por segurança — nunca liberamos uma recarga sem a
    confirmação clara de pagamento aprovado.
    """
    if status == "processed" and status_detail == "accredited":
        return "approved"
    if status in ("canceled", "expired"):
        return "rejected"
    if status == "processed":
        return "rejected"
    return "pending"
