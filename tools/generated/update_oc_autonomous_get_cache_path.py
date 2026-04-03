#!/usr/bin/env python3

import hashlib
import os

def get_cache_path(goal):
    hash_object = hashlib.md5(goal.encode())
    goal_hash = hash_object.hexdigest()
    cache_path = os.path.join('tools', 'generated', 'cache', f'{goal_hash}.py')
    return cache_path

if __name__ == "__main__":
    raise SystemExit()