"""Vercel Serverless Function entrypoint for Stock Sentinel FastAPI."""
import sys
import os
from pathlib import Path

# Explicitly anchor working directory and python path to project root
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
try:
    os.chdir(str(ROOT_DIR))
except Exception:
    pass

from main import app
