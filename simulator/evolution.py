"""
Evolutionary algorithms for X-Neuro.

Two strategies:
  1. Genetic Algorithm (GA) – evolves neuron parameter vectors
     to maximise a configurable fitness function.
  2. Structural Evolution – adds/removes neurons and synapses
     based on activity-dependent rules.

Fitness functions available:
  - max_firing_rate   : maximise mean firing rate
  - target_rate       : hit a target firing rate (Hz)
  - synchrony         : maximise population synchrony
  - diversity         : maximise firing-rate diversity
  - efficiency        : spikes per unit of total synaptic weight
"""

from __future__ import annotations
import copy
import math
import random
from dataclasses import dataclass
from typing import Callable, Optional

from .models.neuron  import Neuron, NeuronParams, NeuronModel
from .models.network import NeuralNetwork
from .models.synapse import Synapse, SynapseType


# ---------------------------------------------------------------------------
# Fitness functions
# ---------------------------------------------------------------------------

FitnessFn = Callable[[NeuralNetwork], float]


def _firing_rates(net: NeuralNetwork) -> list[float]:
    t = max(net.t, 1.0)
    return [n.spike_count / t * 1000.0 for n in net.neurons]


def fitness_max_rate(net: NeuralNetwork) -> float:
    rates = _firing_rates(net)
    return sum(rates) / max(len(rates), 1)


def fitness_target_rate(target_hz: float = 20.0) -> FitnessFn:
    def fn(net: NeuralNetwork) -> float:
        rates = _firing_rates(net)
        mean  = sum(rates) / max(len(rates), 1)
        return -abs(mean - target_hz)
    return fn


def fitness_synchrony(net: NeuralNetwork) -> float:
    return net.stats().get("synchrony", 0.0)


def fitness_diversity(net: NeuralNetwork) -> float:
    rates = _firing_rates(net)
    if len(rates) < 2:
        return 0.0
    mean = sum(rates) / len(rates)
    std  = math.sqrt(sum((r - mean)**2 for r in rates) / len(rates))
    return std / (mean + 1e-6)


def fitness_efficiency(net: NeuralNetwork) -> float:
    total_weight = sum(s.weight for s in net.synapses) + 1e-6
    total_spikes = sum(n.spike_count for n in net.neurons)
    return total_spikes / total_weight


FITNESS_FNS: dict[str, FitnessFn] = {
    "max_firing_rate": fitness_max_rate,
    "target_20hz":     fitness_target_rate(20.0),
    "target_10hz":     fitness_target_rate(10.0),
    "synchrony":       fitness_synchrony,
    "diversity":       fitness_diversity,
    "efficiency":      fitness_efficiency,
}


# ---------------------------------------------------------------------------
# Genome – a flat vector of mutable neuron parameters
# ---------------------------------------------------------------------------

MUTABLE_PARAMS = [
    "v_rest", "v_threshold", "v_peak", "v_reset",
    "tau_m", "tau_ref", "r_m",
    "g_na", "g_k", "g_l",
    "izh_a", "izh_b", "izh_c", "izh_d",
    "noise_level",
]

PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "v_rest":      (-90.0, -50.0),
    "v_threshold": (-65.0, -40.0),
    "v_peak":      (10.0,  60.0),
    "v_reset":     (-80.0, -50.0),
    "tau_m":       (2.0,   50.0),
    "tau_ref":     (0.5,   10.0),
    "r_m":         (1.0,   50.0),
    "g_na":        (50.0,  200.0),
    "g_k":         (10.0,  80.0),
    "g_l":         (0.05,  2.0),
    "izh_a":       (0.01,  0.5),
    "izh_b":       (0.1,   0.5),
    "izh_c":       (-80.0, -40.0),
    "izh_d":       (0.05,  20.0),
    "noise_level": (0.0,   3.0),
}


def _params_to_genome(p: NeuronParams) -> list[float]:
    return [getattr(p, k) for k in MUTABLE_PARAMS]


def _genome_to_params(genome: list[float], template: NeuronParams) -> NeuronParams:
    p = copy.deepcopy(template)
    for i, k in enumerate(MUTABLE_PARAMS):
        lo, hi = PARAM_BOUNDS[k]
        setattr(p, k, max(lo, min(hi, genome[i])))
    return p


def _mutate(genome: list[float], rate: float, scale: float) -> list[float]:
    """Gaussian mutation with probability `rate` per gene."""
    new = genome[:]
    for i, k in enumerate(MUTABLE_PARAMS):
        if random.random() < rate:
            lo, hi = PARAM_BOUNDS[k]
            sigma  = (hi - lo) * scale
            new[i] = max(lo, min(hi, genome[i] + random.gauss(0, sigma)))
    return new


def _crossover(g1: list[float], g2: list[float]) -> list[float]:
    """Uniform crossover."""
    return [g1[i] if random.random() < 0.5 else g2[i] for i in range(len(g1))]


# ---------------------------------------------------------------------------
# Evolution configuration
# ---------------------------------------------------------------------------

@dataclass
class EvolutionConfig:
    fitness_fn:      str   = "max_firing_rate"
    generations:     int   = 5
    mutation_rate:   float = 0.15    # probability of mutating each gene
    mutation_scale:  float = 0.10    # sigma as fraction of parameter range
    elite_fraction:  float = 0.20    # top fraction kept unchanged
    trial_ms:        float = 200.0   # ms to simulate per fitness evaluation

    # Structural evolution
    structural:            bool  = True
    add_neuron_threshold:  float = 0.05   # add if mean rate below this (Hz)
    prune_synapse_weight:  float = 0.05   # remove synapses below this weight
    prune_silent_neuron_ms: float = 2000.0  # remove neuron if silent for this long

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------
# Evolution engine
# ---------------------------------------------------------------------------

class EvolutionEngine:

    def __init__(self, network: NeuralNetwork):
        self.network = network
        self.config  = EvolutionConfig()
        self.history: list[dict] = []   # per-generation stats

    def run_generation(self) -> dict:
        """
        Run one generation of genetic evolution on neuron parameters.

        Each neuron is treated independently (population = all neurons).
        Fitter parameter sets survive; others are replaced by mutations.
        """
        cfg    = self.config
        net    = self.network
        fn     = FITNESS_FNS.get(cfg.fitness_fn, fitness_max_rate)
        n      = len(net.neurons)
        if n == 0:
            return {}

        # --- evaluate current fitness (score per neuron based on firing rate) ---
        t_total = max(net.t, 1.0)
        scores: list[tuple[float, int]] = []
        for neuron in net.neurons:
            # individual fitness: how well this neuron fires
            rate    = neuron.spike_count / t_total * 1000.0
            network_fitness = fn(net)
            # blend: 60% network fitness, 40% individual rate contribution
            score = 0.6 * network_fitness + 0.4 * rate
            scores.append((score, neuron.id))

        scores.sort(reverse=True)
        n_elite = max(1, int(n * cfg.elite_fraction))
        elite_ids = {nid for _, nid in scores[:n_elite]}

        # --- build new parameter sets ---
        genome_map = {n.id: _params_to_genome(n.params) for n in net.neurons}
        elite_genomes = [genome_map[nid] for nid in elite_ids if nid in genome_map]

        for neuron in net.neurons:
            if neuron.id in elite_ids:
                continue  # keep elite unchanged
            # select a parent from elite
            parent_genome = random.choice(elite_genomes)
            # optionally crossover with another elite
            if len(elite_genomes) > 1 and random.random() < 0.5:
                other = random.choice(elite_genomes)
                child_genome = _crossover(parent_genome, other)
            else:
                child_genome = parent_genome[:]
            # mutate
            child_genome = _mutate(child_genome, cfg.mutation_rate, cfg.mutation_scale)
            neuron.params = _genome_to_params(child_genome, neuron.params)

        best_score = scores[0][0] if scores else 0.0
        mean_score = sum(s for s, _ in scores) / max(len(scores), 1)
        result = {
            "best_fitness":  round(best_score, 4),
            "mean_fitness":  round(mean_score, 4),
            "elite_count":   n_elite,
            "fitness_fn":    cfg.fitness_fn,
        }
        self.history.append(result)
        return result

    def structural_evolution(self) -> dict:
        """
        Prune weak synapses and silence neurons.
        Optionally add neurons if network is under-active.
        """
        cfg    = self.config
        net    = self.network
        report = {"pruned_synapses": 0, "pruned_neurons": 0, "added_neurons": 0}

        if not cfg.structural:
            return report

        # prune weak synapses
        before = len(net.synapses)
        net.synapses = [s for s in net.synapses if s.weight >= cfg.prune_synapse_weight]
        net._build_syn_map()
        report["pruned_synapses"] = before - len(net.synapses)

        # prune silent neurons (never fired during the last N ms)
        silent_ids = [
            n.id for n in net.neurons
            if n.spike_count == 0 and net.t > cfg.prune_silent_neuron_ms
        ]
        for nid in silent_ids:
            net.remove_neuron(nid)
        report["pruned_neurons"] = len(silent_ids)

        # add neuron if mean rate is too low
        rates = [n.spike_count / max(net.t, 1.0) * 1000.0 for n in net.neurons]
        mean_rate = sum(rates) / max(len(rates), 1)
        if mean_rate < cfg.add_neuron_threshold and len(net.neurons) < 100:
            new_n = net.add_neuron(x=random.uniform(50, 750), y=random.uniform(50, 550))
            # connect it randomly to some existing neurons
            for other in random.sample(net.neurons[:-1], min(3, len(net.neurons) - 1)):
                net.add_synapse(other.id, new_n.id,
                                weight=random.uniform(0.5, 2.0),
                                distance=random.uniform(0.5, 2.0))
            report["added_neurons"] = 1

        return report

    def run_full_evolution(self) -> list[dict]:
        """Run config.generations generations and return history."""
        results = []
        for _ in range(self.config.generations):
            gen_result = self.run_generation()
            struct_result = self.structural_evolution()
            gen_result.update(struct_result)
            results.append(gen_result)
        return results
