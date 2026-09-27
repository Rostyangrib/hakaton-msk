import unittest
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
