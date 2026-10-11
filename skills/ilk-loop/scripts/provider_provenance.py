"""Public configured origin, never credentials or inferred provider identity."""
from urllib.parse import urlsplit


def configured_provider(value):
    origin = None
    try:
        parts = urlsplit(value or "")
        if parts.scheme in {"http", "https"} and parts.hostname:
            host = parts.hostname
            if ":" in host:
                host = "[" + host + "]"
            port = parts.port
            origin = parts.scheme + "://" + host + (":" + str(port) if port is not None else "")
    except (ValueError, TypeError):
        pass
    return {"origin": origin, "identity_basis": "configured URL; actual provider unobserved"}
