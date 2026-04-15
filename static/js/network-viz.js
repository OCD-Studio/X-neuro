/**
 * network-viz.js – D3-powered neural network visualisation
 *
 * Responsibilities:
 *   - Render neurons as coloured circles (voltage → colour mapping)
 *   - Render synapses as directed arrows (excitatory=green, inhibitory=red)
 *   - Animate spike pulses travelling along axons
 *   - Handle click events (neuron/synapse selection)
 *   - Expose updateState(stateMsg) called from app.js on each WS message
 */

// ── Public API (imported by app.js) ─────────────────────────────────────────
export let selectedNeuronId  = null;
export let selectedSynapse   = null;   // { src_id, tgt_id }
export let onNeuronSelect    = null;   // callback(neuronId)
export let onSynapseSelect   = null;   // callback({src_id,tgt_id})

export function setCallbacks(onNeuron, onSynapse) {
  onNeuronSelect  = onNeuron;
  onSynapseSelect = onSynapse;
}

// ── State ────────────────────────────────────────────────────────────────────
let neurons  = [];
let synapses = [];
let neuronMap = {};   // id → neuron object (with .x, .y from layout)
let synMap    = {};   // "src-tgt" → synapse
let activePulses = []; // { x1,y1, x2,y2, progress, color }

// ── SVG setup ────────────────────────────────────────────────────────────────
const svg        = d3.select('#network-svg');
const synLayer   = d3.select('#syn-layer');
const neuLayer   = d3.select('#neuron-layer');
const pulseLayer = d3.select('#pulse-layer');
const tooltip    = document.getElementById('network-tooltip');

// ── Colour helpers ───────────────────────────────────────────────────────────
// Map membrane voltage (−90…+40 mV) → CSS colour
function voltageColor(v) {
  // clamp
  const lo = -90, hi = 40;
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  // colour stops: deep-blue → blue → cyan → green → yellow → orange → red
  const stops = [
    [0.00, [40,  0,  80]],
    [0.15, [0,   20, 120]],
    [0.35, [0,  100, 200]],
    [0.50, [0,  180, 120]],  // near resting ~−70mV
    [0.65, [30, 220,  60]],
    [0.75, [220,200,   0]],  // threshold ~−55mV
    [0.88, [255,100,   0]],
    [1.00, [255,240,  20]],  // peak +40mV
  ];
  let r = 0, g = 0, b = 0;
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      r = Math.round(c0[0] + f * (c1[0] - c0[0]));
      g = Math.round(c0[1] + f * (c1[1] - c0[1]));
      b = Math.round(c0[2] + f * (c1[2] - c0[2]));
      break;
    }
  }
  return `rgb(${r},${g},${b})`;
}

function neuronRadius(n) {
  const baseR = { pyramidal:9, interneuron:7, motor:12, sensory:8, purkinje:10, granule:5, custom:8 };
  return baseR[n.type] || 8;
}

// ── Initialise network from full config ──────────────────────────────────────
export function initNetwork(data) {
  neurons  = data.full.neurons;
  synapses = data.full.synapses;
  neuronMap = {};
  synMap    = {};
  activePulses = [];

  neurons.forEach(n => { neuronMap[n.id] = n; });
  synapses.forEach(s => { synMap[`${s.src_id}-${s.tgt_id}`] = s; });

  _renderSynapses();
  _renderNeurons();
}

// ── Update state (called each WebSocket frame) ────────────────────────────────
export function updateState(msg) {
  if (!msg.neurons) return;

  // update neuron states
  msg.neurons.forEach(ns => {
    const n = neuronMap[ns.id];
    if (!n) return;
    n.v      = ns.v;
    n.firing = ns.firing;
    n.spikes = ns.spikes;
  });

  // update synapse active flags
  if (msg.synapses) {
    msg.synapses.forEach(ss => {
      const s = synMap[`${ss.src}-${ss.tgt}`];
      if (s) { s.active = ss.active; s.weight = ss.w; }
    });
  }

  // queue spike pulses
  if (msg.spikes) {
    msg.spikes.forEach(sp => {
      const src = neuronMap[sp.id];
      if (!src) return;
      // find outgoing synapses
      synapses.forEach(s => {
        if (s.src_id !== sp.id) return;
        const tgt = neuronMap[s.tgt_id];
        if (!tgt) return;
        activePulses.push({
          x1: src.x, y1: src.y,
          x2: tgt.x, y2: tgt.y,
          progress: 0,
          exc: src.exc,
          id: Math.random(),
        });
      });
    });
  }

  _updateNeuronColors();
  _updateSynapseWidths();
  _animatePulses();
}

// ── Render synapses ───────────────────────────────────────────────────────────
function _renderSynapses() {
  synLayer.selectAll('line.synapse-line').remove();

  synLayer.selectAll('line.synapse-line')
    .data(synapses, s => `${s.src_id}-${s.tgt_id}`)
    .join('line')
    .attr('class', 'synapse-line')
    .attr('x1', s => (neuronMap[s.src_id] || {x:0}).x)
    .attr('y1', s => (neuronMap[s.src_id] || {y:0}).y)
    .attr('x2', s => (neuronMap[s.tgt_id] || {x:0}).x)
    .attr('y2', s => (neuronMap[s.tgt_id] || {y:0}).y)
    .attr('stroke', s => _synColor(s))
    .attr('stroke-width', s => 1 + s.weight * 0.8)
    .attr('marker-end', s => s.type === 'gaba_a' || s.type === 'gaba_b'
      ? 'url(#arrow-inh)' : 'url(#arrow-exc)')
    .on('click', (event, s) => {
      event.stopPropagation();
      selectedSynapse = { src_id: s.src_id, tgt_id: s.tgt_id };
      if (onSynapseSelect) onSynapseSelect(s);
      _highlightSynapse(s);
    })
    .on('mouseover', (event, s) => _showTooltip(event, _synTooltip(s)))
    .on('mouseout',  () => _hideTooltip());
}

function _synColor(s) {
  const isInh = s.type === 'gaba_a' || s.type === 'gaba_b';
  if (s.active) return '#ffee00';
  return isInh ? '#ff4466' : '#00bb66';
}

function _synTooltip(s) {
  const src = neuronMap[s.src_id];
  const tgt = neuronMap[s.tgt_id];
  const atten = (Math.exp(-s.distance / (s.lambda || 1.0)) * 100).toFixed(1);
  return `<b>${src?.type || '?'} #${s.src_id} → #${s.tgt_id}</b><br>
    Tipo: ${s.type}<br>
    Peso: ${s.weight?.toFixed(3)}<br>
    Dist: ${s.distance?.toFixed(2)} mm<br>
    Atenuação: ${atten}%<br>
    τ = ${s.tau} ms`;
}

// ── Render neurons ────────────────────────────────────────────────────────────
function _renderNeurons() {
  neuLayer.selectAll('g.neuron-g').remove();

  const groups = neuLayer.selectAll('g.neuron-g')
    .data(neurons, n => n.id)
    .join('g')
    .attr('class', 'neuron-g')
    .attr('transform', n => `translate(${n.x},${n.y})`)
    .style('cursor', 'pointer')
    .on('click', (event, n) => {
      event.stopPropagation();
      selectedNeuronId = n.id;
      if (onNeuronSelect) onNeuronSelect(n);
      _highlightNeuron(n.id);
    })
    .on('mouseover', (event, n) => _showTooltip(event, _neuronTooltip(n)))
    .on('mouseout',  () => _hideTooltip());

  // outer glow ring (visible when firing)
  groups.append('circle')
    .attr('class', 'neuron-glow')
    .attr('r', n => neuronRadius(n) + 5)
    .attr('fill', 'none')
    .attr('stroke', n => n.exc ? '#00ff88' : '#ff4466')
    .attr('stroke-width', 1.5)
    .attr('opacity', 0);

  // main circle
  groups.append('circle')
    .attr('class', 'neuron-circle')
    .attr('r', n => neuronRadius(n))
    .attr('fill', n => voltageColor(n.v || -70))
    .attr('stroke', n => n.exc ? '#00aa55' : '#aa2233')
    .attr('stroke-width', 1.5);

  // ID label
  groups.append('text')
    .attr('class', 'neuron-label')
    .attr('text-anchor', 'middle')
    .attr('dy', '0.35em')
    .text(n => n.id);
}

// ── Update neuron colours each frame ─────────────────────────────────────────
function _updateNeuronColors() {
  neuLayer.selectAll('g.neuron-g')
    .each(function(n) {
      const g = d3.select(this);
      g.select('circle.neuron-circle')
        .attr('fill', voltageColor(n.v || -70))
        .attr('r', n.firing ? neuronRadius(n) * 1.4 : neuronRadius(n));

      g.select('circle.neuron-glow')
        .attr('opacity', n.firing ? 0.8 : 0)
        .attr('r', n.firing ? neuronRadius(n) + 8 : neuronRadius(n) + 5);

      // selected highlight
      if (n.id === selectedNeuronId) {
        g.select('circle.neuron-circle')
          .attr('stroke', '#ffee00').attr('stroke-width', 3);
      } else {
        g.select('circle.neuron-circle')
          .attr('stroke', n.exc ? '#00aa55' : '#aa2233')
          .attr('stroke-width', 1.5);
      }
    });
}

// ── Update synapse line widths ────────────────────────────────────────────────
function _updateSynapseWidths() {
  synLayer.selectAll('line.synapse-line')
    .attr('stroke', s => _synColor(s))
    .attr('stroke-width', s => s.active ? 3 : 1 + (s.weight || 1) * 0.8)
    .attr('marker-end', s => {
      if (s.active) return 'url(#arrow-active)';
      return (s.type === 'gaba_a' || s.type === 'gaba_b')
        ? 'url(#arrow-inh)' : 'url(#arrow-exc)';
    });
}

// ── Animate spike pulses ──────────────────────────────────────────────────────
function _animatePulses() {
  // advance all pulses
  activePulses = activePulses.filter(p => p.progress < 1.0);
  activePulses.forEach(p => { p.progress = Math.min(1.0, p.progress + 0.06); });

  const pulses = pulseLayer.selectAll('circle.spike-pulse')
    .data(activePulses, p => p.id);

  pulses.join(
    enter => enter.append('circle')
      .attr('class', 'spike-pulse')
      .attr('r', 4)
      .attr('fill', p => p.exc ? '#00ff88' : '#ff4466')
      .attr('filter', 'url(#glow)')
      .attr('opacity', 1),
    update => update,
    exit   => exit.remove(),
  )
  .attr('cx', p => p.x1 + (p.x2 - p.x1) * p.progress)
  .attr('cy', p => p.y1 + (p.y2 - p.y1) * p.progress)
  .attr('opacity', p => 1 - p.progress * 0.7);
}

// ── Highlight helpers ─────────────────────────────────────────────────────────
function _highlightNeuron(id) {
  neuLayer.selectAll('g.neuron-g').each(function(n) {
    d3.select(this).select('circle.neuron-circle')
      .attr('stroke', n.id === id ? '#ffee00' : (n.exc ? '#00aa55' : '#aa2233'))
      .attr('stroke-width', n.id === id ? 3 : 1.5);
  });
}

function _highlightSynapse(sel) {
  synLayer.selectAll('line.synapse-line')
    .attr('stroke-opacity', s =>
      (s.src_id === sel.src_id && s.tgt_id === sel.tgt_id) ? 1.0 : 0.4);
}

// ── Tooltip helpers ───────────────────────────────────────────────────────────
function _showTooltip(event, html) {
  const container = document.getElementById('network-container');
  const rect = container.getBoundingClientRect();
  tooltip.innerHTML = html;
  tooltip.classList.remove('hidden');
  const x = event.clientX - rect.left + 12;
  const y = event.clientY - rect.top  + 12;
  tooltip.style.left = Math.min(x, rect.width  - 220) + 'px';
  tooltip.style.top  = Math.min(y, rect.height - 100) + 'px';
}
function _hideTooltip() { tooltip.classList.add('hidden'); }

function _neuronTooltip(n) {
  return `<b>${n.type} #${n.id}</b> (${n.exc ? 'excitatório' : 'inibitório'})<br>
    V = <b>${(n.v||0).toFixed(1)} mV</b><br>
    Spikes: ${n.spikes || 0}<br>
    i_ext: ${(n.i_ext||0).toFixed(1)} μA/cm²`;
}

// ── SVG click (deselect) ──────────────────────────────────────────────────────
svg.on('click', () => {
  selectedNeuronId = null;
  selectedSynapse  = null;
  _updateNeuronColors();
  synLayer.selectAll('line.synapse-line').attr('stroke-opacity', 0.55);
  if (onNeuronSelect)  onNeuronSelect(null);
  if (onSynapseSelect) onSynapseSelect(null);
});

// ── Zoom & pan ────────────────────────────────────────────────────────────────
const zoom = d3.zoom().scaleExtent([0.3, 4]).on('zoom', (event) => {
  synLayer.attr('transform', event.transform);
  neuLayer.attr('transform', event.transform);
  pulseLayer.attr('transform', event.transform);
});
svg.call(zoom);
