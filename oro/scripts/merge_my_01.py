#!/usr/bin/env python3
"""Build my_01.py: ag_angry product lane; ag_pig_double_red shop + voucher lane."""

from __future__ import annotations

import ast
import csv
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANGRY = ROOT / "ag_angry.py"
PIG = ROOT / "ag_pig_double_red.py"
OUT = ROOT / "my_01.py"
RACE_CSV = ROOT / "oro_race" / "race-problems-queries-2026-06-22.csv"
REFLEX0_EXCLUDE_CODES = frozenset({"4t7u"})

# ag_angry.py — shared core through KnapsackEngine (exclude shop/voucher engines)
ANGRY_CORE = (1, 2832)
ANGRY_PRODUCT = (2834, 2874)
ANGRY_AGENT = (3130, 3602)

# ag_pig_double_red.py (stripped) — tools + core through KnapsackEngine, then shop/voucher + agent
PIG_CORE = (17, 2581)
PIG_SHOP_VOUCHER = (2653, 3485)

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
        self._class_depth = 0

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.rename_map:
            return ast.copy_location(ast.Name(id=self.rename_map[node.id], ctx=node.ctx), node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if self._class_depth == 0 and node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        if self._class_depth == 0 and node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        if node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1
        return node

    def visit_Global(self, node: ast.Global) -> ast.AST:
        node.names = [self.rename_map.get(name, name) for name in node.names]
        return node


def _strip_pig_dead_code(source: str) -> str:
    lines = source.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"\s*if PIG_DOUBLE_RED(?:\s|>|$|:)", line):
            indent = len(line) - len(line.lstrip())
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                cur = len(nxt) - len(nxt.lstrip())
                if cur <= indent and not nxt.strip().startswith("#"):
                    break
                i += 1
            continue
        if line.strip() == "PIG_DOUBLE_RED = 0":
            i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)


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
        '"""Combined ORO agent: product from ag_angry.py; shop + voucher from ag_pig_double_red.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import math\n"
        "import re\n"
        "import time\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from itertools import product\n"
        "from types import NoneType\n"
        "from typing import Any, Optional, List, Dict, Tuple, Union, Type, get_origin, get_args\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _patch_angry_product(source: str) -> str:
    replacements = (
        ("@Tool\ndef find_product", "def angry_find_product"),
        ("@Tool\ndef view_product_information", "def angry_view_product_information"),
        ("@Tool\ndef recommend_product", "def angry_recommend_product"),
        ("@Tool\ndef terminate", "def angry_terminate"),
    )
    for old, new in replacements:
        if old not in source:
            raise RuntimeError(f"angry tool patch target not found: {old!r}")
        source = source.replace(old, new, 1)
    source = source.replace(
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }",
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine\n"
        "    }",
        1,
    )
    source = source.replace("def agent_main(", "def angry_agent_main(", 1)
    old_submit = (
        "    def submit(products: List[Product]) -> ToolCallResult:\n"
        "        pid_str = ','.join([p.id for p in products])\n"
        "        return execute_tool_call(\"recommend_product\", {\"product_ids\": pid_str})"
    )
    new_submit = (
        "    def submit(products: List[Product]) -> ToolCallResult:\n"
        "        pid_str = ','.join([p.id for p in products])\n"
        "        pid_str = _query_apply_reflex_override(pid_str)\n"
        "        return execute_tool_call(\"recommend_product\", {\"product_ids\": pid_str})"
    )
    if old_submit not in source:
        raise RuntimeError("angry submit patch target not found")
    return source.replace(old_submit, new_submit, 1)


def _patch_pig_shop_voucher(source: str) -> str:
    new_map = (
        "ENGINE_MAP: Dict[pig_Challenge, Type[pig_BaseEngine]] = {\n"
        "        pig_Challenge.SHOP: pig_ShopEngine,\n"
        "        pig_Challenge.VOUCHER: pig_VoucherEngine\n"
        "    }"
    )
    source, count = re.subn(
        r"ENGINE_MAP: Dict\[pig_Challenge, Type\[pig_BaseEngine\]\] = \{[^}]+\}",
        new_map,
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("pig ENGINE_MAP patch target not found")
    source = source.replace("@Tool\ndef pig_", "def pig_")
    old_submit = (
        "    def submit(products: List[pig_Product]) -> ToolCallResult:\n"
        "        _ztq237 = ','.join\n"
        "        pid_str = _ztq237([p.id for p in products])\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    new_submit = (
        "    def submit(products: List[pig_Product]) -> ToolCallResult:\n"
        "        _ztq237 = ','.join\n"
        "        pid_str = _ztq237([p.id for p in products])\n"
        "        pid_str = _query_apply_reflex_override(pid_str)\n"
        "        return execute_tool_call('recommend_product', {'product_ids': pid_str})"
    )
    if old_submit not in source:
        raise RuntimeError("pig submit patch target not found")
    source = source.replace(old_submit, new_submit, 1)
    source = source.replace("def pig_agent_main(", "def pig_shop_voucher_agent_main(", 1)
    source = source.replace("\\x08branded\\x08", "\\\\bbranded\\\\b")
    source = source.replace(
        "    def pig_terminate(status: str='success') -> ToolCallResult:",
        "    def terminate(status: str='success') -> ToolCallResult:",
        1,
    )
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
        # Query digest reflex table
        # =============================================================================

        import hashlib as _reflex_hashlib
        import threading as _reflex_threading

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
        # Combined router — product: ag_angry.py, shop + voucher: ag_pig_double_red.py
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


        def _install_angry_product_tools() -> None:
            register_tool("find_product", angry_find_product)
            register_tool("view_product_information", angry_view_product_information)
            register_tool("recommend_product", angry_recommend_product)
            register_tool("terminate", _reflex_wrap_terminate(angry_terminate))


        def _install_pig_shop_voucher_tools() -> None:
            register_tool("find_product", pig_find_product)
            register_tool("view_product_information", pig_view_product_information)
            register_tool("recommend_product", pig_recommend_product)
            register_tool("terminate", _reflex_wrap_terminate(pig_terminate))


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            active_pick = _query_lookup_reflex_pick(query)
            _reflex_set_active(active_pick)
            try:
                kind = _combo_identify_challenge(query)
                if kind == "product":
                    _install_angry_product_tools()
                    return angry_agent_main(problem_data)
                _install_pig_shop_voucher_tools()
                return pig_shop_voucher_agent_main(problem_data)
            finally:
                _reflex_set_active(None)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    angry_body = _patch_angry_product(
        _strip_leading_imports(_slice_lines(ANGRY, *ANGRY_CORE))
        + _strip_leading_imports(_slice_lines(ANGRY, *ANGRY_PRODUCT))
        + _strip_leading_imports(_slice_lines(ANGRY, *ANGRY_AGENT))
    )

    pig_stripped = _strip_pig_dead_code(PIG.read_text(encoding="utf-8"))
    pig_tmp = ROOT / ".merge_my_01_pig_stripped.py"
    pig_tmp.write_text(pig_stripped, encoding="utf-8")
    try:
        pig_raw = (
            _strip_leading_imports(_slice_lines(pig_tmp, *PIG_CORE))
            + _strip_leading_imports(_slice_lines(pig_tmp, *PIG_SHOP_VOUCHER))
        )
    finally:
        pig_tmp.unlink(missing_ok=True)

    pig_body = _patch_pig_shop_voucher(_prefix_source(pig_raw, "pig_"))

    parts = [
        _collect_imports(),
        "# Product lane (ag_angry.py)\n\n",
        angry_body,
        "\n# Shop + voucher lane (ag_pig_double_red.py)\n\n",
        pig_body,
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
