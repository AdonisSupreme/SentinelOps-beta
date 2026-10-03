"""Synchronize the portable policy and frontend registry; --check detects drift."""
from pathlib import Path
import argparse

root = Path(__file__).resolve().parents[2]
source = root/'SentinelOps-beta/app/access'
parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
targets = [(source/'modules.json', root/'SentinelOps/src/policies/modules.json')]
targets += [(source/name, root/'sentinelops-ai/app/access'/name) for name in ('__init__.py','policy.py','middleware.py','modules.json','notifications.py','projections.py','request_context.py')]
for src,dest in targets:
    if args.check:
        assert dest.exists() and src.read_bytes()==dest.read_bytes(), f'Access contract drift: {dest}'
    else:
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(src.read_bytes())
print('Access contracts synchronized' if not args.check else 'Access contracts match')
