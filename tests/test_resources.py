import itertools
import random
import unittest
from collections import Counter
from corridor.resources import min_cost_assignment, support_selection


class ResourceTests(unittest.TestCase):
    def test_global_assignment_not_greedy(self):
        self.assertEqual(min_cost_assignment({'a':{'x':1,'y':2},'b':{'x':1}},{'x':1,'y':1}),{'a':'y','b':'x'})
        self.assertEqual(len(min_cost_assignment({'a':{'x':1},'b':{'x':2}},{'x':1})),1)
        self.assertEqual(min_cost_assignment({'a':{}},{'x':1}),{})

    def test_differential_assignments(self):
        rng=random.Random(0)
        for _ in range(100):
            candidates={v:{s:rng.randint(1,10) for s in 'xy' if rng.random()<0.8} for v in 'abc'}
            capacity={'x':1,'y':1}
            actual=min_cost_assignment(candidates,capacity)
            best=(0,float('inf'))
            for values in itertools.product([None,'x','y'],repeat=3):
                assign=dict(zip('abc',values)); counts=Counter(s for s in values if s)
                if any(counts[s]>capacity[s] for s in counts) or any(s and s not in candidates[v] for v,s in assign.items()): continue
                score=(-sum(s is not None for s in values),sum(candidates[v][s] for v,s in assign.items() if s))
                best=min(best,score)
            self.assertEqual((-len(actual),sum(candidates[v][s] for v,s in actual.items())),best)

    def test_seventeen_requests_six_operators(self):
        requests={f'AV-{i:03}':(i%3,i%5+1) for i in range(17)}
        self.assertEqual(len(support_selection(requests,6)),6)
        self.assertEqual(support_selection({'a':(0,1),'b':(0,1)},1,['b']),{'b'})
