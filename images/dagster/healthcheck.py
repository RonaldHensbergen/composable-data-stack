import socket
import sys


def check(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=3):
        pass


if __name__ == "__main__":
    check(sys.argv[1], int(sys.argv[2]))
