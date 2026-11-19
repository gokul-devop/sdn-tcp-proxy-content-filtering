import socket
import threading
import argparse

from common.protocol import send_message, recv_message


HOST = "0.0.0.0"
PORT = 9002

INFORMATION = {
    "status": "All systems operational",
    "announcements": "Welcome to the SDN network",
    "reports": "Latest system reports available"
}


def handle_client(conn, addr):

    print(f"[SERVICE B] Client connected: {addr}")

    try:
        conn.settimeout(30)

        while True:

            try:
                request = recv_message(conn)

            except ConnectionError:
                break

            except socket.timeout:
                break

            except (ValueError, UnicodeDecodeError):
                break

            if not isinstance(request, dict):

                response = {
                    "status": "ERROR",
                    "message": "Request must be a JSON object"
                }

            else:

                action = request.get("action")
                resource = request.get("resource")

                if action != "GET":

                    response = {
                        "status": "ERROR",
                        "message": "Unsupported action"
                    }

                elif resource not in INFORMATION:

                    response = {
                        "status": "ERROR",
                        "message": "Resource not found"
                    }

                else:

                    response = {
                        "status": "SUCCESS",
                        "service": "service_B",
                        "resource": resource,
                        "data": INFORMATION[resource]
                    }

            send_message(conn, response)

    except (OSError, ValueError, TypeError) as error:
        print(f"[SERVICE B] Error: {error}")

    finally:
        conn.close()
        print(f"[SERVICE B] Client disconnected: {addr}")


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
            f"[SERVICE B] Listening on "
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
