"""
Time-based one-time passwords (RFC 6238) for the two-factor login: 6 digits, 30 s steps, SHA-1 -
what every authenticator app (Aegis, 2FAS, Google/Microsoft Authenticator, 1Password …) expects.
Plus single-use recovery codes and the QR code for setting up the app (segno, if installed).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import List, Optional
from urllib.parse import quote

STEP = 30
DIGITS = 6
WINDOW = 1          # accept one step before / after (clock drift)
RECOVERY_CODES = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret: str, code: str, last_step: int = 0, now: Optional[float] = None) -> Optional[int]:
    """The matching time step (to store, so a code can't be used twice) or None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret: str, account: str, issuer: str = "PiCore") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(text: str) -> str:
    """QR code as SVG markup, "" without the segno package."""
    try:
        import io
        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff", xmldecl=False, svgns=True)
    return buf.getvalue().decode()


def _norm_recovery(code: str) -> str:
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def recovery_codes() -> List[str]:
    """Readable single-use codes like 4f7k-2m9q (no 0/o, 1/l)."""
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(RECOVERY_CODES)]


def hash_recovery(code: str) -> str:
    return hashlib.sha256(_norm_recovery(code).encode()).hexdigest()


def is_recovery_format(code: str) -> bool:
    return len(_norm_recovery(code)) == 8 and not _norm_recovery(code).isdigit()
