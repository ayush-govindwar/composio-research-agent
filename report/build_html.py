"""
Renders report/template.html into report/index.html, injecting:
  - STATS_JSON  (data/stats.json)
  - APPS_JSON   (data/pass2.jsonl, the full 100 rows incl. is_mock flags)
  - AUDIT_JSON  (data/audit_results.json, or {} if not run yet)

Everything else (table rendering, filters, stat cards) is plain JS inside
template.html reading these three blobs -- no build step, deploy the single
output file as-is.

Run:
    python report/build_html.py
"""

import json
import os
from jinja2 import Template

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.html")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "index.html")

STATS_PATH = os.path.join(DATA_DIR, "stats.json")
APPS_PATH = os.path.join(DATA_DIR, "pass2.jsonl")
AUDIT_PATH = os.path.join(DATA_DIR, "audit_results.json")


def build_report(template_path=TEMPLATE_PATH, stats_path=STATS_PATH, apps_path=APPS_PATH,
                  audit_path=AUDIT_PATH, output_path=OUTPUT_PATH):
    if not os.path.exists(stats_path) or not os.path.exists(apps_path):
        print("Missing data/stats.json or data/pass2.jsonl. Run the research -> verify -> analyze steps first.")
        return

    with open(stats_path, "r") as f:
        stats_json = f.read()

    apps = []
    with open(apps_path, "r") as f:
        for line in f:
            if line.strip():
                apps.append(json.loads(line))
    apps_json = json.dumps(apps)

    if os.path.exists(audit_path):
        with open(audit_path, "r") as f:
            audit_json = f.read()
    else:
        audit_json = json.dumps({"entries": [], "summary": {"available": False}})

    with open(template_path, "r") as f:
        template_str = f.read()

    template = Template(template_str)
    rendered = template.render(STATS_JSON=stats_json, APPS_JSON=apps_json, AUDIT_JSON=audit_json)

    with open(output_path, "w") as f:
        f.write(rendered)

    print(f"Report built: {output_path}")


if __name__ == "__main__":
    build_report()
