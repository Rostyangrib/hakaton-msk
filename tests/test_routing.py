import unittest
import random
from corridor.routing import dijkstra, route_choice


class RoutingTests(unittest.TestCase):
    def test_strict_boundaries(self):
        self.assertEqual(route_choice(['a'],['b'],500,1000),'REROUTE')
        self.assertEqual(route_choice(['a'],['b'],500.001,1000),'CONTINUE')
        self.assertEqual(route_choice(['a'],['b'],100,100),'REROUTE')
        self.assertEqual(route_choice(['a'],['a'],0,100),'CONTINUE')
        self.assertEqual(route_choice(['a'],None,0,100),'HOLD')
        self.assertEqual(route_choice(['a'],['b'],900,1000,True),'REROUTE')

    def test_directed_full_path_ties_unreachable(self):
        segments={'a':dict(to_node='B'),'b':dict(to_node='C'),'c':dict(to_node='D'),'d':dict(to_node='D')}
        outgoing={'A':['b','a'],'B':['c'],'C':['d']}
        self.assertEqual(dijkstra(segments,outgoing,'A','D',dict(a=1,b=1,c=1,d=1)),(['a','c'],2))
        self.assertIsNone(dijkstra(segments,outgoing,'D','A',dict(a=1))[0])
        with self.assertRaises(ValueError): dijkstra(segments,outgoing,'A','D',dict(a=0))

    def test_full_list_comparison(self):
        self.assertEqual(route_choice(['a','b'],['a','c'],1,100),'REROUTE')

    def test_differential_small_graphs(self):
        rng=random.Random(42)
        for _ in range(100):
            segments={}; outgoing={}; costs={}
            for a in range(5):
                for b in range(5):
                    if a!=b and rng.random()<.25:
                        sid=f'{a}{b}'; segments[sid]=dict(to_node=b); outgoing.setdefault(a,[]).append(sid); costs[sid]=rng.randint(1,5)
            candidates=[]
            def enumerate_paths(node,seen,path,cost):
                if node==4: candidates.append((cost,tuple(path))); return
                for sid in outgoing.get(node,[]):
                    end=segments[sid]['to_node']
                    if end not in seen: enumerate_paths(end,seen|{end},path+[sid],cost+costs[sid])
            enumerate_paths(0,{0},[],0)
            result=dijkstra(segments,outgoing,0,4,costs)
            if candidates:
                cost,path=min(candidates); self.assertEqual(result,(list(path),cost))
            else: self.assertIsNone(result[0])
