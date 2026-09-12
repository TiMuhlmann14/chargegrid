"""
ChargeGrid Intelligence — Mapa de Registradores MODBUS
=======================================================
Mapa oficial de registradores do carregador GoodWe HCA G2
Fonte: Mapa_MODBUS_HCA_G2.pdf — Protocolo v1.0.15

Protocolo: Modbus TCP
Baud rate: 9600 | Byte size: 8 | Stop bits: 1 | Parity: N

Como usar:
    from core.modbus_map import HCA_G2, decode_status, decode_power_source

Na simulação atual (Sprint 2), esses endereços são referência documental.
Na integração real (Sprint 3+), usar com biblioteca pymodbus:

    from pymodbus.client import ModbusTcpClient
    client = ModbusTcpClient(host="192.168.1.x", port=502)
    result = client.read_holding_registers(HCA_G2.READ.CHARGING_POWER, count=1)
    power_kw = result.registers[0] / 10  # gain = 10
"""


class _ReadRegisters:
    """Registradores somente leitura (RO) — o sistema lê o estado do carregador."""

    # ── Falhas e alertas ──────────────────────────────────────────────
    AC_FAULT_01         = 10001  # bit0=emergência, bit1=sobretensão, bit2=sobrecorrente...
    AC_FAULT_02         = 10002  # bit0=acesso, bit1=aterramento, bit2=timeout handshake...
    AC_FAULT_03         = 10003  # bit0=curto, bit1=fuga corrente, bit4=offline PV/bat...
    AC_FAULT_04         = 10004  # reservado
    AC_ALARM_05         = 10005  # bit0=gun overtemp, bit6=stop alarm, bit7=meter anomaly
    AC_ALARM_06         = 10006  # bit0=env overtemp alarm
    HW_FAULT_07         = 10007  # bit0=flash, bit1=eeprom, bit4=SN não registrado...
    HW_FAULT_08         = 10008  # reservado

    # ── Medição elétrica em tempo real ────────────────────────────────
    VOLT_PHASE_A        = 10009  # Tensão fase A (gain: /10 → V)
    VOLT_PHASE_B        = 10010  # Tensão fase B (gain: /10 → V)
    VOLT_PHASE_C        = 10011  # Tensão fase C (gain: /10 → V)
    CURRENT_PHASE_A     = 10012  # Corrente fase A (gain: /10 → A)
    CURRENT_PHASE_B     = 10013  # Corrente fase B (gain: /10 → A)
    CURRENT_PHASE_C     = 10014  # Corrente fase C (gain: /10 → A)

    # ── Potência e energia da sessão ──────────────────────────────────
    CHARGING_POWER      = 10015  # Potência atual (gain: /10 → kW) ★ BASE DO CONTROLE DE DEMANDA
    CHARGING_CAPACITY   = 10016  # Energia consumida na sessão (gain: /10 → kWh)
    CHARGING_DURATION   = 10063  # Duração da sessão (U32, 2 regs, em segundos)
    ACCUMULATED_ENERGY  = 10065  # Energia histórica acumulada (U32, 2 regs, gain: /10 → kWh)

    # ── Estado do carregador ──────────────────────────────────────────
    CHARGER_STATUS      = 10017  # Estado atual — ver ChargerStatus abaixo
    COMM_STATUS         = 10018  # Bit-0=WiFi, Bit-1=IoT, Bit-2=inversor, Bit-3=MID, Bit-5=EMS
    CAR_CONN_STATUS     = 10075  # 0=desconectado, 1=semi-conectado, 2=conectado
    CP_VOLTAGE_STATE    = 10084  # ConnectNoV=0, 12V=1, 9V=2, 6V=3, 3V=4
    CHARGE_START_MODE   = 10076  # 0=RFID, 1=backend, 5=plug&charge, 6=agendado...
    CHARGE_STRATEGY     = 10077  # 0=auto, 1=por tempo, 2=por valor, 3=por kWh

    # ── Fonte de energia (sustentabilidade) ──────────────────────────
    CHARGING_POWER_SOURCE = 10108  # bit0=rede, bit1=PV, bit2=bateria — combináveis
    GREEN_ENERGY          = 10103  # Energia renovável consumida na sessão (U32, gain: /10 → kWh)
    GRID_ENERGY           = 10105  # Energia comprada da rede (U32, gain: /10 → kWh)

    # ── Dados de faturamento da sessão ────────────────────────────────
    SESSION_AMOUNT        = 10061  # Valor cobrado na sessão (U32, gain: /100)
    SESSION_START_YM      = 10158  # Início: alto=ano, baixo=mês
    SESSION_START_DH      = 10159  # Início: alto=dia, baixo=hora
    SESSION_START_MS      = 10160  # Início: alto=minuto, baixo=segundo
    SESSION_END_YM        = 10162  # Fim: alto=ano, baixo=mês
    SESSION_END_DH        = 10163  # Fim: alto=dia, baixo=hora
    SESSION_END_MS        = 10164  # Fim: alto=minuto, baixo=segundo
    SESSION_DURATION_S    = 10166  # Duração da sessão (U32, em segundos)
    SESSION_END_REASON    = 10168  # Motivo de encerramento (U32)
    METER_BEFORE          = 10170  # Leitura do medidor antes da sessão (U32, 0.01 kWh)
    METER_AFTER           = 10172  # Leitura do medidor após a sessão (U32, 0.01 kWh)
    SESSION_INDEX         = 10174  # Índice da sessão atual no histórico

    # ── Identificação do hardware ─────────────────────────────────────
    SN_NUMBER             = 10040  # Número de série (STR, 8 regs, ASCII)
    SW_VERSION_EXT        = 10048  # Versão de software (STR, 2 regs)
    HW_VERSION            = 10056  # Versão de hardware (STR, 2 regs)
    POWER_SPEC            = 10058  # 0=7kW, 1=11kW, 2=22kW
    CHARGER_TYPE          = 10059  # 0=trifásico, 1=monofásico
    CHARGER_PROJECT_TYPE  = 10107  # 0/1=DC, 2=AC

    # ── Alarmes IoT ───────────────────────────────────────────────────
    IOT_ALARM_BASE        = 30000  # 30000–30015: alarmes IoT (big-endian)


class _WriteRegisters:
    """Registradores de escrita (WO/RW) — o sistema CONTROLA o carregador."""

    # ── Controle de demanda ★ CORE DO CHARGEGRID ──────────────────────
    MAX_CHARGING_POWER    = 10029
    """
    ★ REGISTRADOR PRINCIPAL DO CONTROLE DE DEMANDA
    
    Define o limite máximo de potência do carregador.
    Tipo: RW U16 | Gain: /10 → kW | Salvo em flash: SIM
    
    Faixas por modelo:
        7kW  monofásico: 1,4 kW – 7,0 kW   (valores: 14 – 70)
        11kW trifásico:  4,2 kW – 11,0 kW  (valores: 42 – 110)
        22kW trifásico:  4,2 kW – 22,0 kW  (valores: 42 – 220)
    
    Uso no DemandController:
        Após _redistribute_power(), escrever session.allocated_kw * 10
        neste registrador de cada carregador ativo via Modbus TCP.
    
    Exemplo pymodbus:
        client.write_register(HCA_G2.WRITE.MAX_CHARGING_POWER, int(allocated_kw * 10))
    """

    EMS_DISPATCH          = 10000
    """
    Despacho de energia EMS.
    0 = operação normal
    1 = forçar potência mínima (throttle total)
    Usar em emergência quando demanda ultrapassar limite contratado.
    """

    DLM_ENABLE            = 10025
    """
    Dynamic Load Management nativo do carregador.
    0 = desligado | 1 = ligado
    Quando o ChargeGrid assume o controle, manter em 0 para evitar conflito
    com o DLM interno. O sistema de vocês é o DLM externo centralizado.
    """

    CHARGER_ON_OFF        = 10060  # 1=desligar, 2=ligar carregamento
    PLUG_AND_CHARGE       = 10019  # 0=off, 1=on — habilita início automático ao conectar
    RESERVATION_STATUS    = 10020  # 0=inativo, 1=uso único, 2=permanente
    RESERVATION_START     = 10021  # Horário de início agendado (hex: 0x0C1E = 12:30)
    RESERVATION_DURATION  = 10022  # Duração do agendamento em minutos

    ADVANCED_CHARGE_MODE  = 10032
    """
    Modo de carregamento avançado:
    0 = Rápido (usa rede elétrica prioritariamente)
    1 = PV (usa apenas energia solar)
    2 = PV + Bateria (usa renovável prioritariamente)
    
    O ChargeGrid pode selecionar o modo com base na disponibilidade
    de geração solar no momento da sessão.
    """

    MAX_GRID_POWER        = 10039  # Limite de compra da rede (kW × 10) — demand response
    HOUSEHOLD_BREAKER     = 10026  # Corrente nominal do disjuntor de entrada (A)
    CHARGER_TIME_YM       = 10071  # Sincronizar relógio: ano/mês
    CHARGER_TIME_DH       = 10072  # Sincronizar relógio: dia/hora
    CHARGER_TIME_MS       = 10073  # Sincronizar relógio: minuto/segundo
    TRANSPARENT_MODE      = 10157  # 1=gateway, 0=IoT direto


class HCA_G2:
    """
    Namespace central do mapa MODBUS do GoodWe HCA G2.

    Uso:
        HCA_G2.READ.CHARGING_POWER    → 10015
        HCA_G2.WRITE.MAX_CHARGING_POWER → 10029
    """
    READ  = _ReadRegisters
    WRITE = _WriteRegisters

    # Configuração de comunicação
    MODBUS_TCP_PORT     = 502
    BAUD_RATE           = 9600
    BYTE_SIZE           = 8
    STOP_BITS           = 1
    PARITY              = "N"

    # Limites de potência por modelo (kW)
    POWER_LIMITS = {
        "7kW":  {"min": 1.4, "max": 7.0},
        "11kW": {"min": 4.2, "max": 11.0},
        "22kW": {"min": 4.2, "max": 22.0},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Decoders — traduzem valores brutos do hardware em linguagem do sistema
# ──────────────────────────────────────────────────────────────────────────────

class ChargerStatus:
    """Estados do registrador 10017 — Charging Station Status."""
    IDLE_NO_GUN         = 0   # Livre, sem cabo conectado
    IDLE_GUN_PLUGGED    = 1   # Livre, cabo conectado
    HANDSHAKING         = 2   # Negociando com o veículo
    CHARGING            = 3   # Carregando ★
    CHARGE_COMPLETE     = 4   # Carregamento concluído
    ALARM               = 5   # Falha/alarme ativo
    SCHEDULED_START     = 6   # Aguardando início agendado
    MAINTENANCE         = 7   # Em manutenção
    START_FAILED        = 8   # Falha ao iniciar
    UPGRADING           = 9   # Atualizando firmware
    CHARGING_INTERRUPTED = 10 # Interrompido por falta de PV/bateria

    LABELS = {
        0: "LIVRE (sem cabo)",
        1: "LIVRE (cabo conectado)",
        2: "Handshake com veículo",
        3: "CARREGANDO",
        4: "Carregamento completo",
        5: "ALARME",
        6: "Aguardando agendamento",
        7: "Manutenção",
        8: "Falha na inicialização",
        9: "Atualizando firmware",
        10: "Interrompido (PV/bat insuficiente)",
    }

    @staticmethod
    def label(code: int) -> str:
        return ChargerStatus.LABELS.get(code, f"Desconhecido ({code})")

    @staticmethod
    def is_charging(code: int) -> bool:
        return code == ChargerStatus.CHARGING

    @staticmethod
    def is_available(code: int) -> bool:
        return code in (ChargerStatus.IDLE_NO_GUN, ChargerStatus.IDLE_GUN_PLUGGED)


def decode_power_source(register_value: int) -> dict:
    """
    Decodifica o registrador 10108 (Charging Power Source).
    
    Retorna dicionário com as fontes ativas.
    Exemplo: 0b00000101 → grid=True, pv=False, battery=True
    
    Args:
        register_value: Valor bruto do registrador 10108
    
    Returns:
        dict com keys: grid, pv, battery, label
    
    Uso no ChargeGrid:
        Quando pv=True, registrar a sessão como "energia renovável"
        e aplicar tarifa diferenciada (mais barata) se configurado.
    """
    sources = {
        "grid":    bool(register_value & 0b001),
        "pv":      bool(register_value & 0b010),
        "battery": bool(register_value & 0b100),
    }
    active = [k.upper() for k, v in sources.items() if v]
    sources["label"] = " + ".join(active) if active else "Nenhuma"
    return sources


def decode_comm_status(register_value: int) -> dict:
    """
    Decodifica o registrador 10018 (Communication Connection Status).
    
    Args:
        register_value: Valor bruto do registrador 10018
    
    Returns:
        dict com status de cada canal de comunicação
    """
    return {
        "wifi":     bool(register_value & (1 << 0)),
        "iot":      bool(register_value & (1 << 1)),
        "inverter": bool(register_value & (1 << 2)),
        "mid_meter":bool(register_value & (1 << 3)),
        "gw_meter": bool(register_value & (1 << 4)),
        "ems":      bool(register_value & (1 << 5)),
    }


def decode_fault_byte(register_value: int, fault_map: dict) -> list[str]:
    """
    Decodifica qualquer byte de falha/alarme do HCA G2.
    
    Args:
        register_value: Valor do registrador de falha
        fault_map: Dicionário {bit_index: "descrição"}
    
    Returns:
        Lista de falhas ativas
    
    Exemplo:
        FAULT_MAP_01 = {
            0: "Emergência", 1: "Sobretensão", 2: "Sobrecorrente",
            3: "Subtensão", 4: "Falha no conector", 7: "Gun overtemp"
        }
        faults = decode_fault_byte(0b00000101, FAULT_MAP_01)
        # → ["Emergência", "Sobrecorrente"]
    """
    return [desc for bit, desc in fault_map.items() if register_value & (1 << bit)]


def power_to_register(kw: float) -> int:
    """
    Converte potência em kW para o valor a ser escrito no registrador 10029.
    O gain do registrador é 10 (valor = kW × 10).
    
    Args:
        kw: Potência desejada em kW
    
    Returns:
        Valor inteiro para escrever no registrador
    
    Exemplo:
        power_to_register(15.5) → 155
    """
    return int(round(kw * 10))


def register_to_power(register_value: int) -> float:
    """
    Converte valor bruto do registrador de potência para kW.
    
    Args:
        register_value: Valor lido do registrador
    
    Returns:
        Potência em kW (float)
    
    Exemplo:
        register_to_power(155) → 15.5
    """
    return register_value / 10.0


# ──────────────────────────────────────────────────────────────────────────────
# Mapas de falhas por registrador (para uso com decode_fault_byte)
# ──────────────────────────────────────────────────────────────────────────────

FAULT_MAP_10001 = {
    0: "Parada de emergência",
    1: "Sobretensão",
    2: "Sobrecorrente",
    3: "Subtensão",
    4: "Falha no conector",
    5: "S2 desconectado",
    6: "Sobretemperatura ambiente",
    7: "Sobretemperatura da pistola",
}

FAULT_MAP_10002 = {
    0: "Falha no controle de acesso",
    1: "Falha de aterramento",
    2: "Timeout de handshake",
    3: "Falha comunicação cartão RF",
    4: "Falha comunicação display serial",
    5: "Falha comunicação IC medidor",
    6: "Falha relé de saída",
    7: "Falha trava da pistola",
}

FAULT_MAP_10003 = {
    0: "Curto-circuito na saída",
    1: "Fuga de corrente",
    2: "Pausa de carregamento >10 min",
    3: "Leitura anormal do medidor",
    4: "Carregador offline ao iniciar por PV/bat",
    5: "Potência insuficiente ao iniciar por PV/bat",
}

FAULT_MAP_10007 = {
    0: "Falha flash externo",
    1: "Falha EEPROM",
    2: "Falha detector de fuga",
    3: "Alimentação de entrada anormal",
    4: "SN não registrado",
    5: "Parâmetros de fábrica anormais",
    6: "Firmware não autorizado",
}
