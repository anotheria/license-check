"""Print the installed distributions of the running interpreter as JSON: [{name, version, license}].

Run it with the project's virtualenv python. Prefers the License-Expression field, then the
"License ::" trove classifiers, then the free-text License field.
"""
import json
from importlib.metadata import distributions

SKIP = {"pip", "setuptools", "wheel"}

rows = {}
for dist in distributions():
    meta = dist.metadata
    name = meta.get("Name")
    if not name or name.lower() in SKIP:
        continue
    lic = (meta.get("License-Expression") or "").strip()
    if not lic:
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                       if c.startswith("License ::") and c.strip() != "License :: OSI Approved"]
        lic = " | ".join(classifiers)
    if not lic:
        text = (meta.get("License") or "").strip()
        # Some packages put the whole license text in this field; keep only the first line.
        lic = text.splitlines()[0][:100] if text else "Unknown"
    rows[name.lower()] = {"name": name, "version": dist.version, "license": lic or "Unknown"}

print(json.dumps(sorted(rows.values(), key=lambda r: r["name"].lower()), indent=1))
