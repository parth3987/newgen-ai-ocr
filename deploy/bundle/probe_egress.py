"""Egress probe, run inside the API and worker containers (ADR-0031, ADR-0032).

Piped into ``python -`` by ``ngocrctl smoke`` and ``scripts/offline_test.py``. Every attempt
to reach the outside world must fail; the database on the internal network (the control)
must answer. Prints one JSON object of outcomes.
"""

import json
import socket
import urllib.request

out = {}


def attempt(name, fn):
    try:
        fn()
        out[name] = "REACHED"
    except Exception as exc:  # noqa: BLE001 - any failure is the expected outcome
        out[name] = f"blocked ({type(exc).__name__})"


def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    query = b"\x12\x34\x01\x00\x00\x01" + b"\x00" * 6 + b"\x03www\x00\x00\x01\x00\x01"
    s.sendto(query, ("8.8.8.8", 53))
    s.recvfrom(512)


BOS = "https://paddle-model-ecology.bj.bcebos.com"
attempt("dns:pypi.org", lambda: socket.getaddrinfo("pypi.org", 443))
attempt("dns:huggingface.co", lambda: socket.getaddrinfo("huggingface.co", 443))
attempt("tcp:1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), 3).close())
attempt("tcp:8.8.8.8:53", lambda: socket.create_connection(("8.8.8.8", 53), 3).close())
attempt("udp:8.8.8.8:53", udp)
attempt("https:huggingface.co", lambda: urllib.request.urlopen("https://huggingface.co", timeout=5))
attempt("https:paddle-model-ecology", lambda: urllib.request.urlopen(BOS, timeout=5))
attempt("control:postgres:5432", lambda: socket.create_connection(("postgres", 5432), 3).close())
print(json.dumps(out))  # noqa: T201 - the report
