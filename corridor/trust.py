import math
from collections import defaultdict, deque
from statistics import median
from .state import timestamp
from .basic import freshness, road_vote


class Trust:
    """Bounded per-step Beta evidence and conservative, comparable FDIR."""
    def __init__(self):
        self.scenario = None
        self.evidence = defaultdict(deque)
        self.last_evidence = {}
        self.excluded = set()
        self.recovery = defaultdict(int)
        self.started = None
        self.generation = None
        self.frozen = {}

    def assess(self, state):
        if self.generation != state.generation or self.scenario != state.scenario_id or self.started is None or state.now < self.started:
            self.__init__()
            self.scenario = state.scenario_id
            self.started = state.now
            self.generation = state.generation
        output = []
        for sid, source in sorted(state.ref.sources.items()):
            period = float(source['expected_period_sec'])
            recent = [e for e in state.events() if e['source_id'] == sid]
            useful = [e for e in recent if e['event_type'] != 'INFRASTRUCTURE_HEALTH']
            health = [e for e in recent if e['event_type'] == 'INFRASTRUCTURE_HEALTH']
            faults = set()
            last_useful = max((timestamp(e['received_time']) for e in useful), default=self.started)
            last_health = max((timestamp(e['received_time']) for e in health), default=self.started)
            if state.now-last_useful > 3*period and state.now-last_health > max(30, 3*period): faults.add('OUTAGE')
            for e in recent:
                if not state.fresh(e, 120): continue
                clock = e.get('source_clock_offset_ms', e.get('time_sync_offset_ms', 0))
                if abs(clock) > 1000: faults.add('TIME_SKEW')
                delay = timestamp(e['received_time']) - timestamp(e['event_time']) + clock/1000
                if delay > max(5, 3*period): faults.add('DELAY')
                if e.get('snapshot_age_sec', 0) > freshness(state.ref, e): faults.add('STALE')
                if e.get('source_updated_time') and state.now-timestamp(e['source_updated_time']) > freshness(state.ref,e): faults.add('STALE')
                if e['event_type'] == 'INFRASTRUCTURE_HEALTH':
                    if e['status'] == 'NO_HEARTBEAT' and e['last_heartbeat_age_sec'] > max(10,3*period): faults.add('OUTAGE')
                    if e.get('network_packet_loss_pct', 0) >= 20: faults.add('PACKET_LOSS')
                if e.get('signature_valid') is False: faults.add('BYZANTINE')
            positive = False
            evidence_ids = set()
            residuals = []
            if sid in self.frozen:
                latest=max((e for e in useful if e['event_type']=='ROAD_OBSERVATION'),key=lambda e:timestamp(e['event_time']),default=None)
                signature=tuple(latest.get(f) for f in ('speed_kmh','flow_vph','occupancy_pct','queue_estimate_m')) if latest else None
                if signature==self.frozen[sid] and latest and state.now-timestamp(latest['received_time'])<=max(15,3*period): faults.add('FREEZE')
                else: self.frozen.pop(sid,None)
            for key, history in state.history.items():
                if key[2] != sid or key[0] != 'ROAD_OBSERVATION': continue
                relevant = [e for e in history if 0 <= state.now-timestamp(e['event_time']) <= 60]
                signatures = [tuple(e.get(f) for f in ('speed_kmh','flow_vph','occupancy_pct','queue_estimate_m')) for e in relevant]
                if signatures:
                    tail=1
                    while tail<len(signatures) and signatures[-tail-1]==signatures[-1]: tail+=1
                    span=timestamp(relevant[-1]['received_time'])-timestamp(relevant[-tail]['received_time'])
                    if tail>=3 and len(signatures)-tail>=3 and span>=max(30,6*period) and sum(v is not None for v in signatures[-1])>=3:
                        faults.add('FREEZE'); self.frozen[sid]=signatures[-1]
                if (len(relevant)>=3 and timestamp(relevant[-1]['event_time'])-timestamp(relevant[0]['event_time'])>=max(15,3*period)
                        and len(set(signatures))==1 and sum(v is not None for v in signatures[-1])>=3
                        and len({e.get('sequence_no') for e in relevant})==1
                        and relevant[0].get('sequence_no') is not None):
                    faults.add('FREEZE')
                for e in relevant:
                    peers = [p for other, values in state.history.items() if other[0] == key[0] and other[1] == key[1] and other[2] != sid
                             for p in values if timestamp(p['event_time']) <= state.now and abs(timestamp(p['event_time'])-timestamp(e['event_time'])) <= period]
                    if not peers: continue
                    closest = {}
                    for p in peers:
                        old = closest.get(p['source_id'])
                        if old is None or abs(timestamp(p['event_time'])-timestamp(e['event_time'])) < abs(timestamp(old['event_time'])-timestamp(e['event_time'])):
                            closest[p['source_id']] = p
                    residual = e['speed_kmh'] - median(p['speed_kmh'] for p in closest.values())
                    residuals.append((timestamp(e['event_time']), residual))
                    if abs(residual) <= 10:
                        positive = True
                        evidence_ids.add(e['event_id'])
                if len(signatures)>=3 and len(set(signatures))==1 and sum(v is not None for v in signatures[-1])>=3 and residuals and any(abs(r)>10 for _,r in residuals): faults.add('FREEZE')
            if len(residuals) >= 3:
                residuals.sort()
                center = median(r for _,r in residuals)
                mad = median(abs(r-center) for _,r in residuals)
                threshold = max(10,3*mad)
                if abs(center) > threshold:
                    change = residuals[-1][1]-residuals[0][1]
                    faults.add('DRIFT' if abs(change) > 10 else 'BIAS')
            for e in useful:
                if e.get('segment_id') and state.fresh(e, freshness(state.ref,e)):
                    peers = [p for p in state.events(entity=e['segment_id']) if p['source_id'] != sid and p['event_type'] in ('ROAD_OBSERVATION','V2X_MESSAGE','DIGITAL_TWIN_SEGMENT') and state.fresh(p,freshness(state.ref,p))]
                    own = road_vote(e,state.ref.segments[e['segment_id']])
                    votes = [road_vote(p,state.ref.segments[e['segment_id']]) for p in peers if p.get('signature_valid',True)]
                    if own == 'CLOSED' and votes.count('OPEN') >= 2:
                        faults.add('FALSE_LANE_CLOSURE')
                    elif own and own in votes:
                        positive = True
                        evidence_ids.add(e['event_id'])
            sequence_events=[e for key,h in state.history.items() if key[2] == sid for e in h
                             if 'sequence_no' in e and state.watermark-60 <= timestamp(e['event_time']) <= state.watermark]
            sequences = sorted({e['sequence_no'] for e in sequence_events})
            repeat_counter=len(sequences)<len(sequence_events)
            if (len(sequences)>=3 and not repeat_counter and 'DELAY' not in faults
                    and sequences[-1]-sequences[0]+1>len(sequences)+1): faults.add('PACKET_LOSS')
            window = self.evidence[sid]
            while window and window[0][0] < state.now-60: window.popleft()
            if faults: evidence_ids.update(e['event_id'] for e in recent if state.fresh(e,120))
            signature = tuple(sorted(evidence_ids)), tuple(sorted(faults))
            informative = bool(faults) or positive
            if informative and signature != self.last_evidence.get(sid):
                window.append((state.now, 0 if faults else 1, 1 if faults else 0))
                self.last_evidence[sid] = signature
                self.recovery[sid] = self.recovery[sid]+1 if not faults else 0
            good, bad = sum(e[1] for e in window), sum(e[2] for e in window)
            reputation = (1+good)/(2+good+bad)
            confirmed_failure = 'OUTAGE' in faults
            age = min((state.now-timestamp(e['event_time']) for e in useful), default=120)
            scale = max((freshness(state.ref,e) for e in useful), default=3*period)
            trust = reputation * math.exp(-max(0,age)/scale) * (0.25 if confirmed_failure else 0.6 if faults else 1)
            if confirmed_failure or (len(window) >= 3 and trust < 0.4): self.excluded.add(sid)
            if sid in self.excluded and self.recovery[sid] >= 3 and not faults: self.excluded.remove(sid)
            status = 'FAILED' if confirmed_failure else 'DEGRADED' if faults else 'OK' if useful else 'UNKNOWN'
            output.append(dict(source_id=sid, status=status, trust_score=round(trust,6),
                               confidence=0.8 if confirmed_failure else 0.65 if faults else 0.6 if useful else 0.35,
                               fault_types=sorted(faults)))
        return output
