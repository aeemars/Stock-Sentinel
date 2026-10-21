"""Vercel Serverless Function entrypoint for Stock Sentinel FastAPI."""
import sys
import os

# Ensure the project root directory is at the beginning of the Python module search path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
