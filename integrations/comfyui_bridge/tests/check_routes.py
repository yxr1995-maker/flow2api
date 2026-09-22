import os
import sys
sys.path.insert(0, os.environ.get("FLOW2API_DIR", os.path.expanduser("~/Documents/flow2api")))
import inspect
from src.api import routes
functions = [f for f in dir(routes) if 'extract' in f.lower() or 'image' in f.lower()]
print('Routes helpers:', functions)
