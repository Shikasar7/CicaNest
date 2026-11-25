"""Ed25519 signatures (RFC 8032), in plain Python: a plugin author's signature in the market list.

Checking one holds no secret, so it need not run in constant time; signing (the `nest plugin-sign` command) is
run by an author on their own machine, on a file of their own.
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
ROOT = pow(2, (P - 1) // 4, P)  # a square root of -1

Point = tuple[int, int, int, int]  # extended coordinates


def _x_of(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    square = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if square == 0:
        return None if sign else 0
    x = pow(square, (P + 3) // 8, P)
    if (x * x - square) % P != 0:
        x = x * ROOT % P
    if (x * x - square) % P != 0:
        return None
    return P - x if (x & 1) != sign else x


_BASE_Y = 4 * pow(5, P - 2, P) % P
_BASE_X = _x_of(_BASE_Y, 0)
assert _BASE_X is not None
BASE: Point = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % P)


def _add(a: Point, b: Point) -> Point:
    s = (a[1] - a[0]) * (b[1] - b[0]) % P
    t = (a[1] + a[0]) * (b[1] + b[0]) % P
    u = 2 * a[3] * b[3] * D % P
    v = 2 * a[2] * b[2] % P
    e, f, g, h = t - s, v - u, v + u, t + s
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _times(n: int, point: Point) -> Point:
    out: Point = (0, 1, 1, 0)
    while n > 0:
        if n & 1:
            out = _add(out, point)
        point = _add(point, point)
        n >>= 1
    return out


def _same(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _pack(point: Point) -> bytes:
    inverse = pow(point[2], P - 2, P)
    x, y = point[0] * inverse % P, point[1] * inverse % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _unpack(data: bytes) -> Point | None:
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _x_of(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _hash(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little") % L


def _secret(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("a seed is 32 bytes")
    digest = hashlib.sha512(seed).digest()
    a = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return a, digest[32:]


def public_key(seed: bytes) -> bytes:
    return _pack(_times(_secret(seed)[0], BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _secret(seed)
    public = _pack(_times(a, BASE))
    r = _hash(prefix, message)
    point = _pack(_times(r, BASE))
    s = (r + _hash(point, public, message) * a) % L
    return point + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _unpack(public), _unpack(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= L:
        return False
    k = _hash(signature[:32], public, message)
    return _same(_times(s, BASE), _add(r, _times(k, a)))
