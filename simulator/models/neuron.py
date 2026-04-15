"""
Neuron models for the X-Neuro simulator.

Implements three biophysically grounded models:
  - Hodgkin-Huxley (HH)   : full ionic channel dynamics, high accuracy
  - Leaky Integrate-and-Fire (LIF): efficient, suitable for large networks
  - Izhikevich (IZH)      : 2-variable model capturing 20+ firing patterns

All voltages in mV, time in ms, currents in μA/cm².
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NeuronModel(str, Enum):
    HODGKIN_HUXLEY = "hh"
    LIF            = "lif"
    IZHIKEVICH     = "izh"


class NeuronType(str, Enum):
    PYRAMIDAL   = "pyramidal"    # excitatory cortical, regular spiking
    INTERNEURON = "interneuron"  # inhibitory, fast spiking
    MOTOR       = "motor"        # alpha-motor neuron, large diameter
    SENSORY     = "sensory"      # thalamocortical relay
    PURKINJE    = "purkinje"     # cerebellar, inhibitory
    GRANULE     = "granule"      # cerebellar, tiny, high frequency
    CUSTOM      = "custom"


# ---------------------------------------------------------------------------
# Parameter dataclass
# ---------------------------------------------------------------------------

@dataclass
class NeuronParams:
    """All adjustable parameters for a single neuron."""

    # --- identity ---
    model:       NeuronModel = NeuronModel.IZHIKEVICH
    neuron_type: NeuronType  = NeuronType.PYRAMIDAL
    excitatory:  bool        = True          # True = excitatory, False = inhibitory

    # --- generic membrane ---
    v_rest:      float = -70.0   # mV  resting membrane potential
    v_threshold: float = -55.0   # mV  spike threshold (used in LIF & display)
    v_peak:      float =  30.0   # mV  action-potential peak
    v_reset:     float = -65.0   # mV  post-spike reset
    tau_m:       float =  20.0   # ms  membrane time constant (LIF)
    tau_ref:     float =   2.0   # ms  absolute refractory period
    r_m:         float =  10.0   # MΩ  membrane resistance (LIF)

    # --- HH model ---
    c_m:  float = 1.0          # μF/cm²  membrane capacitance
    g_na: float = 120.0        # mS/cm²  max Na⁺ conductance
    g_k:  float =  36.0        # mS/cm²  max K⁺  conductance
    g_l:  float =   0.3        # mS/cm²  leak conductance
    e_na: float =  50.0        # mV  Na⁺  reversal potential
    e_k:  float = -77.0        # mV  K⁺   reversal potential
    e_l:  float = -54.387      # mV  leak reversal potential

    # --- Izhikevich parameters ---
    izh_a: float = 0.02   # time scale of recovery variable u
    izh_b: float = 0.20   # sensitivity of u to sub-threshold v
    izh_c: float = -65.0  # mV  after-spike reset of v
    izh_d: float =   8.0  # after-spike increment of u

    # --- axon / cable ---
    axon_length:   float = 1.0    # mm
    axon_diameter: float = 1.0    # μm
    myelinated:    bool  = False

    # --- stimulation ---
    i_ext:       float = 0.0   # μA/cm²  external injected current
    noise_level: float = 0.5   # mV   Gaussian noise std added each step

    def to_dict(self) -> dict:
        return {
            "model":        self.model.value,
            "neuron_type":  self.neuron_type.value,
            "excitatory":   self.excitatory,
            "v_rest":       self.v_rest,
            "v_threshold":  self.v_threshold,
            "v_peak":       self.v_peak,
            "v_reset":      self.v_reset,
            "tau_m":        self.tau_m,
            "tau_ref":      self.tau_ref,
            "r_m":          self.r_m,
            "c_m":          self.c_m,
            "g_na":         self.g_na,
            "g_k":          self.g_k,
            "g_l":          self.g_l,
            "e_na":         self.e_na,
            "e_k":          self.e_k,
            "e_l":          self.e_l,
            "izh_a":        self.izh_a,
            "izh_b":        self.izh_b,
            "izh_c":        self.izh_c,
            "izh_d":        self.izh_d,
            "axon_length":  self.axon_length,
            "axon_diameter":self.axon_diameter,
            "myelinated":   self.myelinated,
            "i_ext":        self.i_ext,
            "noise_level":  self.noise_level,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NeuronParams":
        p = cls()
        if "model"        in d: p.model        = NeuronModel(d["model"])
        if "neuron_type"  in d: p.neuron_type  = NeuronType(d["neuron_type"])
        for key in ("excitatory","v_rest","v_threshold","v_peak","v_reset",
                    "tau_m","tau_ref","r_m","c_m","g_na","g_k","g_l",
                    "e_na","e_k","e_l","izh_a","izh_b","izh_c","izh_d",
                    "axon_length","axon_diameter","myelinated","i_ext","noise_level"):
            if key in d:
                setattr(p, key, d[key])
        return p


# ---------------------------------------------------------------------------
# Biological presets
# ---------------------------------------------------------------------------

NEURON_PRESETS: dict[NeuronType, NeuronParams] = {
    NeuronType.PYRAMIDAL: NeuronParams(
        neuron_type=NeuronType.PYRAMIDAL, excitatory=True,
        v_rest=-70.0, v_threshold=-55.0, v_reset=-65.0, tau_m=20.0,
        izh_a=0.02, izh_b=0.20, izh_c=-65.0, izh_d=8.0,   # regular spiking
        axon_diameter=2.0, myelinated=True,
    ),
    NeuronType.INTERNEURON: NeuronParams(
        neuron_type=NeuronType.INTERNEURON, excitatory=False,
        v_rest=-70.0, v_threshold=-52.0, v_reset=-65.0, tau_m=10.0,
        izh_a=0.10, izh_b=0.20, izh_c=-65.0, izh_d=2.0,   # fast spiking
        axon_diameter=0.5, myelinated=False,
    ),
    NeuronType.MOTOR: NeuronParams(
        neuron_type=NeuronType.MOTOR, excitatory=True,
        v_rest=-65.0, v_threshold=-50.0, v_reset=-60.0, tau_m=15.0,
        izh_a=0.02, izh_b=0.20, izh_c=-65.0, izh_d=6.0,
        axon_diameter=12.0, myelinated=True,
    ),
    NeuronType.SENSORY: NeuronParams(
        neuron_type=NeuronType.SENSORY, excitatory=True,
        v_rest=-70.0, v_threshold=-55.0, v_reset=-65.0, tau_m=8.0,
        izh_a=0.02, izh_b=0.25, izh_c=-65.0, izh_d=0.05,  # thalamocortical
        axon_diameter=1.5, myelinated=True,
    ),
    NeuronType.PURKINJE: NeuronParams(
        neuron_type=NeuronType.PURKINJE, excitatory=False,
        v_rest=-68.0, v_threshold=-52.0, v_reset=-65.0, tau_m=25.0,
        izh_a=0.02, izh_b=0.20, izh_c=-65.0, izh_d=2.0,   # chattering
        axon_diameter=3.0, myelinated=True,
    ),
    NeuronType.GRANULE: NeuronParams(
        neuron_type=NeuronType.GRANULE, excitatory=True,
        v_rest=-75.0, v_threshold=-50.0, v_reset=-70.0, tau_m=5.0,
        izh_a=0.10, izh_b=0.20, izh_c=-65.0, izh_d=2.0,   # fast spiking
        axon_diameter=0.2, myelinated=False, noise_level=0.2,
    ),
}


# ---------------------------------------------------------------------------
# Helper: HH alpha/beta rate functions
# ---------------------------------------------------------------------------

def _safe_div(num: float, denom: float, fallback: float) -> float:
    """Avoid division by zero in HH rate functions."""
    return fallback if abs(denom) < 1e-7 else num / denom


def _hh_alpha_m(v: float) -> float:
    return _safe_div(0.1 * (v + 40.0), 1.0 - math.exp(-(v + 40.0) / 10.0), 1.0)

def _hh_beta_m(v: float) -> float:
    return 4.0 * math.exp(-(v + 65.0) / 18.0)

def _hh_alpha_h(v: float) -> float:
    return 0.07 * math.exp(-(v + 65.0) / 20.0)

def _hh_beta_h(v: float) -> float:
    return 1.0 / (1.0 + math.exp(-(v + 35.0) / 10.0))

def _hh_alpha_n(v: float) -> float:
    return _safe_div(0.01 * (v + 55.0), 1.0 - math.exp(-(v + 55.0) / 10.0), 0.1)

def _hh_beta_n(v: float) -> float:
    return 0.125 * math.exp(-(v + 65.0) / 80.0)


# ---------------------------------------------------------------------------
# Neuron class
# ---------------------------------------------------------------------------

class Neuron:
    """
    A single neuron that can be simulated with the HH, LIF, or Izhikevich model.

    State variables per model:
      HH  : v, m, h, n  (membrane potential + 3 gating variables)
      LIF : v, ref_time  (membrane potential + refractory counter)
      IZH : v, u         (membrane potential + recovery variable)
    """

    def __init__(self, neuron_id: int, params: Optional[NeuronParams] = None,
                 x: float = 0.0, y: float = 0.0):
        self.id     = neuron_id
        self.params = params or NeuronParams()
        self.x      = x        # display position (px)
        self.y      = y

        # --- state ---
        self.v: float = self.params.v_rest
        self.firing: bool = False
        self.spike_count: int = 0
        self.last_spike_t: float = -1e9   # ms

        # model-specific state
        self._m: float = 0.05   # HH Na activation
        self._h: float = 0.60   # HH Na inactivation
        self._n: float = 0.32   # HH K  activation
        self._u: float = self.params.izh_b * self.params.v_rest  # Izhikevich
        self._ref_remaining: float = 0.0  # LIF refractory time remaining

        # incoming synaptic current accumulator (reset each step)
        self.i_syn: float = 0.0

        # short history for charts (ring buffer, last 200 ms at 0.1 ms res)
        self._v_history: list[float] = [self.v] * 200
        self._hist_idx: int = 0
        self._hist_counter: int = 0  # sub-step counter

    # ------------------------------------------------------------------
    # Step functions
    # ------------------------------------------------------------------

    def step(self, dt: float, t: float) -> bool:
        """Advance state by dt ms. Returns True if a spike occurred."""
        p = self.params
        noise = random.gauss(0.0, p.noise_level) if p.noise_level > 0 else 0.0
        i_total = p.i_ext + self.i_syn + noise

        if p.model == NeuronModel.HODGKIN_HUXLEY:
            fired = self._step_hh(dt, i_total, t)
        elif p.model == NeuronModel.LIF:
            fired = self._step_lif(dt, i_total, t)
        else:
            fired = self._step_izh(dt, i_total, t)

        self.i_syn  = 0.0   # reset synaptic current
        self.firing = fired
        if fired:
            self.spike_count += 1
            self.last_spike_t = t

        # history (sample every 1 ms ÷ dt steps)
        self._hist_counter += 1
        if self._hist_counter >= max(1, int(round(1.0 / dt))):
            self._hist_counter = 0
            self._v_history[self._hist_idx] = self.v
            self._hist_idx = (self._hist_idx + 1) % len(self._v_history)

        return fired

    # --- Hodgkin-Huxley ---
    def _step_hh(self, dt: float, i_total: float, t: float) -> bool:
        p = self.params
        v, m, h, n = self.v, self._m, self._h, self._n

        I_Na = p.g_na * m**3 * h * (v - p.e_na)
        I_K  = p.g_k  * n**4     * (v - p.e_k)
        I_L  = p.g_l              * (v - p.e_l)

        dv = (i_total - I_Na - I_K - I_L) / p.c_m
        dm = _hh_alpha_m(v) * (1 - m) - _hh_beta_m(v) * m
        dh = _hh_alpha_h(v) * (1 - h) - _hh_beta_h(v) * h
        dn = _hh_alpha_n(v) * (1 - n) - _hh_beta_n(v) * n

        self.v   = v + dv * dt
        self._m  = max(0.0, min(1.0, m + dm * dt))
        self._h  = max(0.0, min(1.0, h + dh * dt))
        self._n  = max(0.0, min(1.0, n + dn * dt))

        fired = (v < p.v_peak) and (self.v >= p.v_peak)
        return fired

    # --- Leaky Integrate-and-Fire ---
    def _step_lif(self, dt: float, i_total: float, t: float) -> bool:
        p = self.params
        if self._ref_remaining > 0:
            self._ref_remaining -= dt
            self.v = p.v_reset
            return False

        dv = (-(self.v - p.v_rest) + p.r_m * i_total) / p.tau_m
        self.v += dv * dt

        if self.v >= p.v_threshold:
            self.v = p.v_peak
            self._ref_remaining = p.tau_ref
            return True
        return False

    # --- Izhikevich ---
    def _step_izh(self, dt: float, i_total: float, t: float) -> bool:
        p  = self.params
        v  = self.v
        u  = self._u
        # scale i_total to pA-like range the Izhikevich model expects
        I  = i_total * 10.0

        dv = (0.04 * v * v + 5.0 * v + 140.0 - u + I)
        du = p.izh_a * (p.izh_b * v - u)

        self.v   = v + dv * dt
        self._u  = u + du * dt

        if self.v >= p.v_peak:
            self.v  = p.izh_c
            self._u = self._u + p.izh_d
            return True
        return False

    # ------------------------------------------------------------------
    # Conduction velocity (m/s) based on axon properties
    # ------------------------------------------------------------------

    @property
    def conduction_velocity(self) -> float:
        """Estimate axonal conduction velocity in m/s."""
        d = self.params.axon_diameter   # μm
        if self.params.myelinated:
            # empirical: ~6 × diameter (μm) → m/s
            return max(1.0, 6.0 * d)
        else:
            # unmyelinated: ~0.57 × sqrt(diameter)
            return max(0.2, 0.57 * math.sqrt(d))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_state_dict(self) -> dict:
        """Compact snapshot for WebSocket streaming."""
        return {
            "id":      self.id,
            "v":       round(self.v, 2),
            "firing":  self.firing,
            "spikes":  self.spike_count,
            "i_ext":   self.params.i_ext,
            "type":    self.params.neuron_type.value,
            "exc":     self.params.excitatory,
            "x":       self.x,
            "y":       self.y,
        }

    def to_full_dict(self) -> dict:
        """Full description including params, for config panel."""
        d = self.to_state_dict()
        d["params"] = self.params.to_dict()
        # voltage history (ordered oldest→newest)
        idx = self._hist_idx
        h = self._v_history
        d["v_history"] = h[idx:] + h[:idx]
        return d

    def reset(self) -> None:
        p = self.params
        self.v             = p.v_rest
        self._m            = 0.05
        self._h            = 0.60
        self._n            = 0.32
        self._u            = p.izh_b * p.v_rest
        self._ref_remaining= 0.0
        self.firing        = False
        self.spike_count   = 0
        self.last_spike_t  = -1e9
        self.i_syn         = 0.0
        self._v_history    = [p.v_rest] * 200
        self._hist_idx     = 0
        self._hist_counter = 0
