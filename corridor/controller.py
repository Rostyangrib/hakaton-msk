from .contract import Contract, skeleton
from .state import State
from . import basic
from .trust import Trust
from .fusion import Fusion
from . import odd
from .routing import Router
from . import resources
from . import guard


class Controller:
    def __init__(self, reference):
        self.ref = reference
        self.state = State(reference)
        self.contract = Contract(reference)
        self.trust = Trust()

    def process(self, packet):
        self.state.ingest(packet)
        decision = skeleton(packet, self.ref)
        try:
            decision['source_assessments'] = self.trust.assess(self.state)
            self.fusion = Fusion(self.state, decision['source_assessments'], self.trust.excluded)
            decision['state_estimates'] = self.fusion.road_estimates()
            basic.policy(self.state, decision)
            decision['vehicle_assessments'] = odd.assessments(self.state,self.fusion,decision)
            self.router = Router(self.state,self.fusion)
            self.router.apply(decision)
            resources.apply(self.state,self.fusion,self.router,decision)
            decision=guard.finalize(self.state,self.fusion,self.router,self.contract,packet,decision)
        except Exception as exc:
            self.state.diagnostics.append('planner_exception:'+type(exc).__name__)
            for action in decision['vehicle_actions']: guard.hold(action)
            try: self.contract.validate(decision)
            except Exception: decision=skeleton(packet,self.ref)
            guard.diagnostic_support(self.state,decision)
            self.contract.validate(decision)
        self.state.remember(decision)
        return decision
