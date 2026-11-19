import socket
import threading
import logging
import argparse

from common.protocol import send_message, recv_message


HOST = "0.0.0.0"
PORT = 9001

DATA = {
    "public_data": "Public information available",
    "private_data": "Confidential information",
    "student_records": "Student database information"
}


def handle_client(conn, addr):
    print(f"[SERVICE A] Client connected: {addr}")

    try:
        conn.settimeout(30)

        while True:
            try:
                request = recv_message(conn)

            except ConnectionError:
                break

            except socket.timeout:
                print("[SERVICE A] Client timed out")
                break

            except (ValueError, UnicodeDecodeError) as error:
                print(f"[SERVICE A] Invalid request: {error}")
                break

            if not isinstance(request, dict):
                response = {
                    "status": "ERROR",
                    "message": "Request must be a JSON object"
                }

            else:
                action = request.get("action")
                resource = request.get("resource")

                if action != "READ":
                    response = {
                        "status": "ERROR",
                        "message": "Unsupported action"
                    }

                elif resource not in DATA:
                    response = {
                        "status": "ERROR",
                        "message": "Resource not found"
                    }

                else:
                    response = {
                        "status": "SUCCESS",
                        "service": "service_A",
                        "resource": resource,
                        "data": DATA[resource]
                    }

            send_message(conn, response)

    except (OSError, ValueError, TypeError) as error:
        print(f"[SERVICE A] Error: {error}")

    finally:
        conn.close()
        print(f"[SERVICE A] Client disconnected: {addr}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)

    args = parser.parse_args()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:

        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server.bind((args.host, args.port))

        server.listen(50)

        print(
            f"[SERVICE A] Listening on "
            f"{args.host}:{args.port}",
            flush=True
        )

        while True:

            conn, addr = server.accept()

            thread = threading.Thread(
                target=handle_client,
                args=(conn, addr),
                daemon=True
            )

            thread.start()


if __name__ == "__main__":
    main()
