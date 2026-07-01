#!/usr/bin/env python3
"""Build my_10.py: product from ag_pig_double_red.py; shop + voucher from ag_v2.py."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIG = ROOT / "ag_pig_double_red.py"
V2 = ROOT / "ag_v2.py"
OUT = ROOT / "my_10.py"

# ag_pig_double_red.py — product lane (Agent → ProductEngine)
PIG_TOOLS = (15, 243)
PIG_CORE = (245, 8945)
PIG_PRODUCT = (9745, 10006)
PIG_AGENT = (11047, 12853)

# ag_v2.py — shop + voucher lane (Agent → ShopEngine / VoucherEngine)
V2_TOOLS = (22, 38)
V2_CORE = (40, 1666)
V2_SHOP_VOUCHER = (1702, 2093)

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
        "random",
        "cartesian_product",
        "ToolCallResult",
        "registerSandboxTool",
    }
)


class PrefixRenamer(ast.NodeTransformer):
    def __init__(self, rename_map: dict[str, str]) -> None:
        self.rename_map = rename_map

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.rename_map:
            return ast.copy_location(ast.Name(id=self.rename_map[node.id], ctx=node.ctx), node)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if node.attr in self.rename_map:
            node.attr = self.rename_map[node.attr]
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


def _patch_pig_product(source: str) -> str:
    new_map = (
        "ENGINE_MAP: Dict[pdr_Challenge, Type[pdr_BaseEngine]] = {\n"
        "        pdr_Challenge.PRODUCT: pdr_ProductEngine,\n"
        "    }"
    )
    source, count = re.subn(
        r"ENGINE_MAP: Dict\[pdr_Challenge, Type\[pdr_BaseEngine\]\] = \{[^}]+\}",
        new_map,
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("pig ENGINE_MAP patch target not found")

    source = source.replace("@Tool\ndef pdr_", "def pdr_")
    source = source.replace("def pdr_agent_main(", "def pdr_run_product_pipeline(", 1)
    return source


def _patch_v2_shop_voucher(source: str) -> str:
    new_map = (
        "ENGINE_MAP: Dict[v2_Challenge, Type[v2_BaseEngine]] = {\n"
        "        v2_Challenge.SHOP: v2_ShopEngine,\n"
        "        v2_Challenge.VOUCHER: v2_VoucherEngine,\n"
        "    }"
    )
    source, count = re.subn(
        r"ENGINE_MAP: Dict\[v2_Challenge, Type\[v2_BaseEngine\]\] = \{[^}]+\}",
        new_map,
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("v2 ENGINE_MAP patch target not found")

    source = source.replace("@Tool\ndef v2_", "def v2_")

    return source


def _collect_imports() -> str:
    return (
        '"""Combined ORO agent: product from ag_pig_double_red.py; shop + voucher from ag_v2.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import logging\n"
        "import math\n"
        "import dataclasses\n"
        "import re\n"
        "import threading\n"
        "import time\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict\n"
        "from collections.abc import Sequence\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from functools import wraps\n"
        "from itertools import product\n"
        "from os import getenv\n"
        "from types import NoneType\n"
        "from typing import Any, Callable, ClassVar, NamedTuple, Optional, List, Dict, Tuple, Union, Type, TypeGuard, TypeVar, get_origin, get_args\n"
        "from urllib.parse import quote_plus\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_pig_double_red.py, shop + voucher: ag_v2.py
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


        def _install_pig_product_tools() -> None:
            register_tool("find_product", pdr_find_product)
            register_tool("view_product_information", pdr_view_product_information)
            register_tool("recommend_product", pdr_recommend_product)
            register_tool("terminate", pdr_terminate)


        def _install_v2_shop_voucher_tools() -> None:
            register_tool("find_product", v2_find_product)
            register_tool("view_product_information", v2_view_product_information)
            register_tool("recommend_product", v2_recommend_product)
            register_tool("terminate", v2_terminate)


        def v2_shop_voucher_agent_main(prob: dict) -> list:
            query = prob.get("query", "") if isinstance(prob, dict) else ""
            dialog: list = []
            try:
                agent = v2_Agent(query)
                agent.run()
                dialog = agent.get_dialog()
            except Exception as e:
                dialog = [
                    create_dialogue_step(
                        think=f"Unhandled agent error: {type(e).__name__}: {e}",
                        tool_results=[],
                        response="Done.",
                        query=query,
                        step=1,
                    )
                ]
            return dialog


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_identify_challenge(query)
            if kind == "product":
                _install_pig_product_tools()
                return pdr_run_product_pipeline(problem_data)
            _install_v2_shop_voucher_tools()
            return v2_shop_voucher_agent_main(problem_data)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    pig_raw = (
        _strip_leading_imports(_slice_lines(PIG, *PIG_TOOLS))
        + _strip_leading_imports(_slice_lines(PIG, *PIG_CORE))
        + _strip_leading_imports(_slice_lines(PIG, *PIG_PRODUCT))
        + _strip_leading_imports(_slice_lines(PIG, *PIG_AGENT))
    )
    v2_raw = (
        _strip_leading_imports(_slice_lines(V2, *V2_TOOLS))
        + _strip_leading_imports(_slice_lines(V2, *V2_CORE))
        + _strip_leading_imports(_slice_lines(V2, *V2_SHOP_VOUCHER))
    )

    pig_body = _patch_pig_product(_prefix_source(pig_raw, "pdr_"))
    v2_body = _patch_v2_shop_voucher(_prefix_source(v2_raw, "v2_"))

    parts = [
        _collect_imports(),
        "# " + "=" * 77 + "\n",
        "# Product lane (ag_pig_double_red.py — pdr_* Agent / ProductEngine)\n",
        "# " + "=" * 77 + "\n\n",
        pig_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop + voucher lane (ag_v2.py — v2_* Agent / ShopEngine + VoucherEngine)\n",
        "# " + "=" * 77 + "\n\n",
        v2_body,
        "\n",
        _build_router(),
        "\n",
    ]
    combined = "".join(parts)
    ast.parse(combined)
    OUT.write_text(combined, encoding="utf-8")
    size = OUT.stat().st_size
    line_count = combined.count("\n") + 1
    if size >= 1_048_576:
        raise SystemExit(f"Output exceeds 1 MB limit: {size} bytes")
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
