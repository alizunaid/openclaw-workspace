import os, sys
from importlib.util import spec_from_file_location, module_from_spec

if len(sys.argv) != 2:
    raise SystemExit("usage: run_extractor_on_path.py /path/to/body.txt")

mod_path = os.path.expanduser("~/.openclaw/workspace/scripts/extract_request_from_body.py")
spec = spec_from_file_location("extract_request_from_body", mod_path)
m = module_from_spec(spec); spec.loader.exec_module(m)

body_path = sys.argv[1]
txt = open(body_path, "r", encoding="utf-8", errors="replace").read()
print(m.extract_best(txt))
