"""Fixed training recipe, no calibration or holdout inputs."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.decision.learning import fit
p = argparse.ArgumentParser()
p.add_argument('--input', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
rows = []
for path in sorted(a.input.glob('*.json')):
    row = json.loads(path.read_text())
    if 'candidates' in row:
        rows.append(row)
model = fit(rows)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(model, indent=2) + '\n')
print(json.dumps({'models': {k: {f: v[f] for f in ('status', 'n', 'positives')} for k, v in model['models'].items()}, 'mode': model['mode']}))
