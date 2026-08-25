"""
ChargeGrid Intelligence — Demand Controller
-------------------------------------------
Camada de inteligência central: gerencia a potência disponível
e redistribui entre os carregadores conectados em tempo real.

Projetado para ser desacoplado da interface (terminal, web, API).
Toda lógica de negócio fica aqui — a interface apenas chama os métodos.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

# Mapa de registradores do hardware real (GoodWe HCA G2)
# Na Sprint 2: usado como referência documental (simulação interna)
# Na Sprint 3+: usar com pymodbus para comunicação real
#
#   from core.modbus_map import HCA_G2, power_to_register
#   client = ModbusTcpClient(charger_ip, port=HCA_G2.MODBUS_TCP_PORT)
#   client.write_register(HCA_G2.WRITE.MAX_CHARGING_POWER, power_to_register(allocated_kw))
#
# Registradores chave para este módulo:
#   Leitura:  HCA_G2.READ.CHARGING_POWER (10015)  → potência atual do carregador
#             HCA_G2.READ.CHARGER_STATUS (10017)   → estado atual
#             HCA_G2.READ.CAR_CONN_STATUS (10075)  → carro conectado?
#   Escrita:  HCA_G2.WRITE.MAX_CHARGING_POWER (10029) ★ redistribuição de potência
#             HCA_G2.WRITE.EMS_DISPATCH (10000)       → throttle de emergência
try:
    from core.modbus_map import HCA_G2, power_to_register, ChargerStatus
    MODBUS_MAP_AVAILABLE = True
except ImportError:
    MODBUS_MAP_AVAILABLE = False

logger = logging.getLogger("chargegrid.controller")


@dataclass
class ChargingSession:
    """Representa uma sessão de recarga ativa."""
    charger_id: str
    vehicle_id: str
    started_at: datetime
    allocated_kw: float = 0.0
    energy_consumed_kwh: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)

    def duration_minutes(self) -> float:
        return (datetime.now() - self.started_at).total_seconds() / 60

    def update_energy(self, interval_seconds: float = 1.0):
        """Acumula energia consumida com base na potência alocada."""
        self.energy_consumed_kwh += (self.allocated_kw * interval_seconds) / 3600
        self.last_updated = datetime.now()


@dataclass
class Charger:
    """Representa um ponto de recarga físico."""
    charger_id: str
    max_kw: float          # Potência máxima suportada pelo hardware
    location: str          # Ex: "Vaga A1", "Entrada Principal"
    session: Optional[ChargingSession] = None

    @property
    def is_occupied(self) -> bool:
        return self.session is not None

    @property
    def current_kw(self) -> float:
        return self.session.allocated_kw if self.session else 0.0


class DemandController:
    """
    Controlador central de demanda.

    Recebe eventos (conexão/desconexão de veículos) e redistribui
    a potência disponível entre os carregadores ativos, respeitando
    o limite contratado com a distribuidora.

    Desacoplado da interface — pode ser usado por terminal, API REST,
    ou qualquer camada de apresentação futura.
    """

    def __init__(self, site_name: str, contracted_limit_kw: float, safety_margin_pct: float = 0.05):
        """
        Args:
            site_name: Nome do estabelecimento comercial.
            contracted_limit_kw: Demanda contratada com a distribuidora (kW).
            safety_margin_pct: Margem de segurança (padrão 5%) para não ultrapassar o limite.
        """
        self.site_name = site_name
        self.contracted_limit_kw = contracted_limit_kw
        self.safety_margin_pct = safety_margin_pct
        self.chargers: dict[str, Charger] = {}
        self.completed_sessions: list[ChargingSession] = []
        self.events: list[dict] = []  # Log de eventos para futura integração com frontend

    # ------------------------------------------------------------------
    # Propriedades calculadas
    # ------------------------------------------------------------------

    @property
    def usable_limit_kw(self) -> float:
        """Potência máxima utilizável (limite contratado menos margem de segurança)."""
        return self.contracted_limit_kw * (1 - self.safety_margin_pct)

    @property
    def active_sessions(self) -> list[ChargingSession]:
        return [c.session for c in self.chargers.values() if c.session]

    @property
    def total_allocated_kw(self) -> float:
        return sum(s.allocated_kw for s in self.active_sessions)

    @property
    def available_kw(self) -> float:
        return max(0.0, self.usable_limit_kw - self.total_allocated_kw)

    @property
    def utilization_pct(self) -> float:
        if self.contracted_limit_kw == 0:
            return 0.0
        return (self.total_allocated_kw / self.contracted_limit_kw) * 100

    # ------------------------------------------------------------------
    # Gerenciamento de carregadores
    # ------------------------------------------------------------------

    def register_charger(self, charger_id: str, max_kw: float, location: str):
        """Registra um novo ponto de recarga na rede."""
        self.chargers[charger_id] = Charger(charger_id, max_kw, location)
        self._log_event("CHARGER_REGISTERED", charger_id, f"Registrado em '{location}' | Máx: {max_kw} kW")

    # ------------------------------------------------------------------
    # Eventos de sessão
    # ------------------------------------------------------------------

    def vehicle_connect(self, charger_id: str, vehicle_id: str) -> dict:
        """
        Processa a conexão de um veículo.
        Redistribui a potência entre todos os carregadores ativos.
        Retorna o estado resultante para a interface exibir.
        """
        charger = self._get_charger(charger_id)

        if charger.is_occupied:
            return self._error(f"Carregador {charger_id} já está ocupado.")

        session = ChargingSession(
            charger_id=charger_id,
            vehicle_id=vehicle_id,
            started_at=datetime.now(),
        )
        charger.session = session
        self._log_event("VEHICLE_CONNECTED", charger_id, f"Veículo {vehicle_id} conectado")

        self._redistribute_power()
        return self._state_snapshot()

    def vehicle_disconnect(self, charger_id: str) -> dict:
        """
        Processa a desconexão de um veículo.
        Libera a potência e a redistribui para os demais carregadores ativos.
        """
        charger = self._get_charger(charger_id)

        if not charger.is_occupied:
            return self._error(f"Carregador {charger_id} não tem sessão ativa.")

        session = charger.session
        session.update_energy()
        self.completed_sessions.append(session)
        charger.session = None

        self._log_event(
            "VEHICLE_DISCONNECTED", charger_id,
            f"Veículo {session.vehicle_id} desconectado | "
            f"{session.energy_consumed_kwh:.3f} kWh consumidos | "
            f"{session.duration_minutes():.1f} min"
        )

        self._redistribute_power()
        return self._state_snapshot()

    # ------------------------------------------------------------------
    # Algoritmo de redistribuição de potência (coração do sistema)
    # ------------------------------------------------------------------

    def _redistribute_power(self):
        """
        Redistribui a potência disponível entre as sessões ativas.

        Algoritmo iterativo:
        1. Divide o orçamento igualmente entre os carregadores pendentes
        2. Carregadores que atingem seu teto de hardware ficam fixos
        3. A sobra é redistribuída para os demais — repete até estabilizar

        Exemplo com 95 kW, 1 sessão em carregador de 22 kW:
          share = 95 / 1 = 95 kW → teto 22 kW atingido → aloca 22 kW
          (limite do hardware, não do orçamento)

        Exemplo com 95 kW, 3 sessões (22 kW, 11 kW, 11 kW):
          share = 95 / 3 = 31,7 → todos atingem teto
          Total: 22 + 11 + 11 = 44 kW — correto, hardware esgotado

        Exemplo com 95 kW, 1 sessão em carregador de 50 kW:
          share = 95 / 1 = 95 kW → teto 50 kW atingido → aloca 50 kW

        Extensível para: prioridade por SOC, tempo de chegada, tarifa etc.
        """
        sessions = self.active_sessions
        if not sessions:
            return

        sessions_sorted = sorted(sessions, key=lambda s: s.started_at)
        pending = {s.charger_id: s for s in sessions_sorted}
        remaining_kw = self.usable_limit_kw

        while pending:
            share = remaining_kw / len(pending)
            newly_fixed = {
                cid: s for cid, s in pending.items()
                if self.chargers[cid].max_kw <= share
            }

            if not newly_fixed:
                # Nenhum teto atingido — distribui share igualmente para os pendentes
                # Arredonda individualmente e corrige o último para evitar desvio de ponto flutuante
                pending_list = list(pending.items())
                allocated_so_far = 0.0
                for idx, (charger_id, session) in enumerate(pending_list):
                    if idx < len(pending_list) - 1:
                        val = round(share, 2)
                        session.allocated_kw = val
                        allocated_so_far += val
                    else:
                        # Último recebe o que sobrar exatamente
                        session.allocated_kw = round(remaining_kw - allocated_so_far, 2)
                break

            # Fixa os que atingiram o teto e redistribui a sobra
            for charger_id, session in newly_fixed.items():
                session.allocated_kw = round(self.chargers[charger_id].max_kw, 2)
                remaining_kw -= self.chargers[charger_id].max_kw
                del pending[charger_id]

        n = len(sessions_sorted)

        self._log_event(
            "POWER_REDISTRIBUTED", "SYSTEM",
            f"{n} carregador(es) ativo(s) | "
            f"Total alocado: {self.total_allocated_kw:.1f} kW / {self.usable_limit_kw:.1f} kW disponíveis"
        )

    # ------------------------------------------------------------------
    # Snapshot de estado (contrato com a interface)
    # ------------------------------------------------------------------

    def _state_snapshot(self) -> dict:
        """
        Retorna o estado completo do sistema como dicionário.
        Este é o contrato entre a lógica e qualquer interface (terminal, API, frontend).
        """
        return {
            "site": self.site_name,
            "contracted_limit_kw": self.contracted_limit_kw,
            "usable_limit_kw": round(self.usable_limit_kw, 2),
            "total_allocated_kw": round(self.total_allocated_kw, 2),
            "available_kw": round(self.available_kw, 2),
            "utilization_pct": round(self.utilization_pct, 1),
            "active_sessions": len(self.active_sessions),
            "chargers": [
                {
                    "id": c.charger_id,
                    "location": c.location,
                    "max_kw": c.max_kw,
                    "status": "OCUPADO" if c.is_occupied else "LIVRE",
                    "allocated_kw": c.current_kw,
                    "vehicle_id": c.session.vehicle_id if c.session else None,
                    "duration_min": round(c.session.duration_minutes(), 1) if c.session else None,
                    "energy_kwh": round(c.session.energy_consumed_kwh, 3) if c.session else None,
                }
                for c in self.chargers.values()
            ],
            "events": self.events[-5:],  # Últimos 5 eventos para display
        }

    # ------------------------------------------------------------------
    # Utilidades internas
    # ------------------------------------------------------------------

    def _get_charger(self, charger_id: str) -> Charger:
        if charger_id not in self.chargers:
            raise ValueError(f"Carregador '{charger_id}' não encontrado na rede.")
        return self.chargers[charger_id]

    def _log_event(self, event_type: str, source: str, message: str):
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "source": source,
            "message": message,
        }
        self.events.append(entry)
        logger.info(f"[{event_type}] {source}: {message}")

    def _error(self, message: str) -> dict:
        self._log_event("ERROR", "SYSTEM", message)
        return {"error": message}

    # ------------------------------------------------------------------
    # Integração com hardware real (Sprint 3+)
    # ------------------------------------------------------------------

    def _write_power_to_hardware(self, charger_id: str, allocated_kw: float):
        """
        [SPRINT 3+] Escreve o limite de potência no carregador físico via Modbus TCP.

        Na Sprint 2 este método não é chamado — a simulação é interna.
        Na integração real, chamar após cada _redistribute_power().

        Registrador alvo: HCA_G2.WRITE.MAX_CHARGING_POWER (10029)
        Gain: valor_escrito = kW × 10  (ex: 15.5 kW → escrever 155)

        Faixas válidas por modelo:
            7kW  mono:  1.4 – 7.0 kW
            11kW tri:   4.2 – 11.0 kW
            22kW tri:   4.2 – 22.0 kW

        Implementação real (pymodbus):
            from pymodbus.client import ModbusTcpClient
            from core.modbus_map import HCA_G2, power_to_register
            client = ModbusTcpClient(charger_ip, port=HCA_G2.MODBUS_TCP_PORT)
            if client.connect():
                client.write_register(
                    HCA_G2.WRITE.MAX_CHARGING_POWER,
                    power_to_register(allocated_kw)
                )
                client.close()
        """
        if MODBUS_MAP_AVAILABLE:
            register_value = power_to_register(allocated_kw)
            self._log_event(
                "MODBUS_WRITE_SIMULATED", charger_id,
                f"[SIMULADO] Reg {HCA_G2.WRITE.MAX_CHARGING_POWER} "
                f"← {register_value} ({allocated_kw} kW)"
            )

    def _read_power_from_hardware(self, charger_id: str) -> float:
        """
        [SPRINT 3+] Lê a potência atual do carregador físico via Modbus TCP.

        Registrador alvo: HCA_G2.READ.CHARGING_POWER (10015)
        Gain: kW = valor_lido / 10

        Implementação real (pymodbus):
            from pymodbus.client import ModbusTcpClient
            from core.modbus_map import HCA_G2, register_to_power
            client = ModbusTcpClient(charger_ip, port=HCA_G2.MODBUS_TCP_PORT)
            if client.connect():
                result = client.read_holding_registers(
                    HCA_G2.READ.CHARGING_POWER, count=1
                )
                return register_to_power(result.registers[0])
        """
        charger = self.chargers.get(charger_id)
        return charger.current_kw if charger else 0.0
