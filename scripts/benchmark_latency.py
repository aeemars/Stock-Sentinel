import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from fastapi.testclient import TestClient
from main import app
from app.session import dump_session
from app.auth import User
from app.services.cache import set_cached_user

def run_benchmarks():
    print("\n" + "=" * 70)
    print("🚀 STOCK SENTINEL: LATENCY & CACHING BENCHMARK (<60ms TARGET)")
    print("=" * 70 + "\n")

    with TestClient(app, follow_redirects=False) as client:
        # Prime the test environment and create test session
        mock_user = User({"id": 1, "username": "bench_user", "email": "bench@test.com"})
        set_cached_user(1, mock_user)
        session_token = dump_session({"user_id": 1, "_csrf_token": "test-csrf-token"})
        client.cookies.set("session", session_token)

        routes = [
            ("GET", "/login", "Public Login Page", {}),
            ("GET", "/register", "Public Register Page", {}),
            ("GET", "/static/favicon.svg", "Static Asset (/static/*)", {}),
            ("GET", "/dashboard", "Authenticated Dashboard", {}),
            ("GET", "/portfolio/1", "Portfolio Details (SWR)", {}),
            ("GET", "/analytics", "Analytics & Charts", {}),
            ("GET", "/portfolio/1/live-prices", "Live Prices JSON API", {}),
        ]

        all_passed = True
        results_summary = []

        for method, path, label, kwargs in routes:
            # Warmup (2 requests)
            for _ in range(2):
                client.request(method, path, **kwargs)

            # Measurement iterations
            latencies = []
            last_response = None
            iterations = 25

            for _ in range(iterations):
                t0 = time.perf_counter()
                resp = client.request(method, path, **kwargs)
                duration_ms = (time.perf_counter() - t0) * 1000.0
                latencies.append(duration_ms)
                last_response = resp

            p50 = np.percentile(latencies, 50)
            p95 = np.percentile(latencies, 95)
            p99 = np.percentile(latencies, 99)
            avg = np.mean(latencies)
            min_lat = np.min(latencies)

            server_timing = last_response.headers.get("x-response-time", "N/A")
            cache_control = last_response.headers.get("cache-control", "None")

            status_passed = p95 < 60.0
            if not status_passed:
                all_passed = False

            status_icon = "✅ PASS" if status_passed else "❌ FAIL"
            results_summary.append({
                "label": label,
                "path": path,
                "p50": p50,
                "p95": p95,
                "p99": p99,
                "avg": avg,
                "min": min_lat,
                "passed": status_passed,
                "timing_hdr": server_timing,
                "cache_hdr": cache_control,
            })

            print(f"[{status_icon}] {label.ljust(30)} {path}")
            print(f"       P50: {p50:5.2f}ms | P95: {p95:5.2f}ms | P99: {p99:5.2f}ms | Min: {min_lat:5.2f}ms | Server-Hdr: {server_timing}")
            if path.startswith("/static/"):
                print(f"       Cache-Control: {cache_control}")
            print()

        print("=" * 70)
        print("BENCHMARK SUMMARY (<60ms SLA):")
        print(f"{'Route / Feature':<30} | {'P50':<8} | {'P95':<8} | {'P99':<8} | {'SLA Status'}")
        print("-" * 70)
        for r in results_summary:
            sla = "PASS (<60ms)" if r["passed"] else "FAIL (>60ms)"
            print(f"{r['label']:<30} | {r['p50']:5.2f}ms | {r['p95']:5.2f}ms | {r['p99']:5.2f}ms | {sla}")
        print("=" * 70)

        if all_passed:
            print("\n🎉 ALL ROUTES MET THE <60ms RESPONSE TIME SLA!\n")
        else:
            print("\n⚠️ SOME ROUTES EXCEEDED THE 60ms TARGET.\n")

        return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(run_benchmarks())
