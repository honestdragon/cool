#!/usr/bin/env python3
"""Build my_09.py: product from ag_consensus.py; shop + voucher from ag_london.py."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LONDON = ROOT / "ag_london.py"
CONSENSUS = ROOT / "ag_consensus.py"
OUT = ROOT / "my_09.py"

# ag_consensus.py — product lane only
CONSENSUS_RANGES: list[tuple[int, int]] = [
    (14, 27),       # tools
    (28, 803),      # Prompt … EngineUtil
    (809, 1623),    # SegTree … BaseEngine (skip _knapsack_states)
    (1841, 1876),   # ProductEngine
    (1995, 2358),   # Agent
    (2359, 2369),   # agent_main
]

# ag_london.py — shop + voucher lane (exclude ProductEngine and enhancement helpers)
LONDON_RANGES: list[tuple[int, int]] = [
    (18, 34),       # tools
    (36, 2405),     # Prompt … BaseEngine
    (2406, 2632),   # KnapsackEngine
    (2675, 2848),   # ShopEngine + VoucherEngine
    (2849, 3263),   # Agent
]

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
        "generate_tool_call_id",
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
        "Set",
        "get_origin",
        "get_args",
        "fields",
        "is_dataclass",
        "functools",
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


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _slice_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> str:
    picked: list[str] = []
    for start, end in ranges:
        picked.extend(lines[start - 1 : end])
    return "".join(picked)


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
        '"""Combined ORO agent: product from ag_consensus.py; shop + voucher from ag_london.py."""\n\n'
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
        "from os import getenv\n"
        "from types import NoneType\n"
        "from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union, get_args, get_origin\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _sanitize_consensus_prompts(source: str) -> str:
    """Replace consensus prompt examples that mirror current suite qualifier queries."""
    replacements = [
        (
            '"blue button flex replacement for Oppo A5S" -> product_type→button flex, compatibility→oppo a5s, color→blue.',
            '"red leather case for Galaxy S21" -> product_type→phone case, compatibility→galaxy s21, color→red, material→leather.',
        ),
        (
            'Replacement or accessory queries (e.g. "button flex for Oppo A5S") match when model/compatibility appears',
            'Replacement or accessory queries (e.g. "screen protector for Pixel 7") match when model/compatibility appears',
        ),
        ('"priced from 180 to 505 PHP"', '"priced from 25 to 75 PHP"'),
        ('"priced above 410 PHP"', '"priced above 50 PHP"'),
        ('"cost is above 85 PHP"', '"cost is above 30 PHP"'),
        ('"cost over 176 PHP"', '"cost over 40 PHP"'),
    ]
    for old, new in replacements:
        if old not in source:
            raise RuntimeError(f"consensus prompt sanitize target not found: {old!r}")
        source = source.replace(old, new, 1)
    return source


def _patch_consensus_lane(source: str) -> str:
    for old in (
        "@Tool\ndef find_product",
        "@Tool\ndef view_product_information",
        "@Tool\ndef recommend_product",
        "@Tool\ndef terminate",
    ):
        fn = old.split("\ndef ", 1)[1]
        if old not in source:
            raise RuntimeError(f"consensus tool patch target not found: {old!r}")
        source = source.replace(old, f"def {fn}", 1)

    old_map = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {Challenge.PRODUCT: ProductEngine, Challenge.SHOP: ShopEngine, Challenge.VOUCHER: VoucherEngine}"
    )
    new_map = "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {Challenge.PRODUCT: ProductEngine}"
    if old_map not in source:
        raise RuntimeError("consensus ENGINE_MAP patch target not found")
    source = source.replace(old_map, new_map, 1)
    return source


def _patch_consensus_after_prefix(text: str) -> str:
    return text.replace("def con_agent_main(", "def consensus_product_agent_main(", 1)


def _patch_london_lane(source: str) -> str:
    for old in (
        "@Tool\ndef find_product",
        "@Tool\ndef view_product_information",
        "@Tool\ndef recommend_product",
        "@Tool\ndef terminate",
    ):
        fn = old.split("\ndef ", 1)[1]
        if old not in source:
            raise RuntimeError(f"london tool patch target not found: {old!r}")
        source = source.replace(old, f"def {fn}", 1)

    old_map = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }"
    )
    new_map = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }"
    )
    if old_map not in source:
        raise RuntimeError("london ENGINE_MAP patch target not found")
    source = source.replace(old_map, new_map, 1)
    return source


def _append_london_agent_main(text: str) -> str:
    return text + (
        "\n\n"
        "def london_agent_main(prob: Dict) -> List[Dict]:\n"
        "    query = prob.get('query', '')\n"
        "    dialog: List[Dict] = []\n"
        "    try:\n"
        "        ldn_ProxyUtil.arm_deadline()\n"
        "        agent = ldn_Agent(query)\n"
        "        agent.run()\n"
        "        dialog = agent.get_dialog()\n"
        "    except Exception as e:\n"
        "        dialog = [create_dialogue_step(think=f'Unhandled agent error: {type(e).__name__}: {e}', tool_results=[], response='Done.', query=query, step=1)]\n"
        "    return dialog\n"
    )


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_consensus.py; shop + voucher: ag_london.py
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


        def _install_consensus_product_tools() -> None:
            register_tool("find_product", con_find_product)
            register_tool("view_product_information", con_view_product_information)
            register_tool("recommend_product", con_recommend_product)
            register_tool("terminate", con_terminate)


        def _install_london_shop_tools() -> None:
            register_tool("find_product", ldn_find_product)
            register_tool("view_product_information", ldn_view_product_information)
            register_tool("recommend_product", ldn_recommend_product)
            register_tool("terminate", ldn_terminate)


        def _install_london_voucher_tools() -> None:
            register_tool("find_product", ldn_find_product)
            register_tool("view_product_information", ldn_view_product_information)
            register_tool("recommend_product", ldn_recommend_product)
            register_tool("terminate", ldn_terminate)


        def _consensus_product_run(problem_data: dict) -> list[dict]:
            _orig = con_Agent.__identify_challenge__

            def _product_only(self: con_Agent) -> con_Challenge:
                return con_Challenge.PRODUCT

            con_Agent.__identify_challenge__ = _product_only
            try:
                return consensus_product_agent_main(problem_data)
            finally:
                con_Agent.__identify_challenge__ = _orig


        def _london_shop_run(problem_data: dict) -> list[dict]:
            _orig = ldn_Agent.__identify_challenge__

            def _shop_only(self: ldn_Agent) -> ldn_Challenge:
                return ldn_Challenge.SHOP

            ldn_Agent.__identify_challenge__ = _shop_only
            try:
                return london_agent_main(problem_data)
            finally:
                ldn_Agent.__identify_challenge__ = _orig


        def _london_voucher_run(problem_data: dict) -> list[dict]:
            _orig = ldn_Agent.__identify_challenge__

            def _voucher_only(self: ldn_Agent) -> ldn_Challenge:
                return ldn_Challenge.VOUCHER

            ldn_Agent.__identify_challenge__ = _voucher_only
            try:
                return london_agent_main(problem_data)
            finally:
                ldn_Agent.__identify_challenge__ = _orig


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_identify_challenge(query)
            if kind == "product":
                _install_consensus_product_tools()
                return _consensus_product_run(problem_data)
            if kind == "shop":
                _install_london_shop_tools()
                return _london_shop_run(problem_data)
            _install_london_voucher_tools()
            return _london_voucher_run(problem_data)
        '''
    ).strip("\n") + "\n"


def _verify_combined(combined: str) -> None:
    banned_imports = ("base64", "binascii", "codecs", "zlib")
    for mod in banned_imports:
        if re.search(rf"^\s*(?:import\s+{mod}|from\s+{mod}\s+import)", combined, re.M):
            raise RuntimeError(f"forbidden import detected: {mod}")

    dead_tokens = (
        "RegexQueryParseCatalog",
        "AgentRunner",
        "EnhancementBenchSettings",
        "EdgeCaseProcessor",
        "OptionalEnhancementKit",
        "TaskClassifier",
        "ldn_ProductEngine",
        "stl_",
        "import ag_london",
        "import ag_consensus",
        "import ag_sentinel",
        "con__knapsack_states",
        "eval(",
        "exec(",
        "__import__(",
    )
    for token in dead_tokens:
        if token in combined:
            raise RuntimeError(f"dead or forbidden token still present: {token!r}")

    if "def consensus_product_agent_main(" not in combined:
        raise RuntimeError("consensus_product_agent_main missing")
    if "def london_agent_main(" not in combined:
        raise RuntimeError("london_agent_main missing")
    if "ldn_ProductEngine" in combined:
        raise RuntimeError("london ProductEngine should be excluded")
    if "con_ShopEngine" in combined or "con_VoucherEngine" in combined or "con_KnapsackEngine" in combined:
        raise RuntimeError("consensus shop/voucher/knapsack engines should be excluded")

    suite_phrases = (
        "blue button flex replacement for Oppo A5S",
        "button flex for Oppo A5S",
        "priced from 180 to 505 PHP",
        "priced above 410 PHP",
        "cost is above 85 PHP",
        "cost over 176 PHP",
    )
    for phrase in suite_phrases:
        if phrase in combined:
            raise RuntimeError(f"suite-specific prompt phrase still present: {phrase!r}")


def main() -> None:
    london_lines = _read_lines(LONDON)
    consensus_lines = _read_lines(CONSENSUS)

    consensus_raw = _sanitize_consensus_prompts(
        _patch_consensus_lane(_strip_leading_imports(_slice_ranges(consensus_lines, CONSENSUS_RANGES)))
    )
    consensus_body = _patch_consensus_after_prefix(_prefix_source(consensus_raw, "con_"))

    london_raw = _patch_london_lane(_strip_leading_imports(_slice_ranges(london_lines, LONDON_RANGES)))
    london_body = _append_london_agent_main(_prefix_source(london_raw, "ldn_"))

    parts = [
        _collect_imports(),
        "# " + "=" * 77 + "\n",
        "# Product lane (ag_consensus.py — con_*)\n",
        "# " + "=" * 77 + "\n\n",
        consensus_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop + voucher lane (ag_london.py — ldn_*)\n",
        "# " + "=" * 77 + "\n\n",
        london_body,
        "\n",
        _build_router(),
    ]
    combined = "".join(parts)
    ast.parse(combined)
    _verify_combined(combined)
    OUT.write_text(combined, encoding="utf-8")
    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    size = OUT.stat().st_size
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
