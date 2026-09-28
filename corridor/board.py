"""Manual operational health checks, separate from physical ODD violation codes."""


def issues(event):
    if event is None:
        return []
    result = []
    perception = event.get('perception_health', 1)
    if perception < 0.5 or (perception < 0.9 and event.get('autonomy_state') in ('DEGRADED', 'REMOTE_REQUESTED')):
        result.append('perception')
    if event.get('localization_confidence', 1) < 0.5:
        result.append('localization')
    if event.get('communication_latency_ms', 0) >= 2000 or event.get('packet_loss_pct_10s', 0) >= 50:
        result.append('communication')
    return result


def uncertain(state, fusion, vid, event):
    memory = getattr(fusion, 'memory', None)
    if memory and hasattr(memory, 'board_uncertain'):
        return memory.board_uncertain(state, vid, event)
    return bool(issues(event))
