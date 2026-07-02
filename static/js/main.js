'use strict';

const App = (() => {

  // ── Estado global ──────────────────────────────────────────────────────────
  const state = {
    gases:           [],
    clientes:        [],
    proveedores:     [],
    inventarioData:  [],
    filtroUbicacion: 'all',
  };

  // ── Matriz de visibilidad de campos por tipo de movimiento ─────────────────
  // true = mostrar, false = ocultar
  const FIELD_MAP = {
    despacho:          { cliente: true,  ubicacion: false, entidad_ajuste: false, propiedad: true,  estado_ret: false, estado_ajuste: false },
    retorno:           { cliente: true,  ubicacion: false, entidad_ajuste: false, propiedad: true,  estado_ret: true,  estado_ajuste: false },
    intercambio:       { cliente: false, ubicacion: false, entidad_ajuste: false, propiedad: false, estado_ret: false, estado_ajuste: false },
    envio_proveedor:   { cliente: false, ubicacion: false, entidad_ajuste: false, propiedad: false, estado_ret: false, estado_ajuste: false },
    retorno_proveedor: { cliente: false, ubicacion: false, entidad_ajuste: false, propiedad: false, estado_ret: false, estado_ajuste: false },
    ajuste:            { cliente: false, ubicacion: true,  entidad_ajuste: false, propiedad: true,  estado_ret: false, estado_ajuste: true  },
  };
  // Nota: entidad_ajuste se controla dinámicamente en onUbicacionAjusteChange()

  // ── Metadatos de cada tipo para la barra informativa ──────────────────────
  const TIPO_INFO = {
    despacho: {
      icon: '🚚', color: '#0D47A1', bg: '#E3F2FD',
      label: 'Despacho a Cliente',
      desc: 'Envía cilindros llenos de Bodega a un Cliente. Descuenta de Bodega y suma en la cuenta del Cliente.',
    },
    retorno: {
      icon: '↩️', color: '#2E7D32', bg: '#E8F5E9',
      label: 'Retorno de Cliente',
      desc: 'Registra cilindros devueltos por el Cliente a Bodega. El estado puede ser lleno o vacío.',
    },
    intercambio: {
      icon: '🔁', color: '#E65100', bg: '#FFF3E0',
      label: 'Intercambio — Proveedor 1 (Arrendados)',
      desc: 'Entrega vacíos arrendados y recibe la misma cantidad de llenos arrendados al instante. La propiedad siempre es Arrendado.',
    },
    envio_proveedor: {
      icon: '📤', color: '#880E4F', bg: '#FCE4EC',
      label: 'Envío a Proveedor 2 — Fase 1',
      desc: 'Envía cilindros vacíos propios al Proveedor 2. Quedan en custodia del proveedor hasta que los devuelva recargados.',
    },
    retorno_proveedor: {
      icon: '📥', color: '#2E7D32', bg: '#E8F5E9',
      label: 'Retorno de Proveedor 2 — Fase 2',
      desc: 'Registra la recepción de cilindros llenos propios devueltos por el Proveedor 2 a Bodega.',
    },
    ajuste: {
      icon: '✏️', color: '#6A1B9A', bg: '#F3E5F5',
      label: 'Ajuste de Inventario',
      desc: 'Establece la cantidad exacta para cualquier combinación. Úsalo para el ingreso inicial de stock o para correcciones manuales.',
    },
  };

  // ── Etiquetas de botón de envío por tipo ──────────────────────────────────
  const BTN_LABELS = {
    despacho:          '🚚 Registrar Despacho',
    retorno:           '↩️ Registrar Retorno',
    intercambio:       '🔁 Registrar Intercambio',
    envio_proveedor:   '📤 Registrar Envío',
    retorno_proveedor: '📥 Registrar Recepción',
    ajuste:            '✏️ Guardar Ajuste',
  };

  // ── Endpoints por tipo ────────────────────────────────────────────────────
  const ENDPOINTS = {
    despacho:          '/api/movimiento/despacho',
    retorno:           '/api/movimiento/retorno',
    intercambio:       '/api/movimiento/intercambio',
    envio_proveedor:   '/api/movimiento/envio_proveedor',
    retorno_proveedor: '/api/movimiento/retorno_proveedor',
    ajuste:            '/api/inventario/ajuste',
  };

  // ── Helpers DOM ────────────────────────────────────────────────────────────
  const el    = id => document.getElementById(id);
  const show  = id => { el(id).style.display = ''; };
  const hide  = id => { el(id).style.display = 'none'; };
  const val   = id => el(id).value;
  const ival  = id => parseInt(el(id).value, 10);

  function fillSelect(id, items, valKey, labelKey) {
    const s = el(id);
    if (!s) return;
    s.innerHTML = items.map(i => `<option value="${i[valKey]}">${i[labelKey]}</option>`).join('');
  }

  function capitalize(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
  }

  // ── API wrapper ────────────────────────────────────────────────────────────
  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || `Error HTTP ${res.status}`);
    return json;
  }

  // ── Toast ──────────────────────────────────────────────────────────────────
  let _toastTimer;
  function toast(msg, tipo = 'ok') {
    const t = el('toast');
    t.textContent = msg;
    t.className = `toast show ${tipo}`;
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { t.className = 'toast'; }, 4000);
  }

  // ── Reloj ──────────────────────────────────────────────────────────────────
  function updateClock() {
    const now = new Date();
    const fecha = now.toLocaleDateString('es-CO', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
    const hora  = now.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });
    const str = fecha + ' · ' + hora;
    el('fecha-hora').textContent = str.charAt(0).toUpperCase() + str.slice(1);
  }

  // ── Navegación SPA ────────────────────────────────────────────────────────
  function initNav() {
    document.querySelectorAll('.nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.dataset.section;
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        el(targetId).classList.add('active');
        btn.classList.add('active');

        if (targetId === 'sec-inventario') refreshInventario();
        if (targetId === 'sec-historial')  loadHistorial();
        if (targetId === 'sec-config')     renderConfigTables();
      });
    });
  }

  // ── Filtros de inventario ─────────────────────────────────────────────────
  function initFilters() {
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.filtroUbicacion = btn.dataset.filter;
        renderInventario();
      });
    });
  }

  // ── Catálogos ─────────────────────────────────────────────────────────────
  async function loadCatalogos() {
    try {
      [state.gases, state.clientes, state.proveedores] = await Promise.all([
        api('/api/gases'),
        api('/api/clientes'),
        api('/api/proveedores'),
      ]);
      fillSelect('mov-gas',     state.gases,    'id', 'nombre');
      fillSelect('mov-cliente', state.clientes, 'id', 'nombre');
      renderConfigTables();
    } catch (e) {
      toast('Error al cargar catálogos: ' + e.message, 'err');
    }
  }

  // ── INVENTARIO ────────────────────────────────────────────────────────────
  async function refreshInventario() {
    try {
      const [bodega, completo] = await Promise.all([
        api('/api/inventario/bodega'),
        api('/api/inventario/completo'),
      ]);
      renderSummaryCards(bodega);
      state.inventarioData = completo;
      renderInventario();
    } catch (e) {
      toast('Error al cargar inventario: ' + e.message, 'err');
    }
  }

  function renderSummaryCards(bodegaData) {
    const container = el('summary-cards');

    if (!bodegaData.length) {
      container.innerHTML = `
        <div class="card" style="grid-column:1/-1;text-align:center;color:var(--gris-medio)">
          Sin stock en Bodega. Use <strong>Ajuste de Inventario</strong> en la pesta&ntilde;a de Movimientos
          para ingresar el stock inicial.
        </div>`;
      return;
    }

    const porGas = {};
    for (const r of bodegaData) {
      if (!porGas[r.gas]) porGas[r.gas] = { llenos: 0, vacios: 0 };
      if (r.estado === 'lleno') porGas[r.gas].llenos += r.cantidad;
      else                      porGas[r.gas].vacios += r.cantidad;
    }

    container.innerHTML = Object.entries(porGas)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([gas, v]) => `
        <div class="summary-card">
          <div class="sc-gas">${gas}</div>
          <div class="sc-total">${v.llenos + v.vacios}</div>
          <div class="sc-detail">
            <span class="sc-lleno">&#x25B2; ${v.llenos} llenos</span>
            <span class="sc-vacio">&#x25BC; ${v.vacios} vac&iacute;os</span>
          </div>
        </div>`).join('');
  }

  function renderInventario() {
    const tbody = el('tbody-inventario');
    let data = state.inventarioData;

    if (state.filtroUbicacion !== 'all') {
      data = data.filter(r => r.ubicacion === state.filtroUbicacion);
    }

    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-msg">
        Sin registros${state.filtroUbicacion !== 'all' ? ' para esta ubicaci&oacute;n' : ''}.
      </td></tr>`;
      return;
    }

    tbody.innerHTML = data.map(r => `
      <tr>
        <td><span class="badge badge-${r.ubicacion}">${capitalize(r.ubicacion)}</span></td>
        <td>${r.entidad_nombre}</td>
        <td><strong>${r.gas}</strong></td>
        <td><span class="badge badge-${r.propiedad === 'propio' ? 'propio' : 'arrend'}">${capitalize(r.propiedad)}</span></td>
        <td><span class="badge badge-${r.estado}">${capitalize(r.estado)}</span></td>
        <td class="col-num">${r.cantidad}</td>
      </tr>`).join('');
  }

  // ── HISTORIAL ─────────────────────────────────────────────────────────────
  async function loadHistorial() {
    const tbody = el('tbody-historial');
    tbody.innerHTML = '<tr><td colspan="10" class="loading-msg">Cargando&hellip;</td></tr>';
    try {
      const data = await api('/api/historial?limite=150');
      if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-msg">Sin movimientos registrados aún.</td></tr>';
        return;
      }
      tbody.innerHTML = data.map(r => `
        <tr>
          <td style="color:var(--gris-medio);font-size:.8rem">${r.id}</td>
          <td style="white-space:nowrap;font-size:.8rem">${r.fecha}</td>
          <td><span class="tipo-tag">${r.tipo_movimiento}</span></td>
          <td><strong>${r.gas}</strong></td>
          <td><span class="badge badge-${r.propiedad === 'propio' ? 'propio' : 'arrend'}">${capitalize(r.propiedad)}</span></td>
          <td><span class="badge badge-${r.estado_movido}">${capitalize(r.estado_movido)}</span></td>
          <td class="col-num">${r.cantidad}</td>
          <td>${r.origen_nombre  ?? '<span style="color:var(--gris-medio)">Bodega JCM</span>'}</td>
          <td>${r.destino_nombre ?? '<span style="color:var(--gris-medio)">Bodega JCM</span>'}</td>
          <td style="font-size:.8rem;color:var(--texto-sec)">${r.notas ?? ''}</td>
        </tr>`).join('');
    } catch (e) {
      toast('Error al cargar historial: ' + e.message, 'err');
      tbody.innerHTML = '<tr><td colspan="10" class="empty-msg">Error al cargar.</td></tr>';
    }
  }

  // ── CONFIGURACIÓN ─────────────────────────────────────────────────────────
  function renderConfigTables() {
    // Tabla clientes
    const tbodyC = el('tbody-clientes');
    if (tbodyC) {
      tbodyC.innerHTML = state.clientes.length
        ? state.clientes.map(c => `<tr><td style="color:var(--gris-medio)">${c.id}</td><td>${c.nombre}</td></tr>`).join('')
        : '<tr><td colspan="2" class="empty-msg">Sin clientes registrados.</td></tr>';
    }
    // Tabla proveedores
    const tbodyP = el('tbody-proveedores');
    if (tbodyP) {
      tbodyP.innerHTML = state.proveedores.length
        ? state.proveedores.map(p => `<tr>
            <td style="color:var(--gris-medio)">${p.id}</td>
            <td>${p.nombre}</td>
            <td><span class="badge badge-propio">${p.tipo_operacion}</span></td>
          </tr>`).join('')
        : '<tr><td colspan="3" class="empty-msg">Sin proveedores.</td></tr>';
    }
  }

  async function submitNuevoCliente(e) {
    e.preventDefault();
    const nombre = el('nuevo-cliente-nombre').value.trim();
    if (!nombre) return;
    try {
      const nuevo = await api('/api/clientes', {
        method: 'POST',
        body: JSON.stringify({ nombre }),
      });
      toast(`Cliente "${nuevo.nombre}" agregado correctamente`);
      el('nuevo-cliente-nombre').value = '';
      await loadCatalogos();
    } catch (err) {
      toast(err.message, 'err');
    }
  }

  // ── FORMULARIO DE MOVIMIENTOS ─────────────────────────────────────────────

  function onTipoChange() {
    const tipo = val('mov-tipo');
    const map  = FIELD_MAP[tipo];
    if (!map) return;

    // Mostrar/ocultar grupos de campos
    el('fg-cliente').style.display       = map.cliente       ? '' : 'none';
    el('fg-ubicacion').style.display     = map.ubicacion     ? '' : 'none';
    el('fg-propiedad').style.display     = map.propiedad     ? '' : 'none';
    el('fg-estado-ret').style.display    = map.estado_ret    ? '' : 'none';
    el('fg-estado-ajuste').style.display = map.estado_ajuste ? '' : 'none';
    el('fg-entidad-ajuste').style.display = 'none'; // siempre oculto al cambiar tipo; ubic lo controla

    // Barra informativa
    const info = TIPO_INFO[tipo];
    if (info) {
      const bar = el('mov-info');
      bar.className = 'mov-info-bar visible';
      bar.style.cssText = `background:${info.bg};color:${info.color};border-left-color:${info.color}`;
      bar.innerHTML = `
        <span class="mov-info-icon">${info.icon}</span>
        <div class="mov-info-text">
          <strong>${info.label}</strong>
          ${info.desc}
        </div>`;
    }

    // Etiqueta del botón de envío
    el('btn-mov-submit').textContent = BTN_LABELS[tipo] || 'Registrar Movimiento';

    // Si es ajuste, inicializar visibilidad de entidad
    if (tipo === 'ajuste') onUbicacionAjusteChange();
  }

  function onUbicacionAjusteChange() {
    const ubicacion = val('mov-ubicacion');
    if (ubicacion === 'bodega') {
      hide('fg-entidad-ajuste');
    } else {
      show('fg-entidad-ajuste');
      const lista = ubicacion === 'cliente' ? state.clientes : state.proveedores;
      fillSelect('mov-entidad-ajuste', lista, 'id', 'nombre');
    }
  }

  function buildPayload(tipo) {
    const gas_id   = ival('mov-gas');
    const cantidad = ival('mov-cantidad');
    const notas    = val('mov-notas').trim() || null;

    if (isNaN(gas_id) || isNaN(cantidad)) {
      throw new Error('Valores de gas o cantidad inválidos.');
    }

    switch (tipo) {
      case 'despacho':
        return { cliente_id: ival('mov-cliente'), gas_id, propiedad: val('mov-propiedad'), cantidad, notas };

      case 'retorno':
        return { cliente_id: ival('mov-cliente'), gas_id, propiedad: val('mov-propiedad'), estado: val('mov-estado-ret'), cantidad, notas };

      case 'intercambio':
        return { gas_id, cantidad, notas };

      case 'envio_proveedor':
        return { gas_id, cantidad, notas };

      case 'retorno_proveedor':
        return { gas_id, cantidad, notas };

      case 'ajuste': {
        const ubicacion  = val('mov-ubicacion');
        const entidad_id = ubicacion === 'bodega' ? 0 : ival('mov-entidad-ajuste');
        return { ubicacion, entidad_id, gas_id, propiedad: val('mov-propiedad'), estado: val('mov-estado-ajuste'), cantidad, notas };
      }

      default:
        throw new Error('Tipo de movimiento desconocido: ' + tipo);
    }
  }

  function validatePayload(tipo, payload) {
    if (tipo === 'ajuste' && payload.cantidad < 0) return 'La cantidad no puede ser negativa en un ajuste.';
    if (tipo !== 'ajuste' && payload.cantidad < 1) return 'La cantidad debe ser al menos 1.';
    if ((tipo === 'despacho' || tipo === 'retorno') && isNaN(payload.cliente_id)) return 'Seleccione un cliente válido.';
    return null;
  }

  async function submitMovimiento(e) {
    e.preventDefault();
    const tipo = val('mov-tipo');

    let payload;
    try {
      payload = buildPayload(tipo);
    } catch (err) {
      toast(err.message, 'err');
      return;
    }

    const validErr = validatePayload(tipo, payload);
    if (validErr) { toast(validErr, 'warn'); return; }

    const btn = el('btn-mov-submit');
    const labelOrig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Procesando…';

    try {
      await api(ENDPOINTS[tipo], { method: 'POST', body: JSON.stringify(payload) });
      toast('Movimiento registrado correctamente');
      resetForm();
      refreshInventario(); // actualizar inventario en segundo plano
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = labelOrig;
    }
  }

  function resetForm() {
    el('form-movimiento').reset();
    el('mov-cantidad').value = '1';
    // Restaurar visibilidad de campos según tipo actual
    onTipoChange();
  }

  // ── INICIALIZACIÓN ────────────────────────────────────────────────────────
  async function init() {
    initNav();
    initFilters();
    updateClock();
    setInterval(updateClock, 30_000);
    await loadCatalogos();
    onTipoChange();       // estado inicial del formulario
    await refreshInventario();
  }

  // ── API pública ───────────────────────────────────────────────────────────
  return {
    init,
    refreshInventario,
    loadHistorial,
    loadCatalogos,
    onTipoChange,
    onUbicacionAjusteChange,
    submitMovimiento,
    submitNuevoCliente,
    resetForm,
  };

})();

document.addEventListener('DOMContentLoaded', App.init);
