"""Closed, conservative retry policy for a single bounded GPU observation."""


def retry_reason(payload):
    """Retry transient collection errors, never credentials or unclassified remote errors."""
    if payload.get("ok"):
        return None
    error = str(payload.get("error") or "").lower()
    if any(text in error for text in (
        "credential", "permission denied", "authentication", "publickey",
        "host key verification", "host identification has changed", "no matching",
    )):
        return None
    # Invalid JSON, missing tools, output limits and arbitrary remote errors
    # need diagnosis rather than another execution.
    if not error.startswith(("ssh exit 255:", "ssh probe timeout after ")):
        return None
    if "timeout" in error or "timed out" in error:
        return "timeout"
    if any(text in error for text in (
        "connection reset", "connection closed", "connection refused",
        "connection aborted", "connection failed", "no route to host",
        "network is unreachable", "broken pipe",
    )):
        return "connection"
    return None
