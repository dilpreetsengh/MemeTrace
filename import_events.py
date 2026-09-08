"""Import a normalized research sample into MemeTrace.

Usage: python3 import_events.py demo_events.json
"""
import json
import sys
from pathlib import Path
from server import import_events

if len(sys.argv) != 2:
    raise SystemExit("Usage: python3 import_events.py events.json")

events = json.loads(Path(sys.argv[1]).read_text())
print(json.dumps(import_events(events, source=Path(sys.argv[1]).name), indent=2))
