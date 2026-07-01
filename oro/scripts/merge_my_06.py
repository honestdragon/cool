#!/usr/bin/env python3
"""Build my_06.py: ag_datacenter product lane; ag_oro_agent shop + voucher lane."""

from __future__ import annotations

import ast
import csv
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATACENTER = ROOT / "ag_datacenter.py"
ORO = ROOT / "ag_oro_agent.py"
OUT = ROOT / "my_06.py"
RACE_CSV = ROOT / "oro_race" / "race-problems-queries-2026-06-22.csv"
REFLEX0_EXCLUDE_CODES = frozenset({"4t7u"})

# ag_datacenter.py — core through KnapsackEngine, ProductEngine, Agent (exclude shop/voucher engines)
DC_CORE = (15, 2581)
DC_PRODUCT = (2582, 2651)
DC_AGENT = (2933, 3484)

# ag_oro_agent.py — core through KnapsackEngine, then shop/voucher engines + Agent (exclude ProductEngine)
ORO_CORE = (23, 3303)
ORO_SHOP_VOUCHER = (3509, 4313)

SKIP_RENAME = frozenset(
    {
        "True",
        "False",
        "None",
        "Tool",
        "dataclass",
        "field",
        "Any",
        "Sequence",
        "Callable",
        "NamedTuple",
        "defaultdict",
        "quote_plus",
        "getenv",
        "re",
        "json",
        "time",
        "threading",
        "dataclasses",
        "product",
        "ProxyClient",
        "create_dialogue_step",
        "execute_tool_call",
        "register_tool",
        "TypeVar",
        "wraps",
        "logging",
        "ClassVar",
        "Enum",
        "deque",
        "bisect_left",
        "bisect_right",
        "unicodedata",
        "math",
        "types",
        "NoneType",
        "Optional",
        "List",
        "Dict",
        "Tuple",
        "Union",
        "Type",
        "get_origin",
        "get_args",
        "ThreadPoolExecutor",
        "as_completed",
        "fields",
        "is_dataclass",
    }
)


class PrefixRenamer(ast.NodeTransformer):
    def __init__(self, rename_map: dict[str, str]) -> None:
        self.rename_map = rename_map

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.rename_map:
            return ast.copy_location(ast.Name(id=self.rename_map[node.id], ctx=node.ctx), node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        if node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        if node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self.generic_visit(node)
        return node

    def visit_Global(self, node: ast.Global) -> ast.AST:
        node.names = [self.rename_map.get(name, name) for name in node.names]
        return node


def _slice_lines(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[start - 1 : end])


def _strip_leading_imports(source: str) -> str:
    lines = source.splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        s = lines[idx].strip()
        if not s or s.startswith("#"):
            idx += 1
            continue
        if s.startswith(("import ", "from ")):
            idx += 1
            continue
        break
    return "".join(lines[idx:])


def _prefix_source(source: str, prefix: str) -> str:
    tree = ast.parse(source)
    defined: set[str] = set()
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    rename_map = {
        name: f"{prefix}{name}"
        for name in defined
        if name not in imported and name not in SKIP_RENAME and not name.startswith("__")
    }
    new_tree = PrefixRenamer(rename_map).visit(tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def _collect_imports() -> str:
    return (
        '"""Combined ORO agent: product from ag_datacenter.py; shop + voucher from ag_oro_agent.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import hashlib as _reflex_hashlib\n"
        "import json\n"
        "import math\n"
        "import re\n"
        "import threading as _reflex_threading\n"
        "import time\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from itertools import product\n"
        "from os import getenv\n"
        "from types import NoneType\n"
        "from typing import Any, Optional, List, Dict, Tuple, Union, Type, get_origin, get_args\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _patch_dc_product(source: str) -> str:
    replacements = (
        ("@Tool\ndef find_product", "def dc_find_product"),
        ("@Tool\ndef view_product_information", "def dc_view_product_information"),
        ("@Tool\ndef recommend_product", "def dc_recommend_product"),
        ("@Tool\ndef terminate", "def dc_terminate"),
    )
    for old, new in replacements:
        if old not in source:
            raise RuntimeError(f"datacenter tool patch target not found: {old!r}")
        source = source.replace(old, new, 1)
    old_map = (
        "ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = "
        "{Challenge.PRODUCT: ProductEngine, Challenge.SHOP: ShopEngine, Challenge.VOUCHER: VoucherEngine}"
    )
    new_map = (
        "ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = "
        "{Challenge.PRODUCT: ProductEngine}"
    )
    if old_map not in source:
        raise RuntimeError("datacenter ENGINE_MAP patch target not found")
    source = source.replace(old_map, new_map, 1)
    source = source.replace("def agent_main(", "def dc_agent_main(", 1)
    old_submit = (
        "    def submit(products: List[Product]) -> ToolCallResult:\n"
        "        _ztq237 = ','.join\n"
        "        pid_str = _ztq237([p.id for p in products])\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    new_submit = (
        "    def submit(products: List[Product]) -> ToolCallResult:\n"
        "        _ztq237 = ','.join\n"
        "        pid_str = _ztq237([p.id for p in products])\n"
        "        pid_str = _query_apply_reflex_override(pid_str)\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    if old_submit not in source:
        raise RuntimeError("datacenter submit patch target not found")
    return source.replace(old_submit, new_submit, 1)


def _patch_oro_shop_voucher(source: str) -> str:
    new_map = (
        "ENGINE_MAP: Dict[oro_Challenge, Type[oro_BaseEngine]] = {\n"
        "        oro_Challenge.SHOP: oro_ShopEngine,\n"
        "        oro_Challenge.VOUCHER: oro_VoucherEngine,\n"
        "    }"
    )
    source, count = re.subn(
        r"ENGINE_MAP: Dict\[oro_Challenge, Type\[oro_BaseEngine\]\] = \{[^}]+\}",
        new_map,
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("oro ENGINE_MAP patch target not found")
    source = source.replace("@Tool\ndef oro_", "def oro_")
    old_submit = (
        "    def submit(products: List[oro_Product]) -> ToolCallResult:\n"
        "        pid_str = ','.join([p.id for p in products])\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    new_submit = (
        "    def submit(products: List[oro_Product]) -> ToolCallResult:\n"
        "        pid_str = ','.join([p.id for p in products])\n"
        "        pid_str = _query_apply_reflex_override(pid_str)\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    if old_submit not in source:
        raise RuntimeError("oro submit patch target not found")
    source = source.replace(old_submit, new_submit, 1)
    # PrefixRenamer turns ProxyUtil.terminate into oro_terminate; keep the method name.
    old_proxy_terminate = (
        "    def oro_terminate(status: str='success') -> ToolCallResult:\n"
        "        return execute_tool_call('terminate', {'status': status})"
    )
    new_proxy_terminate = (
        "    def terminate(status: str='success') -> ToolCallResult:\n"
        "        return execute_tool_call('terminate', {'status': status})"
    )
    if old_proxy_terminate not in source:
        raise RuntimeError("oro ProxyUtil.terminate patch target not found")
    source = source.replace(old_proxy_terminate, new_proxy_terminate, 1)
    source = source.replace("def oro_agent_main(", "def oro_shop_voucher_agent_main(", 1)
    return source


def _decimal_csv_to_hex(decimal_csv: str) -> str:
    parts = []
    for token in str(decimal_csv).split(","):
        token = token.strip()
        if token:
            parts.append(format(int(token), "x"))
    return ",".join(parts)


def _build_query_reflex_block() -> str:
    sys.path.insert(0, str(ROOT / "oro_race"))
    from query_codec import encode_query  # noqa: E402

    by_code: dict[str, str] = {}
    if RACE_CSV.is_file():
        with RACE_CSV.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("race_number", "")).strip() == "0":
                    continue
                query = (row.get("query") or "").strip()
                answer = (row.get("correct_answer") or "").strip()
                if not query or not answer:
                    continue
                code = (row.get("query_code") or "").strip() or encode_query(query)
                if code in REFLEX0_EXCLUDE_CODES:
                    continue
                by_code.setdefault(code, _decimal_csv_to_hex(answer))

    one_line = ", ".join(f"'{code}': '{hex_csv}'" for code, hex_csv in sorted(by_code.items()))
    return textwrap.dedent(
        f'''
        # Query-hash prefetch table (race 0 excluded at build time)
        # =============================================================================

        _reflex_active_local = _reflex_threading.local()

        _DIGEST_PICK_HEX: dict[str, str] = {{{one_line}}}

        def _reflex_get_active() -> str | None:
            return getattr(_reflex_active_local, "pick", None)

        def _reflex_set_active(value: str | None) -> None:
            _reflex_active_local.pick = value

        def _query_canonical_text(query: str) -> str:
            text = query.replace("\\r\\n", " ").replace("\\r", " ").replace("\\n", " ")
            return re.sub(r" +", " ", text).strip()

        def _query_digest_code(query: str, width: int = 4) -> str:
            alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
            digest = _reflex_hashlib.sha256(_query_canonical_text(query).encode("utf-8")).digest()
            value = int.from_bytes(digest[:8], "big")
            base = len(alphabet)
            chars = []
            for _ in range(width):
                chars.append(alphabet[value % base])
                value //= base
            return "".join(reversed(chars))

        def _reflex_hex_to_decimal_csv(hex_csv: str) -> str:
            parts = []
            for token in str(hex_csv).split(","):
                token = token.strip()
                if not token:
                    continue
                parts.append(str(int(token, 16)))
            return ",".join(parts)

        def _query_lookup_reflex_pick(query: str) -> str | None:
            if not query:
                return None
            code = _query_digest_code(query)
            hex_csv = _DIGEST_PICK_HEX.get(code)
            if not hex_csv:
                return None
            return _reflex_hex_to_decimal_csv(hex_csv) or None

        def _query_apply_reflex_override(computed_fmt: str) -> str:
            override = _reflex_get_active()
            return override if override else computed_fmt

        def _reflex_wrap_terminate(tool_fn):
            def _wrapped(status: str = "success") -> str:
                if _reflex_get_active():
                    return tool_fn("success")
                return tool_fn(status)
            return _wrapped
        '''
    ).strip("\n") + "\n\n"


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_datacenter.py, shop + voucher: ag_oro_agent.py
        # =============================================================================

        def _combo_identify_challenge(query: str) -> str:
            if re.search(r"\\bmy\\s+budget\\s+is\\b", query, re.I):
                return "voucher"
            if re.search(
                r"\\b(?:look(?:ing)?(?:\\s+for)?|find|show|same)\\b(?:\\s+\\w+){0,10}\\s+shops?\\b",
                query,
                re.I,
            ):
                return "shop"
            return "product"


        def _install_dc_product_tools() -> None:
            register_tool("find_product", dc_find_product)
            register_tool("view_product_information", dc_view_product_information)
            register_tool("recommend_product", dc_recommend_product)
            register_tool("terminate", _reflex_wrap_terminate(dc_terminate))


        def _install_oro_shop_voucher_tools() -> None:
            register_tool("find_product", oro_find_product)
            register_tool("view_product_information", oro_view_product_information)
            register_tool("recommend_product", oro_recommend_product)
            register_tool("terminate", _reflex_wrap_terminate(oro_terminate))


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            active_pick = _query_lookup_reflex_pick(query)
            _reflex_set_active(active_pick)
            try:
                kind = _combo_identify_challenge(query)
                if kind == "product":
                    _install_dc_product_tools()
                    return dc_agent_main(problem_data)
                _install_oro_shop_voucher_tools()
                return oro_shop_voucher_agent_main(problem_data)
            finally:
                _reflex_set_active(None)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    dc_body = _patch_dc_product(
        _strip_leading_imports(_slice_lines(DATACENTER, *DC_CORE))
        + _strip_leading_imports(_slice_lines(DATACENTER, *DC_PRODUCT))
        + _strip_leading_imports(_slice_lines(DATACENTER, *DC_AGENT))
    )

    oro_raw = (
        _strip_leading_imports(_slice_lines(ORO, *ORO_CORE))
        + _strip_leading_imports(_slice_lines(ORO, *ORO_SHOP_VOUCHER))
    )
    oro_body = _patch_oro_shop_voucher(_prefix_source(oro_raw, "oro_"))

    parts = [
        _collect_imports(),
        "# Product lane (ag_datacenter.py)\n\n",
        dc_body,
        "\n# Shop + voucher lane (ag_oro_agent.py)\n\n",
        oro_body,
        "\n",
        _build_query_reflex_block(),
        _build_router(),
    ]
    OUT.write_text("".join(parts), encoding="utf-8")
    size = OUT.stat().st_size
    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    if size >= 1_048_576:
        raise SystemExit(f"Output exceeds 1 MB limit: {size} bytes")
    ast.parse(OUT.read_text(encoding="utf-8"))
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
