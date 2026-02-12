"""
Shared utilities for Athena scrapers.
"""

import time
import requests


def fetch(url, method="GET", headers=None, timeout=60, retries=3,
          retry_delay=5, **kwargs):
    """HTTP request with retry logic.

    Args:
        url: The URL to fetch.
        method: "GET" or "POST".
        headers: Request headers dict.
        timeout: Seconds before timeout (default 60).
        retries: Number of attempts (default 3).
        retry_delay: Seconds to wait between retries (default 5).
        **kwargs: Extra args passed to requests (data, params, etc.)

    Returns:
        requests.Response on success.

    Raises:
        requests.RequestException if all retries fail.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=timeout, **kwargs
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(retry_delay)
    raise last_err
