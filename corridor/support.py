"""Snapshot support assignments with bounded switching and causal waiting age.

Assignments are recommendations, never evidence of an executed operator action.
Only the final emitted snapshot commits scheduling history.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SupportRequest:
    risk: int
    cargo: int
    eta: float
    exposed_hold: bool = False


class SupportScheduler:
    MIN_SERVICE_SEC = 30
    WAIT_BUCKET_SEC = 30

    def __init__(self):
        self.tickets = {}
        self.pending = {}

    def select(self, requests, limit, now):
        self.pending = dict(requests)

        def priority(vid):
            request = requests[vid]
            old = self.tickets.get(vid, {})
            incumbent = old.get('selected', False)
            protected = incumbent and now-old['selected_since'] < self.MIN_SERVICE_SEC
            waiting = 0 if incumbent else max(0, now-old.get('waiting_since', now))
            # Protection applies only within equal current risk/ETA.
            # A newly observed higher-priority situation always preempts it.
            return (request.risk, request.eta, not protected,
                    -int(waiting/self.WAIT_BUCKET_SEC), request.cargo, not incumbent, vid)

        return set(sorted(requests, key=priority)[:max(0, limit)])

    def commit(self, selected, now):
        tickets = {}
        for vid in self.pending:
            old = self.tickets.get(vid, {})
            active = vid in selected
            tickets[vid] = dict(
                selected=active,
                selected_since=old['selected_since'] if active and old.get('selected') else now,
                waiting_since=old['waiting_since'] if not active and old and not old['selected'] else now)
        self.tickets = tickets

    def waiting_seconds(self, vid, now):
        old = self.tickets.get(vid)
        return max(0, now-old['waiting_since']) if old and not old['selected'] else 0
