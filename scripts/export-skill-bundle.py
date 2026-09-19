"""Export approved business Skills to an INTERNAL companion file (not GitHub)."""
import argparse
import json
from pathlib import Path
from content_factory_api.skill_bundle import export_bundle

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_bundle(args.database, args.output)))
