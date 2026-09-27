from .contract import Contract, skeleton
from .state import State


class Controller:
    def __init__(self, reference):
        self.ref = reference
        self.state = State(reference)
        self.contract = Contract(reference)

    def process(self, packet):
        self.state.ingest(packet)
        decision = skeleton(packet, self.ref)
        self.contract.validate(decision)
        self.state.remember(decision)
        return decision
