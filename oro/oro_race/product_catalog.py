#!/usr/bin/env python3
"""Fetch product details from the ORO search catalog server."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

DEFAULT_CATALOG = "http://135.181.3.178:5632"

PRODUCT_FIELDS = ("product_id", "title", "attributes", "price", "sku_options", "service", "category")


def slim_product(product: dict) -> dict:
    title = product.get("title")
    if isinstance(title, list):
        title = " ".join(str(t) for t in title)
    service = product.get("service")
    if isinstance(service, list):
        service = [str(s) for s in service]
    return {
        "product_id": str(product.get("product_id") or ""),
        "title": str(title or ""),
        "attributes": product.get("attributes") if isinstance(product.get("attributes"), dict) else {},
        "price": product.get("price"),
        "sku_options": product.get("sku_options") if isinstance(product.get("sku_options"), dict) else {},
        "service": service or [],
        "category": str(product.get("category") or ""),
    }


def fetch_product_raw(product_ids: list[str], catalog_url: str = DEFAULT_CATALOG, chunk_size: int = 20) -> list[dict]:
    if not product_ids:
        return []
    results: list[dict] = []
    base = catalog_url.rstrip("/")
    for offset in range(0, len(product_ids), chunk_size):
        chunk = product_ids[offset : offset + chunk_size]
        params = urllib.parse.urlencode({"product_ids": ",".join(chunk)})
        url = f"{base}/get_product_raw?{params}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            batch = json.load(resp)
        if isinstance(batch, list):
            results.extend(slim_product(item) for item in batch if isinstance(item, dict))
        time.sleep(0.03)
    return results


fetch_product_info = fetch_product_raw


def parse_product_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
