from .contract import Contract, skeleton
from .state import State
from . import basic
from .trust import Trust
from .fusion import Fusion
from . import odd


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
        self.fusion = Fusion(self.state, decision['source_assessments'], self.trust.excluded)
        decision['state_estimates'] = self.fusion.road_estimates()
        basic.policy(self.state, decision)
        decision['vehicle_assessments'] = odd.assessments(self.state,self.fusion,decision)
        self.contract.validate(decision)
        self.state.remember(decision)
        return decision
