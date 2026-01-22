import requests
from urllib.parse import quote
from typing import Tuple


def ssn_send(session: str, target: str, text: str, timeout_s: float = 5.0) -> Tuple[bool, str]:
    """
    https://io.socialstream.ninja/{session}/sendEncodedChat/{target}/{message}
    """
    try:
        url = f"https://io.socialstream.ninja/{session}/sendEncodedChat/{quote(target)}/{quote(text)}"
        r = requests.get(url, timeout=timeout_s)
        if r.status_code == 200:
            return True, ""
        body = getattr(r, "text", "") or ""
        return False, f"HTTP {r.status_code}: {body[:180]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
