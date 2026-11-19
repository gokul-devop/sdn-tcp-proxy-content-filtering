import argparse
import json
import socket
import sys
import uuid

from common.protocol import send_message, recv_message


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--host",
        default="10.0.0.4",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=9000,
    )

    parser.add_argument(
        "--service",
        required=True,
    )

    parser.add_argument(
        "--action",
        required=True,
    )

    parser.add_argument(
        "--resource",
        required=True,
    )

    args = parser.parse_args()

    request = {
        "request_id": str(uuid.uuid4()),
        "service": args.service,
        "action": args.action,
        "resource": args.resource,
    }

    try:
        with socket.create_connection(
            (args.host, args.port),
            timeout=10,
        ) as sock:

            sock.settimeout(10)

            print("[CLIENT] Connected to proxy")
            print("[CLIENT] Request:")
            print(json.dumps(request, indent=2))

            send_message(sock, request)

            response = recv_message(sock)

            print("[CLIENT] Response:")
            print(json.dumps(response, indent=2))

            if response.get("status") != "SUCCESS":
                sys.exit(1)

    except (OSError, ConnectionError, ValueError) as error:
        print("[CLIENT] Error:", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
