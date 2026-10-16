"""FastAPI application entrypoint compatibility shim for Stock Sentinel.
Imports the ASGI application from main.py.
"""
from main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=5000, reload=True)
