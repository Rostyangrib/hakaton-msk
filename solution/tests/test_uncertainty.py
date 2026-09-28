import copy
import unittest
from types import SimpleNamespace

from corridor.planning import Planner


class UncertaintyTests(unittest.TestCase):
    def fixture(self):
        segments = {}
        for sid, start, end, length in [("S001", "A", "B", 1000), ("S002", "B", "C", 1000), ("S003", "C", "D", 1000), ("S004", "B", "E", 2000), ("S005", "E", "D", 2000)]:
            segments[sid] = {"from_node": start, "to_node": end, "length_m": length, "speed_limit_kmh": 90, "capacity_vph": 1000, "weight_limit_t": 44, "av_allowed": 1, "structure": "open"}
        vehicle = {"vehicle_id": "AV-001", "odd_profile_id": "ODD-A", "gross_mass_t": 20, "nominal_max_speed_kmh": 90, "cargo_priority": 1, "destination_hub_id": "HUB-01"}
        profile = {"allowed_structures": ["open"], "v2x_required": False, "min_visibility_m": 70, "max_rain_level": 3, "max_crosswind_mps": 22}
        ref = SimpleNamespace(segments=segments, profiles={"ODD-A": profile}, vehicles={"AV-001": vehicle}, hubs={"HUB-01": {"node_id": "D"}}, outgoing={"A": ["S001"], "B": ["S002", "S004"], "C": ["S003"], "E": ["S005"]}, nearest_weather={s: ["WX-01"] for s in segments}, segment_rsu={}, remote_limit=6, stops={}, orders={})
        weather = {"visibility_m": 1000, "rain_level": 0, "wind_mps": 3}
        obs = SimpleNamespace(get=lambda kind, source, max_age: weather if kind == "WEATHER_OBSERVATION" else None)
        states = {s: {"state": "OPEN", "confidence": 0.99} for s in segments}
        estimator = SimpleNamespace(states=states, assessments={"AV-001": {"odd_status": "COMPLIANT", "violation_codes": [], "confidence": 0.99}}, obs=obs, sources={"WX-01": {"status": "OK"}}, is_inactive=lambda v: False, wind_exceeded=set(), telemetry={"AV-001": {"segment_id": "S001", "destination_hub_id": "HUB-01", "offset_m": 500}})
        return Planner(ref), estimator

    def test_closed_nonshortest_branch_requires_explicit_route(self):
        planner, estimator = self.fixture()
        estimator.states["S004"]["state"] = "CLOSED"
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["motion_action"], "REROUTE")
        self.assertEqual(action["route_segment_ids"], ["S002", "S003"])

    def test_unknown_branch_is_not_presumed_safe(self):
        planner, estimator = self.fixture()
        estimator.states["S004"]["state"] = "UNKNOWN"
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["motion_action"], "REROUTE")
        self.assertIn("STATE_UNKNOWN", action["rationale_codes"])

    def test_all_safe_branches_allow_continue_without_route(self):
        planner, estimator = self.fixture()
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["motion_action"], "CONTINUE")
        self.assertNotIn("route_segment_ids", action)

    def test_no_safe_route_fails_closed(self):
        planner, estimator = self.fixture()
        for sid in ["S002", "S004"]:
            estimator.states[sid]["state"] = "CLOSED"
        self.assertEqual(planner.plan(estimator)["AV-001"]["motion_action"], "HOLD")

    def test_current_closure_precedes_remote_support(self):
        planner, estimator = self.fixture()
        estimator.states["S001"]["state"] = "CLOSED"
        estimator.assessments["AV-001"].update(odd_status="VIOLATED", violation_codes=["V2X"])
        self.assertEqual(planner.plan(estimator)["AV-001"]["motion_action"], "HOLD")

    def test_old_route_is_not_assumed_executed(self):
        planner, estimator = self.fixture()
        estimator.states["S004"]["state"] = "CLOSED"
        expected = copy.deepcopy(estimator.telemetry)
        first = planner.plan(estimator)["AV-001"]
        second = planner.plan(estimator)["AV-001"]
        self.assertEqual(first, second)
        self.assertEqual(estimator.telemetry, expected)
        self.assertEqual(second["motion_action"], "REROUTE")

    def test_old_route_is_revalidated_when_conditions_change(self):
        planner, estimator = self.fixture()
        estimator.states["S004"]["state"] = "CLOSED"
        planner.plan(estimator)
        estimator.states["S004"]["state"] = "OPEN"
        estimator.states["S002"]["state"] = "CLOSED"
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["route_segment_ids"], ["S004", "S005"])

    def test_unknown_destination_does_not_crash(self):
        planner, estimator = self.fixture()
        estimator.telemetry["AV-001"]["destination_hub_id"] = "HUB-99"
        self.assertEqual(planner.plan(estimator)["AV-001"]["motion_action"], "HOLD")

    def test_unknown_current_segment_does_not_crash(self):
        planner, estimator = self.fixture()
        estimator.telemetry["AV-001"]["segment_id"] = "S999"
        self.assertEqual(planner.plan(estimator)["AV-001"]["motion_action"], "HOLD")

    def test_missing_weather_cannot_authorize_a_route(self):
        planner, estimator = self.fixture()
        estimator.obs.get = lambda kind, source, max_age: None
        self.assertEqual(planner.plan(estimator)["AV-001"]["motion_action"], "HOLD")

    def test_mass_incompatible_branch_requires_valid_alternative(self):
        planner, estimator = self.fixture()
        planner.ref.segments["S004"]["weight_limit_t"] = 10
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["route_segment_ids"], ["S002", "S003"])
        self.assertIn("ROUTE_WEIGHT_LIMIT", action["rationale_codes"])

    def test_actual_position_wins_over_previous_recommendation(self):
        planner, estimator = self.fixture()
        planner.previous_routes["AV-001"] = ["S002", "S003"]
        estimator.telemetry["AV-001"]["segment_id"] = "S004"
        estimator.states["S005"]["state"] = "PARTIAL_BLOCK"
        action = planner.plan(estimator)["AV-001"]
        self.assertEqual(action["route_segment_ids"], ["S005"])


if __name__ == "__main__":
    unittest.main()
