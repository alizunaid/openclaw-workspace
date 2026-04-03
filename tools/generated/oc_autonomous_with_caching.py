#!/usr/bin/env python3

import hashlib
import json
import os
import subprocess
from pathlib import Path

def get_cache_path(script_content):
    script_hash = hashlib.sha256(script_content.encode()).hexdigest()
    return f"tools/generated/cache/{script_hash}.py"

def run_cached_script(cache_path):
    print("Cache hit")
    try:
        subprocess.run(['python3', cache_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to execute script: {e}")
        raise

def generate_and_run_script():
    # Sample script content generation (replace with actual logic)
    script_content = "#!/usr/bin/env python3\nprint('Generated script running')\ndef main():\n    pass\nif __name__ == '__main__': raise SystemExit(main())"
    
    cache_path = get_cache_path(script_content)
    cached_script_exists = os.path.exists(cache_path)

    if not os.path.exists("tools/generated/cache"):
        Path("tools/generated/cache").mkdir(parents=True, exist_ok=True)

    if cached_script_exists:
        run_cached_script(cache_path)
    else:
        with open(cache_path, 'w') as f:
            f.write(script_content)
        
        try:
            subprocess.run(['python3', cache_path], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed to execute script: {e}")
            raise

def main():
    generate_and_run_script()

if __name__ == "__main__":
    raise SystemExit(main())