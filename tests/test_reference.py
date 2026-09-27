import os
import unittest
from corridor.reference import Reference


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Reference(os.environ['CORRIDOR_REFERENCE'])

    def test_counts_and_direction(self):
        self.assertEqual(len(self.ref.segments), 86)
        for node, edges in self.ref.outgoing.items():
            self.assertTrue(all(self.ref.segments[s]['from_node'] == node for s in edges))
        a, b = self.ref.segments['S001'], self.ref.segments['S002']
        self.assertEqual((a['from_node'], a['to_node']), (b['to_node'], b['from_node']))

    def test_position(self):
        s = self.ref.segments['S001']
        a = self.ref.position('S001', 0)
        b = self.ref.position('S001', float(s['length_m']))
        self.assertEqual(self.ref.position('S001', float(s['length_m']) / 2), tuple((x+y)/2 for x,y in zip(a,b)))
        with self.assertRaises(ValueError):
            self.ref.position('S001', -1)

    def test_mass_and_tunnel(self):
        v = next(k for k,r in self.ref.vehicles.items() if r['odd_profile_id'] == 'ODD-C')
        tunnel = next(k for k,r in self.ref.segments.items() if r['structure'] == 'tunnel')
        self.assertFalse(self.ref.compatible(v, tunnel))
        v = 'AV-001'
        old = self.ref.vehicles[v]['gross_mass_t']
        self.ref.vehicles[v]['gross_mass_t'] = '1000'
        try:
            self.assertFalse(self.ref.compatible(v, 'S001'))
        finally:
            self.ref.vehicles[v]['gross_mass_t'] = old
