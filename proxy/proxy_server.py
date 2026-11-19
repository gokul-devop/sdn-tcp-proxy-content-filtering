import argparse
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from common.protocol import send_message, recv_message
from pathlib import Path
from proxy.policy_engine import PolicyEngine
from proxy.sdn_enforcer import SDNEnforcer
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 9000

SERVICES = {
    "service_A": ("10.0.0.5", 9001),
    "service_B": ("10.0.0.6", 9002),
}

MAX_CLIENTS = 50
CLIENT_TIMEOUT = 30
BACKEND_TIMEOUT = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("TCP_PROXY")
POLICY_ENGINE = PolicyEngine(
    Path(__file__).resolve().parents[1] / "config" / "policies.json"
)
SDN_ENFORCER = SDNEnforcer(
    controller_url="http://192.168.100.1:8080",
    threshold=3,
    ban_duration=30,
)
def error_response(message, request_id=None):
    return {
        "request_id": request_id,
        "status": "ERROR",
        "message": message,
    }


def forward_request(request):
    service_name = request["service"]
    backend_host, backend_port = SERVICES[service_name]

    # Only forward application fields required by the backend.
    backend_request = {
        "action": request["action"],
        "resource": request["resource"],
    }

    start_time = time.perf_counter()

    with socket.create_connection(
        (backend_host, backend_port),
        timeout=BACKEND_TIMEOUT,
    ) as backend:

        backend.settimeout(BACKEND_TIMEOUT)

        send_message(backend, backend_request)
        response = recv_message(backend)

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    if not isinstance(response, dict):
        raise ValueError("Invalid backend response")

    return response, elapsed_ms


def process_request(request, client_ip):
    if not isinstance(request, dict):
        return error_response("Request must be a JSON object")

    request_id = request.get("request_id")
    service = request.get("service")
    action = request.get("action")
    resource = request.get("resource")

    if not all(
        isinstance(value, str) and value
        for value in (service, action, resource)
    ):
        return error_response(
            "service, action and resource must be non-empty strings",
            request_id,
        )

    if service not in SERVICES:
        return error_response(
            "Unknown service",
            request_id,
        )
    allowed, reason = POLICY_ENGINE.evaluate(
        client_ip,
        service,
        resource,
    )

   
    if not allowed:
        logger.warning(
            "POLICY_DENY | client=%s | service=%s | "
            "resource=%s | reason=%s",
            client_ip,
            service,
            resource,
            reason,
        )

        # Register the security violation.
        SDN_ENFORCER.register_violation(
            client_ip,
            reason,
        )

        return {
            "request_id": request_id,
            "status": "ACCESS_DENIED",
            "message": reason,
        }
    logger.info(
        "POLICY_ALLOW | client=%s | service=%s | resource=%s",
        client_ip,
        service,
        resource,
    )
    logger.info(
        "REQUEST | client=%s | service=%s | action=%s | resource=%s",
        client_ip,
        service,
        action,
        resource,
    )

    try:
        response, elapsed_ms = forward_request(request)

        response["request_id"] = request_id
        response["proxy_time_ms"] = round(elapsed_ms, 3)

        logger.info(
            "FORWARDED | client=%s | service=%s | status=%s | time_ms=%.3f",
            client_ip,
            service,
            response.get("status"),
            elapsed_ms,
        )

        return response

    except (OSError, ConnectionError, TimeoutError, ValueError) as error:
        logger.error(
            "BACKEND_ERROR | client=%s | service=%s | error=%s",
            client_ip,
            service,
            error,
        )

        return error_response(
            "Backend service unavailable or returned an invalid response",
            request_id,
        )


def handle_client(conn, address):
    client_ip, client_port = address

    logger.info(
        "CONNECTED | client=%s:%s",
        client_ip,
        client_port,
    )

    try:
        with conn:
            conn.settimeout(CLIENT_TIMEOUT)

            while True:
                try:
                    request = recv_message(conn)

                except ConnectionError:
                    break

                except socket.timeout:
                    logger.warning(
                        "CLIENT_TIMEOUT | client=%s",
                        client_ip,
                    )
                    break

                except (ValueError, UnicodeDecodeError) as error:
                    logger.warning(
                        "INVALID_REQUEST | client=%s | error=%s",
                        client_ip,
                        error,
                    )
                    break

                response = process_request(request, client_ip)

                send_message(conn, response)

    except OSError as error:
        logger.warning(
            "CLIENT_ERROR | client=%s | error=%s",
            client_ip,
            error,
        )

    finally:
        logger.info(
            "DISCONNECTED | client=%s:%s",
            client_ip,
            client_port,
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default=PROXY_HOST)
    parser.add_argument("--port", type=int, default=PROXY_PORT)

    args = parser.parse_args()

    # Bound the number of active client workers.
    client_slots = threading.BoundedSemaphore(MAX_CLIENTS)

    def run_client(conn, address):
        try:
            handle_client(conn, address)
        finally:
            client_slots.release()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        server.bind((args.host, args.port))
        server.listen(100)

        logger.info(
            "PROXY_STARTED | address=%s:%s",
            args.host,
            args.port,
        )

        with ThreadPoolExecutor(max_workers=MAX_CLIENTS) as executor:
            while True:
                conn, address = server.accept()

                if not client_slots.acquire(blocking=False):
                    logger.warning(
                        "CAPACITY_REACHED | client=%s",
                        address[0],
                    )
                    conn.close()
                    continue

                try:
                    executor.submit(run_client, conn, address)
                except RuntimeError:
                    client_slots.release()
                    conn.close()
                    raise


if __name__ == "__main__":
    main()
