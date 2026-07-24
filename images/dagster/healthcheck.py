import socket
import sys


def check_tcp(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=3):
        pass


def check_unix(path: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(path)


if __name__ == "__main__":
    if sys.argv[1] == "--unix":
        check_unix(sys.argv[2])
    else:
        check_tcp(sys.argv[1], int(sys.argv[2]))
