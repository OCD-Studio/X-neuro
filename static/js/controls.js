/**
 * controls.js – Parameter panel wiring
 *
 * Binds all sliders, selects, and buttons in the left panel to
 * API calls or local state.  Imports are loaded by app.js.
 */

import { addNeuronChart } from './charts.js';

// ── Helper: bind slider to live value display ────────────────────────────────
function bindSlider(id, valId, decimals = 1) {
  const slider = document.getElementById(id);
  const valEl  = document.getElementById(valId);
  if (!slider || !valEl) return slider;
  const update = () => { valEl.textContent = parseFloat(slider.value).toFixed(decimals); };
  slider.addEventListener('input', update);
  update();
  return slider;
}

// ── Network tab ──────────────────────────────────────────────────────────────
bindSlider('net-n',    'net-n-val',    0);
bindSlider('net-prob', 'net-prob-val', 2);
bindSlider('net-exc',  'net-exc-val',  2);
bindSlider('net-dist', 'net-dist-val', 1);
bindSlider('sw-k',     'sw-k-val',     0);
bindSlider('sw-beta',  'sw-beta-val',  2);
bindSlider('sf-m',     'sf-m-val',     0);

// Show/hide topology-specific params
document.getElementById('net-topology').addEventListener('change', e => {
  document.getElementById('sw-params').style.display =
    e.target.value === 'small_world' ? 'block' : 'none';
  document.getElementById('sf-params').style.display =
    e.target.value === 'scale_free'  ? 'block' : 'none';
});

document.getElementById('btn-rebuild').addEventListener('click', async () => {
  const topology = document.getElementById('net-topology').value;
  const cfg = {
    n_neurons:    parseInt(document.getElementById('net-n').value),
    topology:     topology,
    connect_prob: parseFloat(document.getElementById('net-prob').value),
    exc_ratio:    parseFloat(document.getElementById('net-exc').value),
    max_distance: parseFloat(document.getElementById('net-dist').value),
    default_model:document.getElementById('net-model').value,
    sw_k:         parseInt(document.getElementById('sw-k').value),
    sw_beta:      parseFloat(document.getElementById('sw-beta').value),
    sf_m:         parseInt(document.getElementById('sf-m').value),
  };
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ network: cfg }),
  });
});

// ── Neuron tab ────────────────────────────────────────────────────────────────
bindSlider('nrn-vrest',   'nrn-vrest-val',   1);
bindSlider('nrn-vthresh', 'nrn-vthresh-val', 1);
bindSlider('nrn-vpeak',   'nrn-vpeak-val',   1);
bindSlider('nrn-vreset',  'nrn-vreset-val',  1);
bindSlider('nrn-taum',    'nrn-taum-val',    1);
bindSlider('nrn-tauref',  'nrn-tauref-val',  1);
bindSlider('nrn-izha',    'nrn-izha-val',    3);
bindSlider('nrn-izhb',    'nrn-izhb-val',    3);
bindSlider('nrn-izhc',    'nrn-izhc-val',    1);
bindSlider('nrn-izhd',    'nrn-izhd-val',    3);
bindSlider('nrn-gna',     'nrn-gna-val',     1);
bindSlider('nrn-gk',      'nrn-gk-val',      1);
bindSlider('nrn-gl',      'nrn-gl-val',      2);
bindSlider('nrn-ena',     'nrn-ena-val',      1);
bindSlider('nrn-ek',      'nrn-ek-val',       1);
bindSlider('nrn-axond',   'nrn-axond-val',   1);
bindSlider('nrn-axonl',   'nrn-axonl-val',   1);
bindSlider('nrn-noise',   'nrn-noise-val',   1);
bindSlider('nrn-iext',    'nrn-iext-val',    1);

// Show/hide model-specific param sections
document.getElementById('nrn-model').addEventListener('change', e => {
  document.getElementById('izh-params').style.display =
    e.target.value === 'izh' ? 'block' : 'none';
  document.getElementById('hh-params').style.display  =
    e.target.value === 'hh'  ? 'block' : 'none';
});

// Izhikevich presets
const IZH_PRESETS = {
  rs:  { a:0.02, b:0.20, c:-65, d:8.0  },  // Regular spiking
  ib:  { a:0.02, b:0.20, c:-55, d:4.0  },  // Intrinsically bursting
  ch:  { a:0.02, b:0.20, c:-50, d:2.0  },  // Chattering
  fs:  { a:0.10, b:0.20, c:-65, d:2.0  },  // Fast spiking
  tc:  { a:0.02, b:0.25, c:-65, d:0.05 },  // Thalamocortical
  lts: { a:0.02, b:0.25, c:-65, d:2.0  },  // Low-threshold spiking
};

document.querySelectorAll('.preset-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const p = IZH_PRESETS[btn.dataset.preset];
    if (!p) return;
    _setSlider('nrn-izha', 'nrn-izha-val', p.a, 3);
    _setSlider('nrn-izhb', 'nrn-izhb-val', p.b, 3);
    _setSlider('nrn-izhc', 'nrn-izhc-val', p.c, 1);
    _setSlider('nrn-izhd', 'nrn-izhd-val', p.d, 3);
  });
});

function _setSlider(id, valId, value, decimals) {
  const el = document.getElementById(id);
  if (el) { el.value = value; document.getElementById(valId).textContent = value.toFixed(decimals); }
}

// ── Public: populate neuron panel from incoming full data ─────────────────────
export function populateNeuronPanel(neuron) {
  if (!neuron) {
    document.getElementById('nrn-id-label').textContent    = 'Neurónio #–';
    document.getElementById('nrn-voltage-live').textContent = 'V = – mV';
    document.getElementById('nrn-fire-rate').textContent    = '0 Hz';
    document.getElementById('sel-neuron-vel').textContent   = 'Neurónio sel.: – m/s';
    return;
  }
  const p = neuron.params || {};
  document.getElementById('nrn-id-label').textContent = `Neurónio #${neuron.id}`;

  _setSlider('nrn-vrest',   'nrn-vrest-val',   p.v_rest       || -70,  1);
  _setSlider('nrn-vthresh', 'nrn-vthresh-val', p.v_threshold  || -55,  1);
  _setSlider('nrn-vpeak',   'nrn-vpeak-val',   p.v_peak       || 30,   1);
  _setSlider('nrn-vreset',  'nrn-vreset-val',  p.v_reset      || -65,  1);
  _setSlider('nrn-taum',    'nrn-taum-val',    p.tau_m        || 20,   1);
  _setSlider('nrn-tauref',  'nrn-tauref-val',  p.tau_ref      || 2,    1);
  _setSlider('nrn-izha',    'nrn-izha-val',    p.izh_a        || 0.02, 3);
  _setSlider('nrn-izhb',    'nrn-izhb-val',    p.izh_b        || 0.2,  3);
  _setSlider('nrn-izhc',    'nrn-izhc-val',    p.izh_c        || -65,  1);
  _setSlider('nrn-izhd',    'nrn-izhd-val',    p.izh_d        || 8,    3);
  _setSlider('nrn-gna',     'nrn-gna-val',     p.g_na         || 120,  1);
  _setSlider('nrn-gk',      'nrn-gk-val',      p.g_k          || 36,   1);
  _setSlider('nrn-gl',      'nrn-gl-val',      p.g_l          || 0.3,  2);
  _setSlider('nrn-ena',     'nrn-ena-val',      p.e_na         || 50,   1);
  _setSlider('nrn-ek',      'nrn-ek-val',       p.e_k          || -77,  1);
  _setSlider('nrn-axond',   'nrn-axond-val',   p.axon_diameter|| 1,    1);
  _setSlider('nrn-axonl',   'nrn-axonl-val',   p.axon_length  || 1,    1);
  _setSlider('nrn-noise',   'nrn-noise-val',   p.noise_level  || 0.5,  1);
  _setSlider('nrn-iext',    'nrn-iext-val',    p.i_ext        || 0,    1);

  if (p.model)       document.getElementById('nrn-model').value = p.model;
  if (p.neuron_type) document.getElementById('nrn-type').value  = p.neuron_type;
  if (p.myelinated !== undefined)
    document.getElementById('nrn-myelin').checked = p.myelinated;

  // show/hide model sections
  document.getElementById('izh-params').style.display = p.model === 'izh' ? 'block' : 'none';
  document.getElementById('hh-params').style.display  = p.model === 'hh'  ? 'block' : 'none';

  // conduction velocity estimate
  const d = p.axon_diameter || 1;
  const vel = p.myelinated ? (6 * d).toFixed(1) : (0.57 * Math.sqrt(d)).toFixed(2);
  document.getElementById('sel-neuron-vel').textContent = `Neurónio sel.: ${vel} m/s`;
}

export function updateNeuronLive(neuron) {
  if (!neuron) return;
  document.getElementById('nrn-voltage-live').textContent = `V = ${(neuron.v||0).toFixed(1)} mV`;
  document.getElementById('nrn-fire-rate').textContent =
    `${(neuron.spikes || 0)} spikes totais`;
}

// ── Apply neuron params button ────────────────────────────────────────────────
export let currentNeuronId = null;
export function setCurrentNeuronId(id) { currentNeuronId = id; }

document.getElementById('btn-apply-neuron').addEventListener('click', async () => {
  if (currentNeuronId === null) { alert('Selecione um neurónio primeiro.'); return; }
  const params = _readNeuronParams();
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ neuron_id: currentNeuronId, neuron_params: params }),
  });
});

// Stimulate selected neuron
document.getElementById('btn-stim-neuron').addEventListener('click', async () => {
  if (currentNeuronId === null) { alert('Selecione um neurónio primeiro.'); return; }
  await fetch('/api/stimulate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      neuron_id:   currentNeuronId,
      stim_type:   document.getElementById('stim-type').value,
      amplitude:   parseFloat(document.getElementById('stim-amp').value),
      duration_ms: parseFloat(document.getElementById('stim-dur').value),
      frequency:   parseFloat(document.getElementById('stim-freq').value),
    }),
  });
});

function _readNeuronParams() {
  return {
    model:        document.getElementById('nrn-model').value,
    neuron_type:  document.getElementById('nrn-type').value,
    v_rest:       parseFloat(document.getElementById('nrn-vrest').value),
    v_threshold:  parseFloat(document.getElementById('nrn-vthresh').value),
    v_peak:       parseFloat(document.getElementById('nrn-vpeak').value),
    v_reset:      parseFloat(document.getElementById('nrn-vreset').value),
    tau_m:        parseFloat(document.getElementById('nrn-taum').value),
    tau_ref:      parseFloat(document.getElementById('nrn-tauref').value),
    izh_a:        parseFloat(document.getElementById('nrn-izha').value),
    izh_b:        parseFloat(document.getElementById('nrn-izhb').value),
    izh_c:        parseFloat(document.getElementById('nrn-izhc').value),
    izh_d:        parseFloat(document.getElementById('nrn-izhd').value),
    g_na:         parseFloat(document.getElementById('nrn-gna').value),
    g_k:          parseFloat(document.getElementById('nrn-gk').value),
    g_l:          parseFloat(document.getElementById('nrn-gl').value),
    e_na:         parseFloat(document.getElementById('nrn-ena').value),
    e_k:          parseFloat(document.getElementById('nrn-ek').value),
    axon_diameter:parseFloat(document.getElementById('nrn-axond').value),
    axon_length:  parseFloat(document.getElementById('nrn-axonl').value),
    myelinated:   document.getElementById('nrn-myelin').checked,
    noise_level:  parseFloat(document.getElementById('nrn-noise').value),
    i_ext:        parseFloat(document.getElementById('nrn-iext').value),
  };
}

// ── Synapse tab ───────────────────────────────────────────────────────────────
bindSlider('syn-weight', 'syn-weight-val', 2);
bindSlider('syn-dist',   'syn-dist-val',   1);
bindSlider('syn-tau',    'syn-tau-val',    1);

export let currentSynapse = null;
export function setCurrentSynapse(s) {
  currentSynapse = s;
  if (!s) return;
  _setSlider('syn-weight', 'syn-weight-val', s.weight   || 1,   2);
  _setSlider('syn-dist',   'syn-dist-val',   s.distance || 1,   1);
  _setSlider('syn-tau',    'syn-tau-val',     s.tau      || 2,   1);
  if (s.type) document.getElementById('syn-type').value = s.type;

  document.getElementById('syn-id-label').textContent    = `Sinapse #${s.src_id} → #${s.tgt_id}`;
  document.getElementById('syn-atten-label').textContent = `Atenuação: ${((s.atten||1)*100).toFixed(1)}%`;

  const lambda = s.lambda || 1;
  const delay  = (s.distance || 1) / 6;   // rough estimate
  document.getElementById('syn-delay-label').textContent = `Atraso: ~${delay.toFixed(2)} ms`;
  document.getElementById('cable-lambda').textContent   = `λ (constante espacial) = ${lambda.toFixed(2)} mm`;
  const atten_pct = (Math.exp(-(s.distance||1)/lambda)*100).toFixed(1);
  document.getElementById('cable-atten').textContent    = `Atenuação na distância atual: ${atten_pct}%`;
}

// Update cable theory when synapse distance slider changes
document.getElementById('syn-dist').addEventListener('input', () => {
  if (!currentSynapse) return;
  const lambda   = currentSynapse.lambda || 1;
  const dist     = parseFloat(document.getElementById('syn-dist').value);
  const atten    = (Math.exp(-dist / lambda) * 100).toFixed(1);
  document.getElementById('cable-atten').textContent = `Atenuação na distância atual: ${atten}%`;
});

document.getElementById('btn-apply-synapse').addEventListener('click', async () => {
  if (!currentSynapse) { alert('Selecione uma sinapse primeiro.'); return; }
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      synapse: {
        src_id:   currentSynapse.src_id,
        tgt_id:   currentSynapse.tgt_id,
        weight:   parseFloat(document.getElementById('syn-weight').value),
        distance: parseFloat(document.getElementById('syn-dist').value),
        tau:      parseFloat(document.getElementById('syn-tau').value),
      }
    }),
  });
});

// ── Evolution tab ─────────────────────────────────────────────────────────────
bindSlider('evo-gen',    'evo-gen-val',    0);
bindSlider('evo-mrate',  'evo-mrate-val',  2);
bindSlider('evo-mscale', 'evo-mscale-val', 2);

document.getElementById('btn-evolve-panel').addEventListener('click', _runEvolution);
document.getElementById('btn-evolve').addEventListener('click', _runEvolution);

async function _runEvolution() {
  const overlay  = document.getElementById('evo-overlay');
  const fill     = document.getElementById('evo-progress-fill');
  const statusEl = document.getElementById('evo-status-text');
  const gens     = parseInt(document.getElementById('evo-gen').value);
  overlay.classList.remove('hidden');
  fill.style.width = '0%';
  statusEl.textContent = `Preparando ${gens} gerações…`;

  const payload = {
    fitness_fn:    document.getElementById('evo-fitness').value,
    generations:   gens,
    mutation_rate: parseFloat(document.getElementById('evo-mrate').value),
    mutation_scale:parseFloat(document.getElementById('evo-mscale').value),
    structural:    document.getElementById('evo-structural').checked,
  };

  try {
    const res  = await fetch('/api/evolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    fill.style.width = '100%';
    statusEl.textContent = `Concluído! Melhor fitness: ${(data.results?.[data.results.length-1]?.best_fitness||0).toFixed(3)}`;
    _renderEvoHistory(data.results || []);
  } catch (e) {
    statusEl.textContent = 'Erro: ' + e.message;
  }
  setTimeout(() => overlay.classList.add('hidden'), 1200);
}

function _renderEvoHistory(results) {
  const div = document.getElementById('evo-history');
  results.forEach((r, i) => {
    const row = document.createElement('div');
    row.className = 'evo-gen-row';
    row.innerHTML = `<span>G${i+1}</span>
      <span>best <span class="fit-val">${r.best_fitness?.toFixed(3)||'?'}</span></span>
      <span>mean <span class="fit-val">${r.mean_fitness?.toFixed(3)||'?'}</span></span>
      ${r.pruned_synapses ? `<span>−${r.pruned_synapses} syn</span>` : ''}
      ${r.pruned_neurons  ? `<span>−${r.pruned_neurons} nrn</span>` : ''}`;
    div.appendChild(row);
  });
  div.scrollTop = div.scrollHeight;
}

// ── Simulation tab ────────────────────────────────────────────────────────────
bindSlider('sim-speed',    'sim-speed-val',    0);
bindSlider('stdp-aplus',   'stdp-aplus-val',   3);
bindSlider('stdp-aminus',  'stdp-aminus-val',  3);
bindSlider('stdp-tauplus', 'stdp-tauplus-val', 0);
bindSlider('stdp-tauminus','stdp-tauminus-val',0);
bindSlider('stim-amp',     'stim-amp-val',     1);
bindSlider('stim-dur',     'stim-dur-val',     0);
bindSlider('stim-freq',    'stim-freq-val',    0);

document.getElementById('btn-apply-sim').addEventListener('click', async () => {
  const sim = {
    dt:            parseFloat(document.getElementById('sim-dt').value),
    speed:         parseFloat(document.getElementById('sim-speed').value),
    stdp_enabled:  document.getElementById('stdp-enabled').checked,
    stdp_a_plus:   parseFloat(document.getElementById('stdp-aplus').value),
    stdp_a_minus:  parseFloat(document.getElementById('stdp-aminus').value),
    stdp_tau_plus: parseFloat(document.getElementById('stdp-tauplus').value),
    stdp_tau_minus:parseFloat(document.getElementById('stdp-tauminus').value),
  };
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ simulation: sim }),
  });
});

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`)?.classList.add('active');
  });
});
