/**
 * charts.js – Real-time membrane potential charts using Chart.js
 *
 * Up to 4 neurons can be monitored simultaneously.
 * Each chart shows the last ~200 ms of membrane potential.
 */

const MAX_SLOTS   = 4;
const WINDOW_SIZE = 200;   // data points shown (1 per ms)

const COLORS = ['#00aaff', '#00ff88', '#ff8800', '#aa66ff'];

// ── State ────────────────────────────────────────────────────────────────────
const slots = Array.from({ length: MAX_SLOTS }, (_, i) => ({
  neuronId: null,
  chart:    null,
  canvas:   document.getElementById(`chart-${i}`),
  label:    document.getElementById(`chart-label-${i}`),
  data:     new Array(WINDOW_SIZE).fill(null),
  color:    COLORS[i],
}));

// raster canvas
const rasterCanvas = document.getElementById('raster-canvas');
const rasterCtx    = rasterCanvas.getContext('2d');
const RASTER_W     = rasterCanvas.width;
const RASTER_H     = rasterCanvas.height;
let   rasterBuffer = [];   // { x, y } pixel positions (ring buffer)

// ── Initialise charts ────────────────────────────────────────────────────────
slots.forEach((slot, i) => {
  slot.chart = new Chart(slot.canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: Array.from({ length: WINDOW_SIZE }, (_, k) => k),
      datasets: [{
        data: slot.data.slice(),
        borderColor: slot.color,
        borderWidth: 1.5,
        fill: false,
        pointRadius: 0,
        tension: 0.1,
      }],
    },
    options: {
      animation:   false,
      responsive:  true,
      maintainAspectRatio: false,
      plugins: {
        legend:  { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: {
          display: false,
          ticks:   { display: false },
          grid:    { display: false },
        },
        y: {
          min:  -90,
          max:   50,
          ticks: {
            color:   '#546a7a',
            font:    { size: 8 },
            maxTicksLimit: 5,
            callback: v => v + 'mV',
          },
          grid: {
            color:       ctx => ctx.tick.value === -70 || ctx.tick.value === -55
                                  ? '#1e3a4a' : '#111920',
            borderColor: '#1e2d3d',
          },
        },
      },
    },
  });
});

// ── Public: register neuron in a slot ────────────────────────────────────────
export function addNeuronChart(neuronId, neuronType) {
  // check if already tracked
  if (slots.some(s => s.neuronId === neuronId)) return;

  // find empty slot
  const slot = slots.find(s => s.neuronId === null);
  if (!slot) {
    // evict oldest (first slot)
    slots.push(slots.shift());
    const evicted = slots[slots.length - 1];
    evicted.neuronId = null;
    evicted.data     = new Array(WINDOW_SIZE).fill(null);
    evicted.label.textContent = '–';
    return addNeuronChart(neuronId, neuronType);
  }

  slot.neuronId = neuronId;
  slot.data     = new Array(WINDOW_SIZE).fill(-70);
  slot.label.textContent = `#${neuronId} ${neuronType || ''}`;
}

export function clearAllCharts() {
  slots.forEach(slot => {
    slot.neuronId = null;
    slot.data     = new Array(WINDOW_SIZE).fill(null);
    slot.label.textContent = '–';
    slot.chart.data.datasets[0].data = slot.data.slice();
    slot.chart.update('none');
  });
}

// ── Public: update charts from state message ──────────────────────────────────
export function updateCharts(msg) {
  if (!msg.neurons) return;

  const neuronDataMap = {};
  msg.neurons.forEach(n => { neuronDataMap[n.id] = n; });

  slots.forEach(slot => {
    if (slot.neuronId === null) return;
    const n = neuronDataMap[slot.neuronId];
    if (!n) return;

    slot.data.push(n.v);
    if (slot.data.length > WINDOW_SIZE) slot.data.shift();
    slot.chart.data.datasets[0].data = slot.data.slice();
    slot.chart.update('none');

    // live voltage in label
    slot.label.textContent = `#${slot.neuronId} ${(n.v || 0).toFixed(1)}mV`;
  });

  // raster plot
  _updateRaster(msg);
}

// ── Raster plot ───────────────────────────────────────────────────────────────
let rasterTime = 0;

function _updateRaster(msg) {
  if (!msg.spikes || !msg.neurons) return;

  const n  = msg.neurons.length;
  const dt = 1;   // pixels per ms of sim time advance
  rasterTime++;
  if (rasterTime > RASTER_W) {
    // scroll left
    const imgData = rasterCtx.getImageData(1, 0, RASTER_W - 1, RASTER_H);
    rasterCtx.putImageData(imgData, 0, 0);
    rasterCtx.clearRect(RASTER_W - 1, 0, 1, RASTER_H);
    rasterTime = RASTER_W;
  }

  const x = rasterTime - 1;
  rasterCtx.clearRect(x, 0, 1, RASTER_H);

  msg.spikes.forEach(sp => {
    // find neuron index
    const idx = msg.neurons.findIndex(nn => nn.id === sp.id);
    if (idx < 0) return;
    const y = Math.round(idx / Math.max(n - 1, 1) * (RASTER_H - 2));
    const isExc = msg.neurons[idx].exc;
    rasterCtx.fillStyle = isExc ? '#00ff88' : '#ff4466';
    rasterCtx.fillRect(x, y, 2, 2);
  });
}

// ── Button: clear charts ──────────────────────────────────────────────────────
document.getElementById('btn-clear-charts').addEventListener('click', clearAllCharts);
