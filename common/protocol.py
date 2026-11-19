import json
import socket
import struct

MAX_MESSAGE_SIZE = 1024 * 1024


def recv_exact(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError("Connection closed")

        data.extend(chunk)

    return bytes(data)


def send_message(sock, message):
    payload = json.dumps(message).encode("utf-8")

    if len(payload) > MAX_MESSAGE_SIZE:
        raise ValueError("Message exceeds maximum size")

    header = struct.pack("!I", len(payload))

    sock.sendall(header + payload)


def recv_message(sock):
    header = recv_exact(sock, 4)

    message_size = struct.unpack("!I", header)[0]

    if message_size > MAX_MESSAGE_SIZE:
        raise ValueError("Incoming message exceeds maximum size")

    payload = recv_exact(sock, message_size)

    return json.loads(payload.decode("utf-8"))
