/* ChargeGrid Intelligence — app.js
 * Único JS "manual" do projeto: conecta ao WebSocket e anima o
 * diagrama unifilar / anel de energia (SVG), como definido no
 * DESIGN_SYSTEM.md. Toda a lógica de negócio vive no servidor;
 * este arquivo só reflete o estado recebido na UI.
 */
(function () {
  "use strict";

  const BUS_TRACK_X = 40;
  const BUS_TRACK_W = 1080;
  const RING_R = 90;
  const RING_CIRCUMFERENCE = 2 * Math.PI * RING_R;

  let cfg = { page: null, chargerId: null };
  let socket = null;
  let detailsOpenId = null;

  function $(id) { return document.getElementById(id); }

  function pad2(n) { return String(Math.floor(n)).padStart(2, "0"); }

  function formatBRL(value) {
    const s = value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return "R$ " + s;
  }

  function formatHMS(minutes) {
    const totalSeconds = Math.max(0, Math.round((minutes || 0) * 60));
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return `${pad2(h)}:${pad2(m)}:${pad2(s)}`;
  }

  // ------------------------------------------------------------------
  // Count-up de números — em vez de o texto trocar instantaneamente,
  // anima do valor atual exibido até o novo (~500ms, ease-out cúbico).
  // Sem biblioteca externa, só requestAnimationFrame.
  // ------------------------------------------------------------------

  const numberAnimState = new Map(); // elementId -> { value, raf }

  function animateNumberTo(id, targetValue, formatFn) {
    const el = $(id);
    if (!el) return;
    const prevState = numberAnimState.get(id);
    const from = prevState ? prevState.value : targetValue;
    if (prevState && prevState.raf) cancelAnimationFrame(prevState.raf);

    if (from === targetValue) {
      el.textContent = formatFn(targetValue);
      numberAnimState.set(id, { value: targetValue, raf: null });
      return;
    }

    const duration = 500;
    const start = performance.now();

    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = from + (targetValue - from) * eased;
      el.textContent = formatFn(current);
      if (t < 1) {
        numberAnimState.set(id, { value: targetValue, raf: requestAnimationFrame(tick) });
      } else {
        el.textContent = formatFn(targetValue);
        numberAnimState.set(id, { value: targetValue, raf: null });
      }
    }

    numberAnimState.set(id, { value: targetValue, raf: requestAnimationFrame(tick) });
  }

  function setStaticText(id, text) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    numberAnimState.delete(id);
  }

  // ------------------------------------------------------------------
  // WebSocket
  // ------------------------------------------------------------------

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws`);

    socket.addEventListener("message", (evt) => {
      let state;
      try { state = JSON.parse(evt.data); } catch (e) { return; }
      if (cfg.page === "dashboard") renderDashboard(state);
      if (cfg.page === "cliente") renderCliente(state);
    });

    socket.addEventListener("close", () => {
      setTimeout(connect, 1500);
    });
    socket.addEventListener("error", () => socket.close());
  }

  // ------------------------------------------------------------------
  // Dashboard de Gestão
  // ------------------------------------------------------------------

  function renderDashboard(state) {
    if ($("clock")) $("clock").textContent = state.clock;

    if ($("bus-fill")) {
      const frac = Math.min(1, state.utilization_pct / 100);
      $("bus-fill").setAttribute("width", (BUS_TRACK_W * frac).toFixed(1));
      $("bus-fill").setAttribute("class", "bus-fill " + levelClass(state.utilization_level));
    }
    animateNumberTo("bus-allocated", state.total_allocated_kw, (v) => v.toFixed(1));
    animateNumberTo("bus-pct", state.utilization_pct, (v) => v.toFixed(1) + "%");

    const summary = $("unifilar-summary");
    if (summary) summary.className = "unifilar-summary " + levelClass(state.utilization_level);

    const badge = $("bus-level-badge");
    if (badge) {
      if (state.utilization_level === "critical") {
        badge.textContent = "⚠ acima do limite contratado";
        badge.classList.remove("hidden");
      } else if (state.utilization_level === "warning") {
        badge.textContent = "⚠ utilização ≥ 90%";
        badge.classList.remove("hidden");
      } else {
        badge.textContent = "";
        badge.classList.add("hidden");
      }
    }

    state.chargers.forEach((c) => {
      const occupied = c.status === "OCUPADO";
      const flag = c.sim_flag;
      const nodeClass = flag === "erro" ? "fault pulse" : flag === "manutencao" ? "maintenance" : occupied ? "occupied pulse" : "free";

      const node = $("node-" + c.id);
      if (node) node.setAttribute("class", "node " + nodeClass);

      const branch = $("branch-" + c.id);
      if (branch) branch.setAttribute("class", "branch" + (occupied ? " energized" : ""));

      if (flag === "erro") setStaticText("node-kw-" + c.id, "ERRO");
      else if (flag === "manutencao") setStaticText("node-kw-" + c.id, "MANUT.");
      else animateNumberTo("node-kw-" + c.id, c.allocated_kw, (v) => v.toFixed(1) + " kW");

      const pill = $("pill-" + c.id);
      if (pill) {
        pill.className = "status-pill " + (occupied ? "ocupado" : "livre");
        pill.textContent = c.status;
      }

      const flagBadge = $("flag-" + c.id);
      if (flagBadge) {
        if (flag === "manutencao") {
          flagBadge.className = "flag-badge manutencao";
          flagBadge.textContent = "🔧 Manutenção";
        } else if (flag === "erro") {
          flagBadge.className = "flag-badge erro";
          flagBadge.textContent = "⚠ Erro";
        } else {
          flagBadge.className = "flag-badge hidden";
          flagBadge.textContent = "";
        }
      }

      const vehicle = $("vehicle-" + c.id);
      if (vehicle) vehicle.textContent = c.vehicle_id || "—";

      animateNumberTo("kw-" + c.id, c.allocated_kw, (v) => v.toFixed(2));
    });

    renderEvents(state.events);
    renderBilling(state.billing);

    if (detailsOpenId && window.htmx) {
      htmx.ajax("GET", `/charger/${detailsOpenId}/details`, { target: "#details-panel", swap: "innerHTML" });
    }
  }

  function levelClass(level) {
    return level === "critical" ? "level-critical" : level === "warning" ? "level-warning" : "";
  }

  function renderEvents(events) {
    const list = $("events-list");
    if (!list || !events) return;
    list.innerHTML = events.map((e) =>
      `<li class="type-${e.type}"><span class="ts">[${e.timestamp}]</span> ${e.type} · ${e.source} — ${e.message}</li>`
    ).join("");
    const console_ = $("events-console");
    if (console_) console_.scrollTop = console_.scrollHeight;
  }

  function renderBilling(billing) {
    if (!billing) return;
    animateNumberTo("billing-kwh", billing.kwh_today, (v) => v.toFixed(3) + " kWh");
    animateNumberTo("billing-revenue", billing.revenue_today_rs, formatBRL);
    animateNumberTo("billing-sessions", billing.sessions_today, (v) => String(Math.round(v)));

    if (billing.avg_ticket_rs != null) animateNumberTo("billing-avg-ticket", billing.avg_ticket_rs, formatBRL);
    else setStaticText("billing-avg-ticket", "—");

    if (billing.avg_duration_min != null) animateNumberTo("billing-avg-duration", billing.avg_duration_min, (v) => v.toFixed(1) + " min");
    else setStaticText("billing-avg-duration", "—");

    animateNumberTo("billing-completed-count", billing.completed_sessions_count, (v) => String(Math.round(v)));
  }

  function showDetails(chargerId) {
    detailsOpenId = chargerId;
    const panel = $("details-panel");
    const backdrop = $("details-backdrop");
    const widget = $("chat-widget");
    if (panel) panel.classList.add("open");
    if (backdrop) backdrop.classList.add("open");
    // Drawer e widget do chat são ambos ancorados no canto direito —
    // o widget se retrai enquanto o drawer está aberto pra não colidir.
    if (widget) widget.classList.add("suppressed");
    if (window.htmx) {
      htmx.ajax("GET", `/charger/${chargerId}/details`, { target: "#details-panel", swap: "innerHTML" });
    }
  }

  function closeDetails() {
    detailsOpenId = null;
    const panel = $("details-panel");
    const backdrop = $("details-backdrop");
    const widget = $("chat-widget");
    if (panel) panel.classList.remove("open");
    if (backdrop) backdrop.classList.remove("open");
    if (widget) widget.classList.remove("suppressed");
  }

  // ------------------------------------------------------------------
  // Widget flutuante do Assistente IA (disponível em qualquer tela)
  // ------------------------------------------------------------------

  function toggleChat(forceOpen) {
    const fab = $("chat-fab");
    const panel = $("chat-panel");
    if (!fab || !panel) return;
    const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : !panel.classList.contains("open");
    panel.classList.toggle("open", shouldOpen);
    fab.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) {
      const focusTarget = panel.querySelector(".chat-menu button");
      if (focusTarget) focusTarget.focus();
    }
  }

  document.addEventListener("keydown", (evt) => {
    if (evt.key !== "Escape") return;
    if (detailsOpenId) closeDetails();
    toggleChat(false);
  });

  // ------------------------------------------------------------------
  // App do Cliente
  // ------------------------------------------------------------------

  function renderCliente(state) {
    const c = state.chargers.find((x) => x.id === cfg.chargerId);
    if (!c) return;
    const occupied = c.status === "OCUPADO";
    const maxKw = parseFloat(document.body.dataset.maxKw) || c.max_kw || 1;
    const tariff = parseFloat(document.body.dataset.tariff) || 4.26;
    const targetKwh = parseFloat(document.body.dataset.targetKwh);

    // Tela autenticada de "carregando" (data-target-kwh presente): se a vaga
    // deixou de estar ocupada (ex.: operador forçou parada pelo Dashboard),
    // não há mais nada pra mostrar aqui — volta pro servidor, que decide
    // pra onde mandar o cliente (home).
    if (!Number.isNaN(targetKwh) && !occupied) {
      location.href = "/cliente/carregando";
      return;
    }

    const frac = maxKw ? Math.min(1, c.allocated_kw / maxKw) : 0;
    const ring = $("ring-fill");
    if (ring) ring.setAttribute("stroke-dashoffset", (RING_CIRCUMFERENCE * (1 - frac)).toFixed(2));

    animateNumberTo("ring-kw", c.allocated_kw, (v) => v.toFixed(2));

    const energy = c.energy_kwh || 0;
    animateNumberTo("stat-energy", energy, (v) => v.toFixed(3) + " kWh");
    if ($("stat-time")) $("stat-time").textContent = formatHMS(c.duration_min || 0);
    animateNumberTo("stat-cost", energy * tariff, formatBRL);

    if ($("stat-remaining")) {
      const remainingKwh = Math.max(0, targetKwh - energy);
      if (c.allocated_kw > 0) {
        const remainingMin = (remainingKwh / c.allocated_kw) * 60;
        $("stat-remaining").textContent = "~" + Math.round(remainingMin) + " min";
      } else {
        $("stat-remaining").textContent = "—";
      }
    }

    const statusEl = $("cliente-status");
    if (statusEl) {
      statusEl.className = "cliente-status " + (occupied ? "ocupado" : "livre");
      statusEl.textContent = occupied ? (targetKwh ? "Carregando" : "Recarga em andamento") : "Vaga livre · aguardando veículo";
    }
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------

  window.ChargeGrid = {
    init(options) {
      cfg = Object.assign(cfg, options);
      const fab = $("chat-fab");
      if (fab) fab.addEventListener("click", () => toggleChat());
      const closeBtn = $("chat-close");
      if (closeBtn) closeBtn.addEventListener("click", () => toggleChat(false));
      if (window.lucide) lucide.createIcons();
      connect();
    },
    showDetails,
    closeDetails,
    toggleChat,
  };
})();
