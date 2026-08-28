"""
ChargeGrid Intelligence — seed_demo.py
---------------------------------------
FERRAMENTA DE BASTIDOR, não é uma feature do produto. Só serve para
popular rapidamente o grid com várias vagas ocupadas ANTES de gravar a
demo, sem precisar repetir o fluxo do cliente (login → carregar → pagar)
manualmente uma vez por carregador.

Chama o endpoint HTTP /connect que já existe em main.py (que por sua vez
chama vehicle_connect() de verdade no DemandController) — não importa
main.py diretamente, então funciona contra o servidor já rodando.

Uso (com o servidor rodando em outra janela: `uvicorn main:app --port 8000`
dentro de backend/):

    python scripts/seed_demo.py                  # ocupa 6 vagas (padrão)
    python scripts/seed_demo.py --count 12        # ocupa todas as 12
    python scripts/seed_demo.py --host http://localhost:8000
"""

import argparse
import random
import urllib.error
import urllib.request

DEFAULT_CHARGER_IDS = [f"CG-{i:02d}" for i in range(1, 13)]

DEMO_PLATES = {
    "CG-01": "ABC-1A11", "CG-02": "XYZ-2B22", "CG-03": "QRS-3C33",
    "CG-04": "LMN-4D44", "CG-05": "OPQ-5E55", "CG-06": "TUV-6F66",
    "CG-07": "HIJ-7G77", "CG-08": "KLM-8H88", "CG-09": "NOP-9I99",
    "CG-10": "STU-0J00", "CG-11": "VWX-1K11", "CG-12": "YZA-2L22",
}


def random_plate() -> str:
    letters = lambda n: "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=n))
    return f"{letters(3)}-{random.randint(0, 9)}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{random.randint(10, 99)}"


def post_connect(host: str, charger_id: str, vehicle_id: str) -> None:
    body = f"charger_id={charger_id}&vehicle_id={vehicle_id}".encode("utf-8")
    req = urllib.request.Request(
        f"{host}/connect", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"  {charger_id} <- {vehicle_id}: OK ({resp.status})")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"  {charger_id} <- {vehicle_id}: FALHOU ({exc.code}) {detail}")
    except urllib.error.URLError as exc:
        print(f"  {charger_id}: não consegui conectar em {host} ({exc.reason}). "
              f"O servidor está rodando (uvicorn main:app --port 8000)?")
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:8000", help="Base URL do servidor ChargeGrid")
    parser.add_argument("--count", type=int, default=6, help="Quantas vagas ocupar (1-12, padrão 6)")
    args = parser.parse_args()

    count = max(1, min(12, args.count))
    charger_ids = DEFAULT_CHARGER_IDS[:count]

    print(f"[seed_demo] Ocupando {count} vaga(s) em {args.host} ...")
    for charger_id in charger_ids:
        plate = DEMO_PLATES.get(charger_id, random_plate())
        post_connect(args.host, charger_id, plate)
    print("[seed_demo] Concluído.")


if __name__ == "__main__":
    main()
