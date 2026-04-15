/**
 * app.js – Main application orchestrator
 *
 * Manages WebSocket connection, dispatches state to
 * network-viz.js and charts.js, handles header controls.
 */

import { initNetwork, updateState, setCallbacks } from './network-viz.js';
import { updateCharts, addNeuronChart }            from './charts.js';
import {
  populateNeuronPanel, updateNeuronLive,
  setCurrentNeuronId, setCurrentSynapse,
} from './controls.js';

// ── WebSocket ────────────────────────────────────────────────────────────────
let ws;
let reconnectDelay = 1000;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    reconnectDelay = 1000;
    console.log('[X-Neuro] WebSocket connected');
    setStatus('running');
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); }
    catch { return; }
    handleMessage(msg);
  };

  ws.onclose = () => {
    setStatus('paused');
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  };

  ws.onerror = () => { ws.close(); };
}

// ── Message dispatcher ───────────────────────────────────────────────────────
let latestNeurons = {};   // id → latest state for live panel update

function handleMessage(msg) {
  switch (msg.type) {
    case 'init':
    case 'network_reset':
      initNetwork(msg);
      updateHeaderStats(msg.stats);
      break;

    case 'state':
      updateState(msg);
      updateCharts(msg);
      updateHeaderStats(msg.stats);
      updateLiveNeuronPanel(msg);
      updateRightPanel(msg);
      break;

    case 'evolution_done':
      console.log('[evolve] done:', msg.results);
      break;

    case 'neuron_detail':
      populateNeuronPanel(msg.neuron);
      addNeuronChart(msg.neuron.id, msg.neuron.params?.neuron_type);
      break;
  }
}

// ── Header stats ─────────────────────────────────────────────────────────────
function updateHeaderStats(stats) {
  if (!stats) return;
  document.getElementById('sim-time').textContent    = `t = ${(stats.sim_time_ms||0).toFixed(0)} ms`;
  document.getElementById('spike-count').textContent = `${stats.total_spikes || 0} spikes`;
  document.getElementById('sync-badge').textContent  = `sync: ${(stats.synchrony||0).toFixed(2)}`;
}

// ── Right panel stats ─────────────────────────────────────────────────────────
function updateRightPanel(msg) {
  const stats = msg.stats;
  if (!stats) return;
  document.getElementById('st-neurons').textContent  = stats.n_neurons  || 0;
  document.getElementById('st-synapses').textContent = stats.n_synapses || 0;
  document.getElementById('st-exc').textContent      = stats.exc_count  || 0;
  document.getElementById('st-inh').textContent      = stats.inh_count  || 0;
  document.getElementById('st-meanv').textContent    = (stats.mean_v || 0).toFixed(1);
  document.getElementById('st-spikes').textContent   = stats.total_spikes || 0;

  _renderRateBars(stats.firing_rates || [], msg.neurons || []);
}

function _renderRateBars(rates, neurons) {
  const container = document.getElementById('rate-bars');
  const maxRate   = Math.max(...rates, 1);

  // re-use or create rows
  container.innerHTML = '';
  rates.forEach((r, i) => {
    const n   = neurons[i];
    const pct = (r / maxRate * 100).toFixed(1);
    const color = n?.exc ? '#00aaff' : '#ff4466';
    const row = document.createElement('div');
    row.className = 'rate-bar-row';
    row.innerHTML = `
      <span class="rate-bar-label">#${n?.id ?? i}</span>
      <span class="rate-bar-track">
        <span class="rate-bar-fill" style="width:${pct}%;background:${color}"></span>
      </span>
      <span class="rate-bar-val">${r.toFixed(1)} Hz</span>`;
    container.appendChild(row);
  });
}

// ── Live neuron panel ─────────────────────────────────────────────────────────
let _selectedNeuronId = null;

function updateLiveNeuronPanel(msg) {
  if (_selectedNeuronId === null || !msg.neurons) return;
  const n = msg.neurons.find(x => x.id === _selectedNeuronId);
  if (n) updateNeuronLive(n);
}

// ── Network viz callbacks ─────────────────────────────────────────────────────
setCallbacks(
  // onNeuronSelect
  async (neuronOrNull) => {
    if (neuronOrNull === null) {
      _selectedNeuronId = null;
      setCurrentNeuronId(null);
      populateNeuronPanel(null);
      return;
    }
    _selectedNeuronId = neuronOrNull.id;
    setCurrentNeuronId(neuronOrNull.id);

    // request full detail from server
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'get_neuron', id: neuronOrNull.id }));
    }

    // also add to chart
    addNeuronChart(neuronOrNull.id, neuronOrNull.type);

    // switch to neuron tab
    _activateTab('neuron');
  },

  // onSynapseSelect
  (synapseOrNull) => {
    if (!synapseOrNull) { setCurrentSynapse(null); return; }
    setCurrentSynapse(synapseOrNull);
    _activateTab('synapse');
  },
);

function _activateTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  document.querySelectorAll('.tab-content').forEach(t => {
    t.classList.toggle('active', t.id === `tab-${name}`);
  });
}

// ── Header buttons ────────────────────────────────────────────────────────────
document.getElementById('btn-start').addEventListener('click', async () => {
  await fetch('/api/control', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'start' }),
  });
  setStatus('running');
});

document.getElementById('btn-pause').addEventListener('click', async () => {
  await fetch('/api/control', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'pause' }),
  });
  setStatus('paused');
});

document.getElementById('btn-reset').addEventListener('click', async () => {
  if (!confirm('Reiniciar simulação? O histórico de spikes será apagado.')) return;
  await fetch('/api/control', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'reset' }),
  });
  setStatus('running');
});

document.getElementById('btn-stim-all').addEventListener('click', async () => {
  await fetch('/api/stimulate/all', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      neuron_id:   -1,
      stim_type:   document.getElementById('stim-type')?.value || 'pulse',
      amplitude:   parseFloat(document.getElementById('stim-amp')?.value || 8),
      duration_ms: parseFloat(document.getElementById('stim-dur')?.value || 100),
      frequency:   parseFloat(document.getElementById('stim-freq')?.value || 20),
    }),
  });
});

// ── Status badge ──────────────────────────────────────────────────────────────
function setStatus(state) {
  const el = document.getElementById('sim-status');
  if (state === 'running') {
    el.textContent = '▶ A Correr';
    el.className   = 'stat-badge status-running';
  } else {
    el.textContent = '⏸ Pausado';
    el.className   = 'stat-badge status-paused';
  }
}

// ── Kick off ──────────────────────────────────────────────────────────────────
connect();
