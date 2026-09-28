from pathlib import Path

from .estimation import Estimator
from .models import Forest
from .observations import Observations
from .planning import Planner


class Engine:
    def __init__(self, reference, model_path=None):
        self.ref = reference
        self.model = Forest(model_path or Path(__file__).resolve().parents[1] / "models" / "forest.json")
        self.scenario = None
        self.cached_packet = None
        self.cached_decision = None
        self.reset()

    def reset(self):
        self.observations = Observations()
        self.estimator = Estimator(self.ref, self.observations, self.model)
        self.planner = Planner(self.ref)
        self.cached_packet = None
        self.cached_decision = None

    def process(self, packet):
        scenario = packet["scenario_id"]
        if scenario != self.scenario:
            self.reset()
            self.scenario = scenario
        if packet["packet_id"] == self.cached_packet:
            return self.cached_decision
        self.observations.ingest(packet)
        sources = self.estimator.estimate_sources()
        states = self.estimator.estimate_segments()
        vehicles = self.estimator.estimate_vehicles()
        for component, estimates, key in [("source", sources, "status"), ("segment", states, "state"), ("odd", vehicles, "odd_status")]:
            for estimate in estimates.values():
                estimate["confidence"] = self.model.calibrate(component, estimate[key], estimate["confidence"])
        actions = self.planner.plan(self.estimator)
        result = {"scenario_id": scenario, "packet_id": packet["packet_id"], "decision_time": packet.get("decision_time", packet.get("packet_time")), "state_estimates": [states[k] for k in sorted(states)], "source_assessments": [sources[k] for k in sorted(sources)], "vehicle_assessments": [vehicles[k] for k in sorted(vehicles)], "vehicle_actions": [actions[k] for k in sorted(actions)]}
        self.cached_packet = packet["packet_id"]
        self.cached_decision = result
        return result
