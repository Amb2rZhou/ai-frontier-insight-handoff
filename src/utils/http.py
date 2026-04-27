"""Robust HTTP GET with curl fallback.

Python 3.9 + LibreSSL 2.8.3 has intermittent SSL failures, especially
through proxies. This module provides a robust_get() that automatically
falls back to curl subprocess when requests fails with SSL errors.
"""

import subprocess
from urllib.parse import urlencode

import requests


def robust_get(url: str, headers: dict = None, params: dict = None,
               timeout: int = 30) -> requests.Response:
    """HTTP GET with automatic curl fallback on SSL errors.

    First tries requests.get(). If it fails with SSLError or ConnectionError,
    falls back to curl subprocess which uses the system's native TLS stack.

    Args:
        url: The URL to fetch
        headers: Optional HTTP headers dict
        params: Optional query parameters dict
        timeout: Request timeout in seconds

    Returns:
        requests.Response object
    """
    kwargs = {"timeout": timeout}
    if headers:
        kwargs["headers"] = headers
    if params:
        kwargs["params"] = params

    # Try requests twice (intermittent SSL failures)
    for attempt in range(2):
        try:
            resp = requests.get(url, **kwargs)
            return resp
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
            pass

    # Fallback: use curl subprocess
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"

    cmd = ["/usr/bin/curl", "-sS", "--max-time", str(timeout), "-L",
           "-w", "\n%{http_code}", "-o", "-"]
    if headers:
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
    cmd.append(full_url)

    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout + 10)

    if result.returncode != 0:
        raise requests.exceptions.ConnectionError(
            f"curl failed (code {result.returncode}): {result.stderr.strip()}"
        )

    # Parse status code from last line (written by -w)
    output = result.stdout
    lines = output.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else output
    try:
        status_code = int(lines[-1].strip()) if len(lines) > 1 else 200
    except ValueError:
        status_code = 200

    # Build a Response object
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = body.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = full_url
    return resp
