import argparse
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.protocol import send_message, recv_message


def send_request(client_id, host, port):
    request = {
        "request_id": f"concurrent-{client_id}",
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

        latency_ms = (time.perf_counter() - start) * 1000

        return {
            "client": client_id,
            "success": response.get("status") == "SUCCESS",
            "latency_ms": round(latency_ms, 3),
        }

    except Exception as exc:
        return {
            "client": client_id,
            "success": False,
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--host", default="10.0.0.4")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = [
            executor.submit(send_request, i, args.host, args.port)
            for i in range(args.clients)
        ]

        results = [future.result() for future in as_completed(futures)]

    elapsed = time.perf_counter() - started

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print("\n===== CONCURRENCY TEST =====")
    print(f"Total clients: {args.clients}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")
    print(f"Elapsed time: {elapsed:.3f} seconds")

    if successful:
        avg_latency = sum(r["latency_ms"] for r in successful) / len(successful)
        print(f"Average latency: {avg_latency:.3f} ms")

    for result in failed:
        print("FAILED:", json.dumps(result))


if __name__ == "__main__":
    main()
