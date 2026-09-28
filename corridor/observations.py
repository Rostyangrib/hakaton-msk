from collections import deque
from datetime import datetime
import math


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def number(event, key, default=0.0):
    value = event.get(key, default)
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) else default


class Observations:
    def __init__(self):
        self.now = 0.0
        self.start = None
        self.latest = {}
        self.history = {}
        self.seen = {}
        self.seen_order = deque()
        self.duplicate_count = 0
        self.late_count = 0
        self.rejected_count = 0

    def ingest(self, packet):
        self.now = timestamp(packet.get("decision_time", packet.get("packet_time")))
        if self.start is None:
            self.start = timestamp(packet.get("window_start", packet.get("packet_time", packet.get("decision_time"))))
        watermark = timestamp(packet.get("watermark_time", packet.get("decision_time")))
        while self.seen_order and self.seen_order[0][0] < self.now - 600:
            _, event_id = self.seen_order.popleft()
            self.seen.pop(event_id, None)
        for original in packet.get("events", []):
            event_id = original.get("event_id")
            if event_id in self.seen:
                self.duplicate_count += 1
                continue
            try:
                measured = timestamp(original["event_time"])
                received = timestamp(original["received_time"])
            except (KeyError, ValueError, TypeError):
                self.rejected_count += 1
                continue
            if received > self.now + 0.001:
                self.rejected_count += 1
                continue
            self.seen[event_id] = received
            self.seen_order.append((self.now, event_id))
            self.late_count += measured < watermark
            event = dict(original)
            event["_time"] = min(measured, received)
            event["_received"] = received
            event["_delay"] = max(0, received - measured)
            event["_clock"] = measured - received
            event["_snapshot_age"] = number(event, "snapshot_age_sec")
            if updated := event.get("source_updated_time"):
                try:
                    event["_snapshot_age"] = max(event["_snapshot_age"], measured - timestamp(updated))
                except (ValueError, TypeError):
                    pass
            kind = event.get("event_type")
            entity = event.get("segment_id", "") if kind in {"ROAD_OBSERVATION", "V2X_MESSAGE", "DIGITAL_TWIN_SEGMENT"} else ""
            key = (kind, event.get("source_id"), entity)
            previous = self.latest.get(key)
            if previous and (event["_time"], event.get("event_no", 0)) <= (previous["_time"], previous.get("event_no", 0)):
                continue
            self.latest[key] = event
            history_key = (kind, event.get("source_id"))
            if kind != "DIGITAL_TWIN_SEGMENT":
                queue = self.history.setdefault(history_key, deque(maxlen=90))
                queue.append(event)
        for queue in self.history.values():
            while queue and queue[0]["_time"] < self.now - 180:
                queue.popleft()

    def get(self, kind, source, segment="", max_age=120):
        event = self.latest.get((kind, source, segment))
        return event if event and self.now - event["_time"] <= max_age else None

    def events(self, kind, max_age=120):
        return [event for (event_kind, _, _), event in self.latest.items() if event_kind == kind and self.now - event["_time"] <= max_age]
