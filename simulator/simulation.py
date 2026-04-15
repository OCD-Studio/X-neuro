"""
Simulation engine for X-Neuro.

Runs the neural network forward in time using Euler integration.
Supports:
  - Variable timestep (dt)
  - External stimulus injection (pulse, ramp, sinusoidal, random)
  - Spike event collection
  - Real-time state broadcast via a callback
"""

from __future__ import annotations
import asyncio
import math
import random
import threading
import time as wall_time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .models.network import NeuralNetwork


# ---------------------------------------------------------------------------
# Stimulus types
# ---------------------------------------------------------------------------

class StimulusType(str):
    CONSTANT   = "constant"
    PULSE      = "pulse"      # on for duration_ms, then off
    RAMP       = "ramp"       # linearly ramp from 0 → amplitude over duration_ms
    SINUSOIDAL = "sinusoidal" # amplitude * sin(2π f t)
    RANDOM     = "random"     # random steps (Poisson-like)


@dataclass
class Stimulus:
    neuron_id:   int
    stim_type:   str   = StimulusType.PULSE
    amplitude:   float = 5.0    # μA/cm²
    duration_ms: float = 50.0   # ms (for PULSE / RAMP)
    frequency:   float = 10.0   # Hz (for SINUSOIDAL)
    start_t:     float = 0.0    # ms
    _active:     bool  = field(default=True, init=False)

    def current_at(self, t: float) -> float:
        """Returns injected current at time t."""
        rel = t - self.start_t
        if rel < 0:
            return 0.0
        if self.stim_type == StimulusType.CONSTANT:
            return self.amplitude
        if self.stim_type == StimulusType.PULSE:
            return self.amplitude if rel <= self.duration_ms else 0.0
        if self.stim_type == StimulusType.RAMP:
            if rel > self.duration_ms:
                return 0.0
            return self.amplitude * (rel / self.duration_ms)
        if self.stim_type == StimulusType.SINUSOIDAL:
            return self.amplitude * math.sin(2 * math.pi * self.frequency * rel * 1e-3)
        if self.stim_type == StimulusType.RANDOM:
            # Poisson-like: each ms has ~frequency*dt chance of a pulse
            return self.amplitude if random.random() < self.frequency * 1e-3 else 0.0
        return 0.0


# ---------------------------------------------------------------------------
# Spike record
# ---------------------------------------------------------------------------

@dataclass
class SpikeRecord:
    neuron_id: int
    t: float


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

class SimulationEngine:
    """
    Drives the NeuralNetwork forward in time.

    Usage (async):
        engine = SimulationEngine(network)
        engine.start()
        await asyncio.sleep(1.0)
        engine.pause()
    """

    # how many ms of simulation to advance per real second
    # (1000 → real-time; 5000 → 5× faster than real-time)
    SPEED_FACTOR: float = 2000.0

    def __init__(self, network: NeuralNetwork):
        self.network    = network
        self.dt: float  = 0.1      # ms   integration step
        self.running    = False
        self.paused     = False
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # stimuli
        self._stimuli: list[Stimulus] = []

        # spike buffer (cleared after each broadcast)
        self._spike_buffer: list[SpikeRecord] = []

        # broadcast callback: async fn(state_dict) called every broadcast_every_ms
        self._on_state: Optional[Callable] = None
        self.broadcast_every_ms: float = 20.0   # ms sim-time between broadcasts

        # STDP parameters
        self.stdp_enabled:  bool  = False
        self.stdp_a_plus:   float = 0.01    # LTP weight increment
        self.stdp_a_minus:  float = 0.012   # LTD weight decrement
        self.stdp_tau_plus: float = 20.0    # ms
        self.stdp_tau_minus:float = 20.0    # ms

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def set_callback(self, fn: Callable) -> None:
        self._on_state = fn

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.running = True
        self.paused  = False
        self._loop   = asyncio.get_event_loop()
        self._task   = asyncio.create_task(self._run())

    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.paused = False

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def reset(self) -> None:
        await self.stop()
        self.network.reset()
        self._stimuli     = []
        self._spike_buffer= []
        self.running = False
        self.paused  = False

    # ------------------------------------------------------------------
    # Stimuli
    # ------------------------------------------------------------------

    def add_stimulus(self, stim: Stimulus) -> None:
        stim.start_t = self.network.t
        self._stimuli.append(stim)

    def clear_stimuli(self) -> None:
        self._stimuli = []

    def _apply_stimuli(self) -> None:
        t = self.network.t
        # clear per-step external current
        for n in self.network.neurons:
            n.params.i_ext = 0.0
        # sum stimuli
        active_stimuli = []
        for stim in self._stimuli:
            I = stim.current_at(t)
            if I != 0.0 or (t - stim.start_t) <= stim.duration_ms:
                neuron = self.network.get_neuron(stim.neuron_id)
                if neuron:
                    neuron.params.i_ext += I
                active_stimuli.append(stim)
            # keep finished non-constant stimuli for display purposes, remove after 2×
            elif (t - stim.start_t) < stim.duration_ms * 2:
                active_stimuli.append(stim)
        self._stimuli = active_stimuli

    # ------------------------------------------------------------------
    # STDP
    # ------------------------------------------------------------------

    def _apply_stdp(self, fired_ids: set[int]) -> None:
        if not self.stdp_enabled or not fired_ids:
            return
        t = self.network.t
        for syn in self.network.synapses:
            src_fired = syn.src_id in fired_ids
            tgt_fired = syn.tgt_id in fired_ids
            src = self.network.get_neuron(syn.src_id)
            tgt = self.network.get_neuron(syn.tgt_id)
            if not src or not tgt:
                continue

            if src_fired and not tgt_fired:
                # pre before post → LTP
                dt_spike = t - tgt.last_spike_t
                if dt_spike > 0:
                    syn.weight += self.stdp_a_plus * math.exp(-dt_spike / self.stdp_tau_plus)

            if tgt_fired and not src_fired:
                # post before pre → LTD
                dt_spike = t - src.last_spike_t
                if dt_spike > 0:
                    syn.weight -= self.stdp_a_minus * math.exp(-dt_spike / self.stdp_tau_minus)

            # clamp weight
            syn.weight = max(0.0, min(10.0, syn.weight))

    # ------------------------------------------------------------------
    # Core integration loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        net = self.network
        dt  = self.dt
        last_broadcast = net.t
        real_dt_target = dt / self.SPEED_FACTOR   # real seconds per sim-step

        while self.running:
            if self.paused:
                await asyncio.sleep(0.02)
                continue

            step_start = wall_time.monotonic()

            # apply external stimuli
            self._apply_stimuli()

            # --- integrate one step ---
            fired_ids: set[int] = set()

            # 1. Advance all synapses and accumulate synaptic currents
            neuron_map = {n.id: n for n in net.neurons}
            for syn in net.synapses:
                tgt = neuron_map.get(syn.tgt_id)
                if tgt is None:
                    continue
                I = syn.step(dt, net.t, tgt.v)
                tgt.i_syn -= I   # subtract because convention: outward current positive

            # 2. Advance all neurons
            for neuron in net.neurons:
                fired = neuron.step(dt, net.t)
                if fired:
                    fired_ids.add(neuron.id)
                    self._spike_buffer.append(SpikeRecord(neuron.id, net.t))

            # 3. Deliver spikes to outgoing synapses
            for nid in fired_ids:
                neuron = neuron_map.get(nid)
                if neuron:
                    for syn in net.outgoing_synapses(nid):
                        syn.receive_spike(net.t, neuron.conduction_velocity)

            # 4. STDP weight update
            self._apply_stdp(fired_ids)

            net.t += dt

            # 5. Broadcast state
            if (net.t - last_broadcast) >= self.broadcast_every_ms:
                last_broadcast = net.t
                if self._on_state:
                    state = self._build_state()
                    try:
                        await self._on_state(state)
                    except Exception:
                        pass

            # throttle to avoid 100% CPU
            elapsed = wall_time.monotonic() - step_start
            sleep_s = real_dt_target - elapsed
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)

    # ------------------------------------------------------------------
    # State serialisation
    # ------------------------------------------------------------------

    def _build_state(self) -> dict:
        net = self.network
        spikes = [{"id": s.neuron_id, "t": round(s.t, 2)} for s in self._spike_buffer]
        self._spike_buffer = []

        return {
            "type":     "state",
            "t":        round(net.t, 2),
            "neurons":  [n.to_state_dict() for n in net.neurons],
            "synapses": [{"src": s.src_id, "tgt": s.tgt_id,
                          "w": round(s.weight, 3), "active": s.active}
                         for s in net.synapses],
            "spikes":   spikes,
            "stats":    net.stats(),
        }

    def get_full_state(self) -> dict:
        """Full network config + state (for initial load or reset)."""
        state = self._build_state()
        state["full"] = self.network.to_full_dict()
        return state
