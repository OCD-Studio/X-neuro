"""
Neural network model for X-Neuro.

Manages neurons + synapses and provides topology generators:
  - Random (Erdős-Rényi)
  - Small-world (Watts-Strogatz)
  - Scale-free (Barabási-Albert)
  - Layered (feedforward / multi-layer)
  - Custom (manual add/remove)
"""

from __future__ import annotations
import math
import random
from typing import Optional

from .neuron  import Neuron, NeuronParams, NeuronType, NEURON_PRESETS, NeuronModel
from .synapse import Synapse, SynapseType


# ---------------------------------------------------------------------------
# Topology enum
# ---------------------------------------------------------------------------

class Topology(str):
    RANDOM      = "random"
    SMALL_WORLD = "small_world"
    SCALE_FREE  = "scale_free"
    LAYERED     = "layered"
    CUSTOM      = "custom"


# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------

class NetworkConfig:
    def __init__(
        self,
        n_neurons:    int        = 20,
        topology:     str        = Topology.RANDOM,
        connect_prob: float      = 0.20,   # Erdős-Rényi probability
        exc_ratio:    float      = 0.80,   # fraction of excitatory neurons
        sw_k:         int        = 4,      # Watts-Strogatz nearest neighbours
        sw_beta:      float      = 0.30,   # Watts-Strogatz rewire probability
        sf_m:         int        = 2,      # Barabási-Albert edges per new node
        layer_sizes:  list[int]  = None,   # for layered topology
        default_model: str       = "izh",
        max_distance:  float     = 3.0,    # mm max distance between neurons
        weight_mean:   float     = 1.0,
        weight_std:    float     = 0.3,
    ):
        self.n_neurons     = n_neurons
        self.topology      = topology
        self.connect_prob  = connect_prob
        self.exc_ratio     = exc_ratio
        self.sw_k          = sw_k
        self.sw_beta       = sw_beta
        self.sf_m          = sf_m
        self.layer_sizes   = layer_sizes or [5, 10, 5]
        self.default_model = default_model
        self.max_distance  = max_distance
        self.weight_mean   = weight_mean
        self.weight_std    = weight_std

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkConfig":
        c = cls()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c


# ---------------------------------------------------------------------------
# Neural network
# ---------------------------------------------------------------------------

class NeuralNetwork:
    """Container for neurons + synapses with topology generators."""

    def __init__(self):
        self.neurons:   list[Neuron]  = []
        self.synapses:  list[Synapse] = []
        self._syn_map:  dict[int, list[Synapse]] = {}   # src_id → outgoing synapses
        self.config:    NetworkConfig = NetworkConfig()
        self.t:         float = 0.0   # simulation time (ms)

    # ------------------------------------------------------------------
    # Build from config
    # ------------------------------------------------------------------

    def build(self, config: NetworkConfig) -> None:
        """Erase current network and build a new one from config."""
        self.config = config
        self.neurons  = []
        self.synapses = []
        self._syn_map = {}
        self.t        = 0.0

        if config.topology == Topology.LAYERED:
            self._build_layered(config)
        elif config.topology == Topology.SMALL_WORLD:
            self._build_small_world(config)
        elif config.topology == Topology.SCALE_FREE:
            self._build_scale_free(config)
        else:
            self._build_random(config)

        self._build_syn_map()

    # ------------------------------------------------------------------
    # Topology generators
    # ------------------------------------------------------------------

    def _make_neuron(self, nid: int, excitatory: bool,
                     config: NetworkConfig,
                     x: float = 0.0, y: float = 0.0) -> Neuron:
        ntype = (NeuronType.PYRAMIDAL if excitatory else NeuronType.INTERNEURON)
        params = NeuronParams.from_dict(NEURON_PRESETS[ntype].to_dict())
        params.model     = NeuronModel(config.default_model)
        params.excitatory = excitatory
        return Neuron(nid, params, x=x, y=y)

    def _place_circle(self, n: int, radius: float = 300.0):
        """Positions for n neurons on a circle."""
        cx, cy = 400.0, 300.0
        positions = []
        for i in range(n):
            angle = 2 * math.pi * i / n
            positions.append((cx + radius * math.cos(angle),
                               cy + radius * math.sin(angle)))
        return positions

    def _random_weight(self, config: NetworkConfig) -> float:
        return max(0.05, random.gauss(config.weight_mean, config.weight_std))

    def _random_distance(self, config: NetworkConfig) -> float:
        return random.uniform(0.1, config.max_distance)

    def _add_synapse(self, src: Neuron, tgt: Neuron,
                     weight: float, distance: float,
                     syn_type: Optional[SynapseType] = None) -> None:
        if syn_type is None:
            syn_type = SynapseType.AMPA if src.params.excitatory else SynapseType.GABA_A
        s = Synapse(
            src_id=src.id, tgt_id=tgt.id,
            weight=weight, syn_type=syn_type,
            distance=distance,
            axon_diameter=src.params.axon_diameter,
            myelinated=src.params.myelinated,
        )
        self.synapses.append(s)

    def _build_random(self, config: NetworkConfig) -> None:
        n = config.n_neurons
        positions = self._place_circle(n)
        for i in range(n):
            exc = random.random() < config.exc_ratio
            x, y = positions[i]
            self.neurons.append(self._make_neuron(i, exc, config, x, y))

        for src in self.neurons:
            for tgt in self.neurons:
                if src.id != tgt.id and random.random() < config.connect_prob:
                    dist = self._random_distance(config)
                    self._add_synapse(src, tgt, self._random_weight(config), dist)

    def _build_small_world(self, config: NetworkConfig) -> None:
        n  = config.n_neurons
        k  = min(config.sw_k, n - 1)
        positions = self._place_circle(n)
        for i in range(n):
            exc = random.random() < config.exc_ratio
            x, y = positions[i]
            self.neurons.append(self._make_neuron(i, exc, config, x, y))

        # ring lattice
        edges: set[tuple[int,int]] = set()
        for i in range(n):
            for j in range(1, k // 2 + 1):
                edges.add((i, (i + j) % n))
                edges.add((i, (i - j) % n))

        # rewire with probability beta
        rewired: set[tuple[int,int]] = set()
        for (i, j) in list(edges):
            if random.random() < config.sw_beta:
                candidates = [x for x in range(n) if x != i and (i, x) not in edges]
                if candidates:
                    new_j = random.choice(candidates)
                    rewired.add((i, new_j))
                    continue
            rewired.add((i, j))

        for (src_id, tgt_id) in rewired:
            src = self.neurons[src_id]
            tgt = self.neurons[tgt_id]
            dist = self._random_distance(config)
            self._add_synapse(src, tgt, self._random_weight(config), dist)

    def _build_scale_free(self, config: NetworkConfig) -> None:
        n = config.n_neurons
        m = min(config.sf_m, n - 1)
        positions = self._place_circle(n, radius=280.0)

        # seed with m+1 fully connected nodes
        seed = min(m + 1, n)
        for i in range(seed):
            exc = random.random() < config.exc_ratio
            x, y = positions[i]
            self.neurons.append(self._make_neuron(i, exc, config, x, y))
        for i in range(seed):
            for j in range(seed):
                if i != j:
                    dist = self._random_distance(config)
                    self._add_synapse(self.neurons[i], self.neurons[j],
                                      self._random_weight(config), dist)

        # preferential attachment
        degrees = [seed - 1] * seed
        for i in range(seed, n):
            exc = random.random() < config.exc_ratio
            x, y = positions[i]
            new_neuron = self._make_neuron(i, exc, config, x, y)
            self.neurons.append(new_neuron)

            total_deg = sum(degrees)
            if total_deg == 0:
                probs = [1.0 / len(degrees)] * len(degrees)
            else:
                probs = [d / total_deg for d in degrees]

            # select m targets by preferential attachment (no replacement)
            targets: set[int] = set()
            attempts = 0
            while len(targets) < m and attempts < n * 10:
                r = random.random()
                cumsum = 0.0
                for idx, p in enumerate(probs):
                    cumsum += p
                    if r < cumsum:
                        targets.add(idx)
                        break
                attempts += 1

            for tgt_id in targets:
                dist = self._random_distance(config)
                self._add_synapse(new_neuron, self.neurons[tgt_id],
                                  self._random_weight(config), dist)
                degrees[tgt_id] += 1
            degrees.append(len(targets))

    def _build_layered(self, config: NetworkConfig) -> None:
        layer_sizes = config.layer_sizes
        positions_by_layer: list[list[tuple]] = []
        nid = 0
        total_w, total_h = 800.0, 600.0
        n_layers = len(layer_sizes)

        for li, size in enumerate(layer_sizes):
            x = 80.0 + (total_w - 160.0) * li / max(n_layers - 1, 1)
            col_positions = []
            for ni in range(size):
                y = 60.0 + (total_h - 120.0) * ni / max(size - 1, 1)
                col_positions.append((x, y))
            positions_by_layer.append(col_positions)

        # create neurons
        neuron_layers: list[list[Neuron]] = []
        for li, size in enumerate(layer_sizes):
            layer = []
            is_last = li == n_layers - 1
            for ni in range(size):
                exc = (li < n_layers - 1) or (random.random() < config.exc_ratio)
                x, y = positions_by_layer[li][ni]
                n = self._make_neuron(nid, exc, config, x, y)
                # first layer = sensory, middle = interneuron/pyramidal, last = motor
                if li == 0:
                    n.params.neuron_type = NeuronType.SENSORY
                elif is_last:
                    n.params.neuron_type = NeuronType.MOTOR
                self.neurons.append(n)
                layer.append(n)
                nid += 1
            neuron_layers.append(layer)

        # connect adjacent layers
        for li in range(len(neuron_layers) - 1):
            src_layer = neuron_layers[li]
            tgt_layer = neuron_layers[li + 1]
            for src in src_layer:
                for tgt in tgt_layer:
                    if random.random() < config.connect_prob:
                        dist = self._random_distance(config)
                        self._add_synapse(src, tgt, self._random_weight(config), dist)

    # ------------------------------------------------------------------
    # Synapse lookup map
    # ------------------------------------------------------------------

    def _build_syn_map(self) -> None:
        self._syn_map = {n.id: [] for n in self.neurons}
        for s in self.synapses:
            self._syn_map.setdefault(s.src_id, []).append(s)

    def outgoing_synapses(self, neuron_id: int) -> list[Synapse]:
        return self._syn_map.get(neuron_id, [])

    # ------------------------------------------------------------------
    # Manual add / remove
    # ------------------------------------------------------------------

    def add_neuron(self, params: Optional[NeuronParams] = None,
                   x: float = 400.0, y: float = 300.0) -> Neuron:
        nid = max((n.id for n in self.neurons), default=-1) + 1
        n = Neuron(nid, params or NeuronParams(), x=x, y=y)
        self.neurons.append(n)
        self._syn_map[nid] = []
        return n

    def remove_neuron(self, neuron_id: int) -> None:
        self.neurons   = [n for n in self.neurons   if n.id != neuron_id]
        self.synapses  = [s for s in self.synapses  if s.src_id != neuron_id and s.tgt_id != neuron_id]
        self._build_syn_map()

    def add_synapse(self, src_id: int, tgt_id: int,
                    weight: float = 1.0,
                    syn_type: SynapseType = SynapseType.AMPA,
                    distance: float = 1.0) -> Optional[Synapse]:
        src = self.get_neuron(src_id)
        tgt = self.get_neuron(tgt_id)
        if src is None or tgt is None:
            return None
        s = Synapse(src_id, tgt_id, weight, syn_type, distance,
                    src.params.axon_diameter, src.params.myelinated)
        self.synapses.append(s)
        self._syn_map.setdefault(src_id, []).append(s)
        return s

    def remove_synapse(self, src_id: int, tgt_id: int) -> None:
        self.synapses = [s for s in self.synapses
                         if not (s.src_id == src_id and s.tgt_id == tgt_id)]
        self._build_syn_map()

    def get_neuron(self, nid: int) -> Optional[Neuron]:
        for n in self.neurons:
            if n.id == nid:
                return n
        return None

    def get_synapse(self, src_id: int, tgt_id: int) -> Optional[Synapse]:
        for s in self.synapses:
            if s.src_id == src_id and s.tgt_id == tgt_id:
                return s
        return None

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self, window_ms: float = 1000.0) -> dict:
        """Network-level statistics."""
        n = len(self.neurons)
        if n == 0:
            return {}
        firing_rates = []
        mean_v = 0.0
        for neuron in self.neurons:
            rate = neuron.spike_count / max(self.t, 1.0) * 1000.0   # Hz
            firing_rates.append(round(rate, 2))
            mean_v += neuron.v
        mean_v /= n

        # synchrony: coefficient of variation of inter-neuron rate spread
        if len(firing_rates) > 1:
            mean_fr = sum(firing_rates) / len(firing_rates)
            std_fr  = math.sqrt(sum((r - mean_fr)**2 for r in firing_rates) / len(firing_rates))
            synchrony = 1.0 - min(1.0, std_fr / (mean_fr + 1e-6))
        else:
            synchrony = 1.0

        exc_count = sum(1 for n in self.neurons if n.params.excitatory)
        return {
            "n_neurons":    n,
            "n_synapses":   len(self.synapses),
            "exc_count":    exc_count,
            "inh_count":    n - exc_count,
            "mean_v":       round(mean_v, 2),
            "total_spikes": sum(n.spike_count for n in self.neurons),
            "firing_rates": firing_rates,
            "synchrony":    round(synchrony, 3),
            "sim_time_ms":  round(self.t, 1),
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_full_dict(self) -> dict:
        return {
            "neurons":  [n.to_full_dict() for n in self.neurons],
            "synapses": [s.to_dict()       for s in self.synapses],
            "config":   self.config.to_dict(),
            "stats":    self.stats(),
        }

    def reset(self) -> None:
        self.t = 0.0
        for n in self.neurons:
            n.reset()
        for s in self.synapses:
            s.reset()
