#!/usr/bin/env python3
"""Deterministic short query codes via hash — no lookup database needed."""

from __future__ import annotations

import hashlib
import re

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
DEFAULT_WIDTH = 4


def normalize_query(query: str) -> str:
    """Remove line breaks and collapse extra spaces."""
    text = query.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r" +", " ", text).strip()


def encode_query(query: str, width: int = DEFAULT_WIDTH) -> str:
    """Calculate a fixed code from query text. Same query always gives same code."""
    digest = hashlib.sha256(normalize_query(query).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    base = len(ALPHABET)
    chars = []
    for _ in range(width):
        chars.append(ALPHABET[value % base])
        value //= base
    return "".join(reversed(chars))


def verify(code: str, query: str, width: int = DEFAULT_WIDTH) -> bool:
    """Verify a code matches a query by recalculating the hash."""
    return encode_query(query, width).lower() == code.strip().lower()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Encode or verify query codes (hash-based, no database)."
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Code length (default: 4)")
    parser.add_argument("--encode", metavar="QUERY", help="Calculate code for a query")
    parser.add_argument("--verify", nargs=2, metavar=("CODE", "QUERY"), help="Verify code matches query")
    args = parser.parse_args()

    if args.encode:
        print(encode_query(args.encode, args.width))

    if args.verify:
        code, query = args.verify
        print("MATCH" if verify(code, query, args.width) else "NO MATCH")


if __name__ == "__main__":
    main()
