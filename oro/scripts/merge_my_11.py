#!/usr/bin/env python3
"""Build my_11.py: product from ag_pig_double_red.py; shop + voucher from ag_halo.py."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIG = ROOT / "ag_pig_double_red.py"
HALO = ROOT / "ag_halo.py"
OUT = ROOT / "my_11.py"

# ag_pig_double_red.py (stripped) — tools + core + ProductEngine + Agent (no shop/voucher engines)
PIG_TOOLS = (16, 36)
PIG_CORE = (38, 2403)
PIG_PRODUCT = (2643, 2712)
PIG_AGENT = (2994, 3532)

# ag_halo.py — core + KnapsackEngine + ShopEngine + VoucherEngine + Agent (exclude ProductEngine)
HALO_CORE = (1, 3416)
HALO_KNAPSACK = (3417, 3642)
HALO_SHOP_VOUCHER = (3683, 3852)
HALO_AGENT = (3853, 4331)

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
        "deque",
        "quote_plus",
        "getenv",
        "re",
        "json",
        "time",
        "threading",
        "dataclasses",
        "logging",
        "product",
        "islice",
        "ProxyClient",
        "create_dialogue_step",
        "execute_tool_call",
        "register_tool",
        "TypeVar",
        "wraps",
        "ClassVar",
        "Enum",
        "bisect_left",
        "bisect_right",
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
        '"""Combined ORO agent: product from ag_pig_double_red.py; shop + voucher from ag_halo.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import logging\n"
        "import math\n"
        "import re\n"
        "import threading\n"
        "import time\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict, deque\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from itertools import islice, product\n"
        "from os import getenv\n"
        "from types import NoneType\n"
        "from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union, get_args, get_origin\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _patch_pig_product(source: str) -> str:
    source = source.replace("'\x08branded\x08'", "'\\bbranded\\b'")
    new_map = (
        "ENGINE_MAP: Dict[pig_Challenge, Type[pig_BaseEngine]] = {\n"
        "        pig_Challenge.PRODUCT: pig_ProductEngine\n"
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
    source = source.replace("def pig_agent_main(", "def pig_product_agent_main(", 1)
    return source


def _patch_halo_shop_voucher(source: str) -> str:
    replacements = (
        ("@Tool\ndef find_product", "def halo_find_product"),
        ("@Tool\ndef view_product_information", "def halo_view_product_information"),
        ("@Tool\ndef calculate_voucher", "def halo_calculate_voucher"),
        ("@Tool\ndef recommend_product", "def halo_recommend_product"),
        ("@Tool\ndef terminate", "def halo_terminate"),
    )
    for old, new in replacements:
        if old not in source:
            raise RuntimeError(f"halo tool patch target not found: {old!r}")
        source = source.replace(old, new, 1)
    source = source.replace(
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }",
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }",
        1,
    )
    source = source.replace("def agent_main(", "def halo_agent_main(", 1)
    return source


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_pig_double_red.py; shop + voucher: ag_halo.py
        # =============================================================================

        _COMBO_RX_BUDGET_ANCHOR = re.compile(
            r"(?:\\bmy\\s+budget\\s+is\\b|\\bbudget\\s+is\\b|\\bi\\s+have\\s+a\\s+voucher\\b)",
            re.IGNORECASE,
        )
        _COMBO_RX_SAME_STORE = re.compile(
            r"\\b("
            r"same\\s+(?:shop|store|seller|merchant|vendor)|"
            r"one\\s+(?:shop|store|seller)|"
            r"single\\s+(?:shop|store|seller|merchant)|"
            r"from\\s+the\\s+same\\s+(?:shop|store|seller)"
            r")\\b",
            re.IGNORECASE,
        )
        _COMBO_RX_MULTI_SPLIT = re.compile(
            r"(?:,?\\s*and\\s+also\\s+|,?\\s*also,?\\s*"
            r"|Second(?:ly)?,\\s*|Third(?:ly)?,\\s*|First,\\s*"
            r"|\\(\\d+\\)\\s*|\\d+\\.\\s*"
            r"|Additionally,\\s*|Furthermore,\\s*|Moreover,\\s*"
            r"|In\\s+addition,?\\s*|Plus,\\s*"
            r"|\\bThen\\s*,?\\s*I\\s+(?:need|want|also)\\b"
            r"|\\bI\\s+also\\s+(?:want|need)\\b)",
            re.IGNORECASE,
        )


        def _combo_multi_spec_slice_count(query: str) -> int:
            product_text = _COMBO_RX_BUDGET_ANCHOR.split(query)[0].strip() or query
            parts = [p.strip() for p in _COMBO_RX_MULTI_SPLIT.split(product_text) if p and len(p.strip()) > 10]
            return len(parts)


        def _combo_identify_challenge(query: str) -> str:
            q_lower = query.lower()
            if _COMBO_RX_BUDGET_ANCHOR.search(query):
                return "voucher"
            if re.search(r"\\b(budget|voucher)\\b", q_lower) and re.search(
                r"\\b(threshold|discount|cap|%\\s*off|my\\s+budget)\\b", q_lower
            ):
                return "voucher"
            if _COMBO_RX_SAME_STORE.search(query):
                return "shop"
            if _combo_multi_spec_slice_count(query) >= 2 and (
                _COMBO_RX_SAME_STORE.search(query) or re.search(r"\\bshop\\b", q_lower)
            ):
                return "shop"
            if "shop" in q_lower and (
                re.search(r"\\b(both|these|offering|offers|sells|same|together|along\\s+with)\\b", q_lower)
                or _COMBO_RX_MULTI_SPLIT.search(query)
            ):
                return "shop"
            return "product"


        def _install_pig_product_tools() -> None:
            register_tool("find_product", pig_find_product)
            register_tool("view_product_information", pig_view_product_information)
            register_tool("recommend_product", pig_recommend_product)
            register_tool("terminate", pig_terminate)


        def _install_halo_shop_tools() -> None:
            register_tool("find_product", halo_find_product)
            register_tool("view_product_information", halo_view_product_information)
            register_tool("recommend_product", halo_recommend_product)
            register_tool("terminate", halo_terminate)


        def _install_halo_voucher_tools() -> None:
            register_tool("find_product", halo_find_product)
            register_tool("view_product_information", halo_view_product_information)
            register_tool("recommend_product", halo_recommend_product)
            register_tool("calculate_voucher", halo_calculate_voucher)
            register_tool("terminate", halo_terminate)


        def _pig_product_run(problem_data: dict) -> list[dict]:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list[dict] = []
            _orig = pig_Agent.__identify_challenge__

            def _product_only(self: pig_Agent) -> pig_Challenge:
                return pig_Challenge.PRODUCT

            pig_Agent.__identify_challenge__ = _product_only
            try:
                agent = pig_Agent(query)
                agent.run()
                dialog = agent.get_dialog()
            except Exception as exc:
                dialog = [
                    create_dialogue_step(
                        think=f"Unhandled agent error: {type(exc).__name__}: {exc}",
                        tool_results=[],
                        response="Done.",
                        query=query,
                        step=1,
                    )
                ]
            finally:
                pig_Agent.__identify_challenge__ = _orig
            return dialog


        def _halo_shop_run(problem_data: dict) -> list[dict]:
            _orig = Agent.__identify_challenge__

            def _shop_only(self: Agent) -> Challenge:
                return Challenge.SHOP

            Agent.__identify_challenge__ = _shop_only
            try:
                return halo_agent_main(problem_data)
            finally:
                Agent.__identify_challenge__ = _orig


        def _halo_voucher_run(problem_data: dict) -> list[dict]:
            _orig = Agent.__identify_challenge__

            def _voucher_only(self: Agent) -> Challenge:
                return Challenge.VOUCHER

            Agent.__identify_challenge__ = _voucher_only
            try:
                return halo_agent_main(problem_data)
            finally:
                Agent.__identify_challenge__ = _orig


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_identify_challenge(query)
            if kind == "product":
                _install_pig_product_tools()
                return _pig_product_run(problem_data)
            if kind == "shop":
                _install_halo_shop_tools()
                return _halo_shop_run(problem_data)
            _install_halo_voucher_tools()
            return _halo_voucher_run(problem_data)
        '''
    ).strip("\n") + "\n"


def _verify_anti_cheat(combined: str) -> None:
    banned_imports = ("base64", "binascii", "codecs", "zlib")
    for mod in banned_imports:
        if re.search(rf"^\s*(?:import\s+{mod}|from\s+{mod}\s+import)", combined, re.M):
            raise RuntimeError(f"forbidden import detected: {mod}")

    banned_tokens = (
        "PIG_DOUBLE_RED",
        "RACE_PREFETCH",
        "_race_lookup_prefetch",
        "_race_resolve_final_ids",
        "_race_wrap_terminate",
        "eval(",
        "exec(",
        "__import__(",
    )
    for token in banned_tokens:
        if token in combined:
            raise RuntimeError(f"anti-cheat token still present: {token!r}")

    if "def halo_agent_main(" not in combined:
        raise RuntimeError("halo_agent_main missing — HALO_AGENT slice must include agent_main")

    if "import ag_pig_double_red" in combined or "import ag_halo" in combined:
        raise RuntimeError("source agent import detected")


def main() -> None:
    pig_stripped = _strip_pig_dead_code(PIG.read_text(encoding="utf-8"))
    pig_tmp = ROOT / ".merge_my_11_pig_stripped.py"
    pig_tmp.write_text(pig_stripped, encoding="utf-8")
    try:
        pig_raw = (
            _strip_leading_imports(_slice_lines(pig_tmp, *PIG_TOOLS))
            + _strip_leading_imports(_slice_lines(pig_tmp, *PIG_CORE))
            + _strip_leading_imports(_slice_lines(pig_tmp, *PIG_PRODUCT))
            + _strip_leading_imports(_slice_lines(pig_tmp, *PIG_AGENT))
        )
    finally:
        pig_tmp.unlink(missing_ok=True)

    pig_body = _patch_pig_product(_prefix_source(pig_raw, "pig_"))

    halo_body = _patch_halo_shop_voucher(
        _strip_leading_imports(_slice_lines(HALO, *HALO_CORE))
        + _strip_leading_imports(_slice_lines(HALO, *HALO_KNAPSACK))
        + _strip_leading_imports(_slice_lines(HALO, *HALO_SHOP_VOUCHER))
        + _strip_leading_imports(_slice_lines(HALO, *HALO_AGENT))
    )

    parts = [
        _collect_imports(),
        "# " + "=" * 77 + "\n",
        "# Product lane (ag_pig_double_red.py — pig_*)\n",
        "# " + "=" * 77 + "\n\n",
        pig_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop + voucher lane (ag_halo.py)\n",
        "# " + "=" * 77 + "\n\n",
        halo_body,
        "\n",
        _build_router(),
    ]
    combined = "".join(parts)
    ast.parse(combined)
    _verify_anti_cheat(combined)
    OUT.write_text(combined, encoding="utf-8")
    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    size = OUT.stat().st_size
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
