"""
X-Neuro FastAPI server.

Endpoints:
  GET  /          → serves index.html
  GET  /static/*  → static assets
  GET  /api/state → full network state (JSON)
  POST /api/config → reconfigure / rebuild network
  POST /api/stimulate → inject stimulus
  POST /api/evolve   → run evolution
  WS   /ws        → real-time simulation stream
"""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from simulator.models.network  import NeuralNetwork, NetworkConfig
from simulator.models.neuron   import NeuronParams, NeuronType, NEURON_PRESETS
from simulator.models.synapse  import SynapseType
from simulator.simulation      import SimulationEngine, Stimulus, StimulusType
from simulator.evolution       import EvolutionEngine, EvolutionConfig, FITNESS_FNS


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent

app = FastAPI(title="X-Neuro", version="1.0.0")

# Static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Global simulation state (single-session)
# ---------------------------------------------------------------------------

network   = NeuralNetwork()
engine    = SimulationEngine(network)
evolution = EvolutionEngine(network)
_clients: set[WebSocket] = set()


# ---------------------------------------------------------------------------
# Broadcast helper
# ---------------------------------------------------------------------------

async def _broadcast(data: dict) -> None:
    if not _clients:
        return
    payload = json.dumps(data)
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _clients -= dead


engine.set_callback(_broadcast)


# ---------------------------------------------------------------------------
# Startup: build default network
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _startup():
    cfg = NetworkConfig(
        n_neurons=18, topology="random", connect_prob=0.22,
        exc_ratio=0.78, default_model="izh",
    )
    network.build(cfg)
    await engine.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(), status_code=200)


@app.get("/api/state")
async def get_state():
    return JSONResponse(engine.get_full_state())


@app.get("/api/presets")
async def get_presets():
    return {
        "neuron_types": [t.value for t in NeuronType],
        "synapse_types": [t.value for t in SynapseType],
        "stimulus_types": [
            StimulusType.CONSTANT, StimulusType.PULSE, StimulusType.RAMP,
            StimulusType.SINUSOIDAL, StimulusType.RANDOM,
        ],
        "fitness_fns": list(FITNESS_FNS.keys()),
        "topologies": ["random", "small_world", "scale_free", "layered"],
        "models": ["hh", "lif", "izh"],
    }


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------

class ConfigRequest(BaseModel):
    network: Optional[dict] = None
    neuron_id: Optional[int] = None
    neuron_params: Optional[dict] = None
    synapse: Optional[dict] = None
    simulation: Optional[dict] = None
    evolution: Optional[dict] = None


@app.post("/api/config")
async def configure(req: ConfigRequest):
    global network, engine, evolution

    if req.network:
        was_running = engine.running and not engine.paused
        await engine.stop()
        cfg = NetworkConfig.from_dict(req.network)
        network.build(cfg)
        engine    = SimulationEngine(network)
        evolution = EvolutionEngine(network)
        engine.set_callback(_broadcast)
        if was_running:
            await engine.start()
        state = engine.get_full_state()
        state["type"] = "network_reset"
        await _broadcast(state)
        return {"ok": True, "message": "Network rebuilt"}

    if req.neuron_id is not None and req.neuron_params:
        n = network.get_neuron(req.neuron_id)
        if n:
            n.params = NeuronParams.from_dict(req.neuron_params)
            return {"ok": True, "message": f"Neuron {req.neuron_id} updated"}
        return JSONResponse({"ok": False, "message": "Neuron not found"}, 404)

    if req.synapse:
        s = network.get_synapse(req.synapse["src_id"], req.synapse["tgt_id"])
        if s:
            if "weight"   in req.synapse: s.weight   = float(req.synapse["weight"])
            if "distance" in req.synapse:
                s.distance = float(req.synapse["distance"])
                s.update_cable(s.axon_diameter, s.myelinated)
            if "tau"      in req.synapse: s.tau      = float(req.synapse["tau"])
            return {"ok": True}
        return JSONResponse({"ok": False, "message": "Synapse not found"}, 404)

    if req.simulation:
        d = req.simulation
        if "dt"             in d: engine.dt                = float(d["dt"])
        if "speed"          in d: engine.SPEED_FACTOR      = float(d["speed"])
        if "broadcast_ms"   in d: engine.broadcast_every_ms = float(d["broadcast_ms"])
        if "stdp_enabled"   in d: engine.stdp_enabled      = bool(d["stdp_enabled"])
        if "stdp_a_plus"    in d: engine.stdp_a_plus       = float(d["stdp_a_plus"])
        if "stdp_a_minus"   in d: engine.stdp_a_minus      = float(d["stdp_a_minus"])
        if "stdp_tau_plus"  in d: engine.stdp_tau_plus     = float(d["stdp_tau_plus"])
        if "stdp_tau_minus" in d: engine.stdp_tau_minus    = float(d["stdp_tau_minus"])
        return {"ok": True}

    if req.evolution:
        d = req.evolution
        cfg = evolution.config
        for k, v in d.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return {"ok": True}

    return {"ok": False, "message": "Nothing to configure"}


# ---------------------------------------------------------------------------
# Simulation control
# ---------------------------------------------------------------------------

class ControlRequest(BaseModel):
    action: str   # start | pause | resume | reset


@app.post("/api/control")
async def control(req: ControlRequest):
    if req.action == "start":
        await engine.start()
    elif req.action == "pause":
        await engine.pause()
    elif req.action == "resume":
        await engine.resume()
    elif req.action == "reset":
        await engine.reset()
        await engine.start()
        state = engine.get_full_state()
        state["type"] = "network_reset"
        await _broadcast(state)
    return {"ok": True, "action": req.action}


# ---------------------------------------------------------------------------
# Stimulus endpoint
# ---------------------------------------------------------------------------

class StimulusRequest(BaseModel):
    neuron_id:   int
    stim_type:   str   = "pulse"
    amplitude:   float = 8.0
    duration_ms: float = 100.0
    frequency:   float = 20.0


@app.post("/api/stimulate")
async def stimulate(req: StimulusRequest):
    stim = Stimulus(
        neuron_id=req.neuron_id,
        stim_type=req.stim_type,
        amplitude=req.amplitude,
        duration_ms=req.duration_ms,
        frequency=req.frequency,
    )
    engine.add_stimulus(stim)
    return {"ok": True, "neuron_id": req.neuron_id}


@app.post("/api/stimulate/all")
async def stimulate_all(req: StimulusRequest):
    for n in network.neurons:
        stim = Stimulus(
            neuron_id=n.id,
            stim_type=req.stim_type,
            amplitude=req.amplitude * (0.7 + 0.6 * __import__('random').random()),
            duration_ms=req.duration_ms,
            frequency=req.frequency,
        )
        engine.add_stimulus(stim)
    return {"ok": True, "count": len(network.neurons)}


# ---------------------------------------------------------------------------
# Evolution endpoint
# ---------------------------------------------------------------------------

class EvolveRequest(BaseModel):
    fitness_fn:     str   = "max_firing_rate"
    generations:    int   = 3
    mutation_rate:  float = 0.15
    mutation_scale: float = 0.10
    structural:     bool  = True


@app.post("/api/evolve")
async def evolve(req: EvolveRequest):
    was_paused = engine.paused
    await engine.pause()
    await asyncio.sleep(0.1)   # let current step finish

    evolution.config.fitness_fn    = req.fitness_fn
    evolution.config.generations   = req.generations
    evolution.config.mutation_rate = req.mutation_rate
    evolution.config.mutation_scale= req.mutation_scale
    evolution.config.structural    = req.structural

    results = evolution.run_full_evolution()

    if not was_paused:
        await engine.resume()

    await _broadcast({"type": "evolution_done", "results": results})
    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# Neuron add/remove
# ---------------------------------------------------------------------------

@app.post("/api/neuron/add")
async def add_neuron(body: dict):
    ntype = NeuronType(body.get("type", "pyramidal"))
    params = NeuronParams.from_dict(NEURON_PRESETS[ntype].to_dict())
    x = float(body.get("x", 400))
    y = float(body.get("y", 300))
    n = network.add_neuron(params, x=x, y=y)
    return {"ok": True, "id": n.id}


@app.delete("/api/neuron/{neuron_id}")
async def delete_neuron(neuron_id: int):
    network.remove_neuron(neuron_id)
    return {"ok": True}


@app.post("/api/synapse/add")
async def add_synapse(body: dict):
    s = network.add_synapse(
        src_id   = int(body["src_id"]),
        tgt_id   = int(body["tgt_id"]),
        weight   = float(body.get("weight", 1.0)),
        syn_type = SynapseType(body.get("type", "ampa")),
        distance = float(body.get("distance", 1.0)),
    )
    if s:
        return {"ok": True}
    return JSONResponse({"ok": False, "message": "Neurons not found"}, 400)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)

    # send full state on connect
    try:
        await ws.send_text(json.dumps({**engine.get_full_state(), "type": "init"}))
    except Exception:
        _clients.discard(ws)
        return

    try:
        while True:
            data = await ws.receive_text()
            msg  = json.loads(data)
            action = msg.get("action", "")

            if action == "ping":
                await ws.send_text(json.dumps({"type": "pong", "t": network.t}))

            elif action == "get_neuron":
                nid = msg.get("id")
                n = network.get_neuron(nid)
                if n:
                    await ws.send_text(json.dumps({
                        "type": "neuron_detail", "neuron": n.to_full_dict()
                    }))

    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
