from .contract import Contract, skeleton
from .state import State
from . import basic
from .trust import Trust


class Controller:
    def __init__(self, reference):
        self.ref = reference
        self.state = State(reference)
        self.contract = Contract(reference)
        self.trust = Trust()

    def process(self, packet):
        self.state.ingest(packet)
        decision = skeleton(packet, self.ref)
        decision['source_assessments'] = self.trust.assess(self.state)
        decision['state_estimates'] = basic.roads(self.state)
        basic.policy(self.state, decision)
        self.contract.validate(decision)
        self.state.remember(decision)
        return decision
