import argparse
import csv
import statistics
import socket
import time
from pathlib import Path

from common.protocol import send_message, recv_message


def benchmark(host, port, requests=100):
    latencies = []
    failures = 0

    for i in range(requests):
        request = {
            "request_id": f"benchmark-{i}",
            "service": "service_B",
            "action": "GET",
            "resource": "status",
        }

        start = time.perf_counter()

        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                sock.settimeout(5)
                send_message(sock, request)
                response = recv_message(sock)

            if response.get("status") == "SUCCESS":
                latencies.append((time.perf_counter() - start) * 1000)
            else:
                failures += 1

        except Exception:
            failures += 1

    return {
        "successful": len(latencies),
        "failed": failures,
        "average_ms": round(statistics.mean(latencies), 3) if latencies else 0,
        "median_ms": round(statistics.median(latencies), 3) if latencies else 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()

    scenarios = [
        ("direct", "10.0.0.6", 9002),
        ("proxy", "10.0.0.4", 9000),
    ]

    results = []

    for name, host, port in scenarios:
        result = benchmark(host, port, args.requests)
        result["scenario"] = name
        results.append(result)

        print(name.upper(), result)

    output = Path("logs/performance_results.csv")
    output.parent.mkdir(exist_ok=True)

    with output.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["scenario", "successful", "failed",
                        "average_ms", "median_ms"],
        )
        writer.writeheader()
        writer.writerows(results)

    print("Saved:", output)


if __name__ == "__main__":
    main()
