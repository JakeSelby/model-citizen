#!/usr/bin/env python3
"""An allowlist CONNECT proxy: the only way out of a replay arm's network.

`python3 egress_proxy.py PORT HOST [HOST ...]` listens on PORT, at its own address on the arms'
internal network and nowhere else (`bind_address`), and opens a tunnel only for a
`CONNECT HOST:443` whose host is named exactly. Every other request, a plain HTTP one or a tunnel
to any other host or port, is answered `403` and closed. It runs in a container of its own on
the arms' internal Docker network and on one network that reaches the internet, from the bare
arm's image, so it needs nothing that image lacks: standard library only, Python 3.9 or later.

The runner passes this file's text with `python3 -c`, so nothing is mounted into the proxy
either. Each decision is logged to stderr as one line, `allow` or `deny`, with the target.
"""
import socket
import sys
import threading

PORT_ALLOWED = 443
BUFFER = 65536
HEADER_LIMIT = 16384


def target_of(head):
    """(host, port) for a CONNECT request line, or None for anything else."""
    try:
        method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
    except ValueError:
        return None
    if method.upper() != "CONNECT" or ":" not in target:
        return None
    host, _, port = target.rpartition(":")
    try:
        return host.strip("[]").lower(), int(port)
    except ValueError:
        return None


def allowed(target, hosts):
    return target is not None and target[1] == PORT_ALLOWED and target[0] in hosts


def _pipe(source, sink):
    try:
        while True:
            data = source.recv(BUFFER)
            if not data:
                break
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client, hosts):
    head = b""
    try:
        while b"\r\n\r\n" not in head and len(head) < HEADER_LIMIT:
            data = client.recv(4096)
            if not data:
                return
            head += data
        target = target_of(head)
        if not allowed(target, hosts):
            sys.stderr.write("deny %s\n" % (("%s:%d" % target) if target else "non-CONNECT"))
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        upstream = socket.create_connection(target, timeout=30)
        upstream.settimeout(None)
        sys.stderr.write("allow %s:%d\n" % target)
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        rest = head.split(b"\r\n\r\n", 1)[1]
        if rest:
            upstream.sendall(rest)
        threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()
        _pipe(client, upstream)
    except OSError:
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    finally:
        client.close()


def bind_address():
    """This container's address on the arms' internal network, the only one the proxy listens on.

    The proxy starts attached to that network alone, and Docker writes the container's address
    there against its hostname, so the hostname resolves to it. The runner joins the proxy to the
    outbound network only after this line has been logged. A loopback or unresolvable answer
    stops the proxy rather than let it listen anywhere else."""
    try:
        address = socket.gethostbyname(socket.gethostname())
    except OSError as exc:
        raise SystemExit("egress proxy: cannot resolve its own address: %s" % exc)
    if address.startswith("127.") or address in ("0.0.0.0", ""):
        raise SystemExit("egress proxy: its own address resolved to %s; refusing to listen" % address)
    return address


def main(argv, address=None):
    port, hosts = int(argv[0]), {h.lower() for h in argv[1:]}
    address = address or bind_address()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((address, port))
    server.listen(64)
    sys.stderr.write("egress proxy on %s:%d for %s\n" % (address, port, ", ".join(sorted(hosts))))
    sys.stderr.flush()
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle, args=(client, hosts), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
