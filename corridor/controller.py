from .contract import Contract, skeleton
from .state import State
from . import basic


class Controller:
    def __init__(self, reference):
        self.ref = reference
        self.state = State(reference)
        self.contract = Contract(reference)

    def process(self, packet):
        self.state.ingest(packet)
        decision = skeleton(packet, self.ref)
        decision['state_estimates'] = basic.roads(self.state)
        basic.policy(self.state, decision)
        self.contract.validate(decision)
        self.state.remember(decision)
        return decision
