import json
from pathlib import Path


class Forest:
    def __init__(self, path):
        self.data = json.loads(Path(path).read_text()) if Path(path).exists() else {}

    def predict(self, name, features, default):
        model = self.data.get(name)
        if model is None:
            return default, 0.6
        scores = [0.0] * len(model["classes"])
        for tree in model["trees"]:
            node = 0
            while tree["left"][node] != -1:
                node = tree["left"][node] if features[tree["feature"][node]] <= tree["threshold"][node] else tree["right"][node]
            for index, probability in enumerate(tree["value"][node]):
                scores[index] += probability
        best = max(range(len(scores)), key=scores.__getitem__)
        return model["classes"][best], min(0.995, max(0.5, scores[best] / len(model["trees"])))

    def calibrate(self, component, category, confidence):
        key = component + ":" + category + ":" + str(min(9, int(confidence * 10)))
        return self.data.get("calibration", {}).get(key, confidence)
