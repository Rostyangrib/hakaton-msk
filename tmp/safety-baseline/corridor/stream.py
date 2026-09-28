import json


def packets(stream):
    """Consume one JSON packet per nonempty line until EOF."""
    for line in stream:
        if line.strip():
            yield json.loads(line)
