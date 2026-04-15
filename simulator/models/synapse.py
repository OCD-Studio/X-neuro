"""
Synaptic transmission model for X-Neuro.

Features:
  - AMPA, NMDA, GABA-A, GABA-B receptor subtypes
  - Alpha-function conductance waveform
  - Distance-dependent signal attenuation (cable theory)
  - Axonal propagation delay
  - Synaptic weight plasticity (STDP ready)

Voltage in mV, time in ms, conductance in nS.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Synapse subtypes
# ---------------------------------------------------------------------------

class SynapseType(str, Enum):
    AMPA   = "ampa"    # fast excitatory  (τ ≈ 2 ms,  E_rev =   0 mV)
    NMDA   = "nmda"    # slow excitatory  (τ ≈ 100 ms, E_rev =   0 mV)
    GABA_A = "gaba_a"  # fast inhibitory  (τ ≈ 6 ms,  E_rev = -70 mV)
    GABA_B = "gaba_b"  # slow inhibitory  (τ ≈ 150 ms, E_rev = -90 mV)
    CUSTOM = "custom"


# Default biophysical parameters per subtype
_SYNAPSE_DEFAULTS = {
    SynapseType.AMPA:   {"tau": 2.0,   "e_rev": 0.0,   "g_max": 1.0},
    SynapseType.NMDA:   {"tau": 100.0, "e_rev": 0.0,   "g_max": 0.5},
    SynapseType.GABA_A: {"tau": 6.0,   "e_rev": -70.0, "g_max": 1.2},
    SynapseType.GABA_B: {"tau": 150.0, "e_rev": -90.0, "g_max": 0.8},
    SynapseType.CUSTOM: {"tau": 5.0,   "e_rev": 0.0,   "g_max": 1.0},
}


# ---------------------------------------------------------------------------
# Space constant (cable theory) – signal attenuation with distance
# ---------------------------------------------------------------------------

def space_constant(axon_diameter_um: float, myelinated: bool) -> float:
    """
    Electrotonic space constant λ in mm.

    Cable theory: λ = sqrt(r_m / r_i)
    Approximate empirical values:
      myelinated  : λ ≈ 2.0 × diameter^0.5  (mm, diameter in μm)
      unmyelinated: λ ≈ 0.5 × diameter^0.5
    """
    d = max(0.1, axon_diameter_um)
    if myelinated:
        return 2.0 * math.sqrt(d)
    return 0.5 * math.sqrt(d)


def attenuation(distance_mm: float, lambda_mm: float) -> float:
    """
    Voltage attenuation factor V(x)/V(0) = e^(-x/λ).
    Returns a value in [0, 1].
    """
    if lambda_mm <= 0:
        return 0.0
    return math.exp(-distance_mm / lambda_mm)


def propagation_delay(distance_mm: float, velocity_ms: float) -> float:
    """
    Axonal delay in ms.
    velocity_ms: conduction velocity in m/s (= mm/ms).
    """
    return distance_mm / max(velocity_ms, 0.001)


# ---------------------------------------------------------------------------
# Pending spike event in the axon
# ---------------------------------------------------------------------------

@dataclass
class SpikeEvent:
    arrive_t: float    # simulation time (ms) when spike reaches post-synaptic neuron
    weight:   float    # effective weight after attenuation


# ---------------------------------------------------------------------------
# Synapse
# ---------------------------------------------------------------------------

class Synapse:
    """
    A directed chemical synapse from neuron `src_id` → `tgt_id`.

    Conductance follows an alpha-function waveform:
        g(t) = g_max * w * (t/τ) * exp(1 - t/τ)    for t ≥ 0
    which peaks at t = τ with value g_max * w.

    The current injected into the post-synaptic membrane:
        I_syn = g(t) * (V_post - E_rev)
    is accumulated in neuron.i_syn at each step.
    """

    def __init__(
        self,
        src_id:    int,
        tgt_id:    int,
        weight:    float             = 1.0,
        syn_type:  SynapseType       = SynapseType.AMPA,
        distance:  float             = 0.5,   # mm
        # axon properties (taken from pre-synaptic neuron at construction)
        axon_diameter: float         = 1.0,   # μm
        myelinated:    bool          = False,
        # optional overrides
        tau:       Optional[float]   = None,
        e_rev:     Optional[float]   = None,
        g_max:     Optional[float]   = None,
    ):
        self.src_id   = src_id
        self.tgt_id   = tgt_id
        self.weight   = weight      # synaptic efficacy (0–5 typical)
        self.syn_type = syn_type
        self.distance = distance    # mm  distance between soma centres

        defaults = _SYNAPSE_DEFAULTS[syn_type]
        self.tau   = tau   if tau   is not None else defaults["tau"]
        self.e_rev = e_rev if e_rev is not None else defaults["e_rev"]
        self.g_max = g_max if g_max is not None else defaults["g_max"]

        # cable properties
        self.axon_diameter = axon_diameter
        self.myelinated    = myelinated
        self._lambda       = space_constant(axon_diameter, myelinated)
        self._atten        = attenuation(distance, self._lambda)

        # active conductance state variable (alpha-function integration)
        self._g: float = 0.0    # current conductance (nS)
        self._dg: float = 0.0   # first derivative (for alpha function ODE)

        # spike event queue (delayed transmission)
        self._pending: list[SpikeEvent] = []

        # state flags for visualisation
        self.active: bool  = False
        self.pulse_t: float = -1e9   # time of last triggered pulse

    # ------------------------------------------------------------------
    # Recompute cable properties when distance or diameter changes
    # ------------------------------------------------------------------

    def update_cable(self, axon_diameter: float, myelinated: bool) -> None:
        self.axon_diameter = axon_diameter
        self.myelinated    = myelinated
        self._lambda       = space_constant(axon_diameter, myelinated)
        self._atten        = attenuation(self.distance, self._lambda)

    # ------------------------------------------------------------------
    # Conduction velocity (needs to be supplied by pre-synaptic neuron)
    # ------------------------------------------------------------------

    def _delay(self, velocity: float) -> float:
        """Propagation delay in ms given conduction velocity in m/s."""
        return propagation_delay(self.distance, velocity)

    # ------------------------------------------------------------------
    # Trigger spike from pre-synaptic neuron
    # ------------------------------------------------------------------

    def receive_spike(self, t: float, conduction_velocity: float) -> None:
        """Called when the pre-synaptic neuron fires at time t."""
        delay = self._delay(conduction_velocity)
        eff_weight = self.weight * self._atten
        self._pending.append(SpikeEvent(arrive_t=t + delay, weight=eff_weight))
        self.pulse_t = t

    # ------------------------------------------------------------------
    # Advance synapse by dt ms, inject current into post-synaptic neuron
    # ------------------------------------------------------------------

    def step(self, dt: float, t: float, v_post: float) -> float:
        """
        Returns the synaptic current (μA/cm²) to be added to i_syn of the
        post-synaptic neuron.

        Implements the double-ODE form of the alpha function:
          dg/dt  = dg_dt
          ddg/dt = -2/τ * dg_dt  -  (1/τ)² * g  +  (e/τ²) * Σ w_i δ(t-t_i)
        Equivalent to: g(t) = Σ w_i * (e/τ) * (t-t_i) * exp(-(t-t_i)/τ)
        """
        # deliver pending spikes
        self.active = False
        remaining = []
        for ev in self._pending:
            if t >= ev.arrive_t:
                # kick the alpha function
                self._dg += ev.weight * self.g_max * math.e / self.tau
                self.active = True
            else:
                remaining.append(ev)
        self._pending = remaining

        # integrate alpha-function ODE
        inv_tau = 1.0 / self.tau
        ddg = -2.0 * inv_tau * self._dg - inv_tau * inv_tau * self._g
        self._dg += ddg * dt
        self._g  += self._dg * dt
        if self._g < 0:
            self._g = 0.0

        # synaptic current: I = g * (V - E_rev), scaled to μA/cm²
        I_syn = self._g * (v_post - self.e_rev) * 1e-3   # nS→μA/cm² scale
        return I_syn

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "src_id":   self.src_id,
            "tgt_id":   self.tgt_id,
            "weight":   round(self.weight, 4),
            "type":     self.syn_type.value,
            "distance": round(self.distance, 3),
            "tau":      self.tau,
            "e_rev":    self.e_rev,
            "g_max":    self.g_max,
            "atten":    round(self._atten, 4),
            "lambda":   round(self._lambda, 3),
            "active":   self.active,
        }

    def reset(self) -> None:
        self._g        = 0.0
        self._dg       = 0.0
        self._pending  = []
        self.active    = False
        self.pulse_t   = -1e9
