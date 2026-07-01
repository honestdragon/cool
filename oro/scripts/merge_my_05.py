#!/usr/bin/env python3
"""Build my_05.py: product from ag_shichen.py; shop from ag_tope.py; voucher from ag_a2.py."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHICHEN = ROOT / "ag_shichen.py"
TOPE = ROOT / "ag_tope.py"
A2 = ROOT / "ag_a2.py"
OUT = ROOT / "my_05.py"

SHICHEN_END = 4425
TOPE_END = 3488
A2_END = 3355

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
        "islice",
        "ProxyClient",
        "create_dialogue_step",
        "execute_tool_call",
        "register_tool",
        "generate_tool_call_id",
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
        "Set",
        "get_origin",
        "get_args",
        "ThreadPoolExecutor",
        "as_completed",
        "fields",
        "is_dataclass",
        "logger",
        "lru_cache",
    }
)

SHICHEN_PRODUCT_ROOTS = frozenset(
    {
        "Agent",
        "find_product",
        "view_product_information",
        "recommend_product",
        "terminate",
        "Prompt",
        "Challenge",
        "Vocab",
        "JsonModel",
        "SubQuery",
        "VoucherOption",
        "QueryInfo",
        "SearchOption",
        "Product",
        "Weight",
        "MiscUtil",
        "ProxyUtil",
        "EngineUtil",
        "SegTree",
        "Debugger",
        "Session",
        "BaseEngine",
        "ProductEngine",
    }
)

SHICHEN_ENTRY_METHODS = frozenset(
    {
        ("Agent", "run"),
        ("Agent", "get_dialog"),
        ("Agent", "__init__"),
        ("ProductEngine", "run"),
    }
)

TOPE_SHOP_ROOTS = frozenset(
    {
        "Agent",
        "find_product",
        "view_product_information",
        "recommend_product",
        "terminate",
        "Prompt",
        "Challenge",
        "Vocab",
        "JsonModel",
        "SubQuery",
        "VoucherOption",
        "QueryInfo",
        "SearchOption",
        "Product",
        "Weight",
        "MiscUtil",
        "ProxyUtil",
        "EngineUtil",
        "SegTree",
        "Debugger",
        "Session",
        "BaseEngine",
        "KnapsackEngine",
        "ShopEngine",
    }
)

TOPE_ENTRY_METHODS = frozenset(
    {
        ("Agent", "run"),
        ("Agent", "get_dialog"),
        ("Agent", "__init__"),
        ("ShopEngine", "run"),
    }
)

A2_VOUCHER_ROOTS = frozenset(
    {
        "Agent",
        "find_product",
        "view_product_information",
        "calculate_voucher",
        "recommend_product",
        "terminate",
        "Prompt",
        "Challenge",
        "Vocab",
        "JsonModel",
        "SubQuery",
        "VoucherOption",
        "QueryInfo",
        "SearchOption",
        "Product",
        "Weight",
        "MiscUtil",
        "ProxyUtil",
        "EngineUtil",
        "SegTree",
        "Session",
        "BaseEngine",
        "KnapsackEngine",
        "VoucherEngine",
        "_try_regex_fast_decompose",
        "_try_regex_multi_decompose",
        "_build_regex_decompose_item",
        "_extract_price_range_from_query",
        "_build_regex_hints_from_query",
        "_service_tags_from_query",
        "_multi_spec_slice_count",
        "_RX_BUDGET_ANCHOR",
        "_RX_SAME_STORE",
        "_RX_MULTI_SPLIT",
        "_QUERY_PARSE_STOPWORDS",
        "_pipeline_start",
        "_log_pipeline_event",
        "_PipelinePhaseTimer",
        "_ModelProvider",
        "_RateLimiter",
        "_budget_sec_left",
        "_respect_tool_gap",
        "_stamp_tool_call",
        "_tool_retry_sleep",
        "_rate_limited_search_get",
        "_execute_find_product_with_service_fallback",
        "_voucher_totals_from_prices",
        "_pipeline_elapsed_sec",
        "_may_run_product_probe",
        "_build_hyde_probe_query",
        "_ground_term_in_product",
        "_alt_int_ref",
        "_logger",
        "_CHUTES_MODELS",
        "_OPENROUTER_MODELS",
        "_MODEL_REGISTRY",
    }
)

A2_ENTRY_METHODS = frozenset(
    {
        ("Agent", "run"),
        ("Agent", "get_dialog"),
        ("Agent", "__init__"),
        ("VoucherEngine", "run"),
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

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if node.attr in self.rename_map:
            node.attr = self.rename_map[node.attr]
        return node

    def visit_Global(self, node: ast.Global) -> ast.AST:
        node.names = [self.rename_map.get(name, name) for name in node.names]
        return node


def _slice_lines(path: Path, end_line: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[:end_line])


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


def _strip_tool_decorators(source: str) -> str:
    return re.sub(r"^@Tool\n", "", source, flags=re.MULTILINE)


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


def _collect_load_refs(node: ast.AST) -> set[str]:
    refs: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            refs.add(sub.id)
    return refs


def _collect_self_method_refs(node: ast.AST, owner: str) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.ctx, ast.Load)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
        ):
            refs.add((owner, sub.attr))
    return refs


def _collect_ctor_and_class_attr_refs(node: ast.AST) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            refs.add((sub.func.id, "__init__"))
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if isinstance(sub.func.value, ast.Name):
                refs.add((sub.func.value.id, sub.func.attr))
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            refs.add((sub.value.id, sub.attr))
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Call):
            call = sub.value
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                owner = call.func.value.id
                refs.add((owner, call.func.attr))
                refs.add((owner, sub.attr))
            elif isinstance(call.func, ast.Name):
                refs.add((call.func.id, sub.attr))
    return refs


def _expand_reachable_class_methods(
    reachable: set[str],
    reachable_methods: set[tuple[str, str]],
    class_methods: dict[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]],
    module_funcs: dict[str, ast.FunctionDef],
    absorb_refs,
) -> None:
    for cls_name in reachable:
        for owner, meth in class_methods:
            if owner == cls_name:
                reachable_methods.add((owner, meth))
    while True:
        before = frozenset(reachable)
        for owner, meth in reachable_methods:
            node = class_methods.get((owner, meth))
            if node is not None:
                absorb_refs(node)
        for name in list(reachable):
            node = module_funcs.get(name)
            if node is not None:
                absorb_refs(node)
        if frozenset(reachable) == before:
            break


def _trim_lane(
    source: str,
    roots: frozenset[str],
    entry_methods: frozenset[tuple[str, str]],
) -> str:
    tree = ast.parse(source)
    module_funcs: dict[str, ast.FunctionDef] = {}
    module_classes: dict[str, ast.ClassDef] = {}
    class_methods: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    nested_classes: dict[tuple[str, str], ast.ClassDef] = {}
    aliases: dict[str, tuple[str, str]] = {}
    instance_of: dict[str, str] = {}

    def _index_class(cls_node: ast.ClassDef, owner: str | None = None) -> None:
        key_owner = owner or cls_node.name
        if owner is None:
            module_classes[cls_node.name] = cls_node
        else:
            nested_classes[(owner, cls_node.name)] = cls_node
        for item in cls_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                class_methods[(key_owner, item.name)] = item
            elif isinstance(item, ast.ClassDef):
                _index_class(item, key_owner)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            module_funcs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            _index_class(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
                    aliases[target.id] = (node.value.value.id, node.value.attr)
                elif (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                ):
                    instance_of[target.id] = node.value.func.id

    reachable: set[str] = set(roots)
    reachable_methods: set[tuple[str, str]] = set(entry_methods)
    reachable_nested: set[tuple[str, str]] = set()
    for cls_name, _meth in entry_methods:
        reachable.add(cls_name)

    def _follow_alias(name: str) -> None:
        if name in aliases:
            cls_name, meth_name = aliases[name]
            reachable.add(cls_name)
            reachable_methods.add((cls_name, meth_name))

    def _nodes_for_name(name: str) -> list[ast.AST]:
        if name in module_funcs:
            return [module_funcs[name]]
        return []

    def _assign_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
        if isinstance(node, ast.Assign):
            return [t.id for t in node.targets if isinstance(t, ast.Name)]
        if isinstance(node.target, ast.Name):
            return [node.target.id]
        return []

    def _absorb_refs(node: ast.AST) -> None:
        for ref in _collect_load_refs(node):
            reachable.add(ref)
            _follow_alias(ref)
        for pair in _collect_ctor_and_class_attr_refs(node):
            if pair in nested_classes:
                reachable_nested.add(pair)
                reachable.add(pair[0])
            elif pair in class_methods:
                reachable_methods.add(pair)
                reachable.add(pair[0])
            elif pair[0] in instance_of:
                cls_name = instance_of[pair[0]]
                meth_pair = (cls_name, pair[1])
                if meth_pair in class_methods:
                    reachable_methods.add(meth_pair)
                    reachable.add(cls_name)
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.ctx, ast.Load)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in instance_of
            ):
                cls_name = instance_of[sub.value.id]
                meth_pair = (cls_name, sub.attr)
                if meth_pair in class_methods:
                    reachable_methods.add(meth_pair)
                    reachable.add(cls_name)

    while True:
        before = (frozenset(reachable), frozenset(reachable_methods), frozenset(reachable_nested))
        for name in list(reachable):
            _follow_alias(name)
            for node in _nodes_for_name(name):
                _absorb_refs(node)
        for owner, meth in list(reachable_methods):
            node = class_methods.get((owner, meth))
            if node is None:
                continue
            _absorb_refs(node)
            for self_owner, self_meth in _collect_self_method_refs(node, owner):
                if (self_owner, self_meth) in class_methods:
                    reachable_methods.add((self_owner, self_meth))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = _assign_targets(node)
            if not targets or not all(t in reachable for t in targets):
                continue
            value = node.value if isinstance(node, ast.AnnAssign) else node.value
            _absorb_refs(value)
        after = (frozenset(reachable), frozenset(reachable_methods), frozenset(reachable_nested))
        if after == before:
            break

    _expand_reachable_class_methods(
        reachable, reachable_methods, class_methods, module_funcs, _absorb_refs
    )

    while True:
        before = frozenset(reachable)
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            names = _assign_targets(node)
            if names and all(n in reachable for n in names) and node.value is not None:
                _absorb_refs(node.value)
        for cls_name in list(reachable):
            cls = module_classes.get(cls_name)
            if cls is None:
                continue
            for item in cls.body:
                if isinstance(item, (ast.Assign, ast.AnnAssign)):
                    if item.value is not None:
                        _absorb_refs(item.value)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (cls_name, item.name) in reachable_methods:
                        _absorb_refs(item)
        if frozenset(reachable) == before:
            break

    def _keep_class_body(cls_node: ast.ClassDef, owner: str) -> list[ast.stmt]:
        kept: list[ast.stmt] = []
        for item in cls_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (owner, item.name) in reachable_methods:
                    kept.append(item)
            elif isinstance(item, ast.ClassDef):
                if (owner, item.name) in reachable_nested:
                    filtered = ast.ClassDef(
                        name=item.name,
                        bases=item.bases,
                        keywords=item.keywords,
                        body=_keep_class_body(item, item.name),
                        decorator_list=item.decorator_list,
                    )
                    ast.copy_location(filtered, item)
                    if not filtered.body:
                        filtered.body = [ast.Pass()]
                    kept.append(filtered)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                kept.append(item)
            elif isinstance(item, (ast.Pass, ast.Expr)):
                kept.append(item)
        return kept

    new_body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name in reachable:
                new_body.append(node)
        elif isinstance(node, ast.ClassDef):
            if node.name not in reachable:
                continue
            filtered = ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                body=_keep_class_body(node, node.name),
                decorator_list=node.decorator_list,
            )
            ast.copy_location(filtered, node)
            if not filtered.body and node.body:
                filtered.body = list(node.body)
            if filtered.body:
                new_body.append(filtered)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assign_targets(node)
            if names and all(n in reachable for n in names):
                new_body.append(node)
        elif isinstance(node, ast.Expr):
            continue

    trimmed = ast.Module(body=new_body, type_ignores=[])
    ast.fix_missing_locations(trimmed)
    return ast.unparse(trimmed) + "\n"


def _patch_engine_map(source: str, keep: str) -> str:
    old = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }"
    )
    mapping = {
        "PRODUCT": "        Challenge.PRODUCT: ProductEngine\n",
        "SHOP": "        Challenge.SHOP: ShopEngine\n",
        "VOUCHER": "        Challenge.VOUCHER: VoucherEngine\n",
    }
    new = f"    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {{\n{mapping[keep]}    }}"
    if old not in source:
        alt = (
            "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = "
            "{Challenge.PRODUCT: ProductEngine, Challenge.SHOP: ShopEngine, Challenge.VOUCHER: VoucherEngine}"
        )
        if keep == "PRODUCT":
            rep = "{Challenge.PRODUCT: ProductEngine}"
        elif keep == "SHOP":
            rep = "{Challenge.SHOP: ShopEngine}"
        else:
            rep = "{Challenge.VOUCHER: VoucherEngine}"
        new_one = f"    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {rep}"
        if alt not in source:
            raise RuntimeError(f"ENGINE_MAP patch target not found for {keep}")
        return source.replace(alt, new_one, 1)
    return source.replace(old, new, 1)


def _patch_agent_init_shichen(source: str) -> str:
    old = "    def __init__(self, query: str, debugger: Optional[Debugger]=None) -> None:"
    if old not in source:
        raise RuntimeError("shichen agent __init__ patch target not found")
    source = source.replace(
        old,
        "    def __init__(self, query: str, debugger: Optional[Debugger]=None, forced_challenge: Optional[Challenge]=None) -> None:",
        1,
    )
    needle = "        self.query = query\n        self.sess: Session = Session(query, self.debugger)"
    if needle not in source:
        raise RuntimeError("shichen agent __init__ body patch target not found")
    return source.replace(
        needle,
        "        self.query = query\n        self._forced_challenge = forced_challenge\n        self.sess: Session = Session(query, self.debugger)",
        1,
    )


def _patch_agent_init_tope(source: str) -> str:
    old = "    def __init__(self, query: str, debugger: Optional[Debugger]=None) -> None:"
    if old not in source:
        raise RuntimeError("tope agent __init__ patch target not found")
    source = source.replace(
        old,
        "    def __init__(self, query: str, debugger: Optional[Debugger]=None, forced_challenge: Optional[Challenge]=None) -> None:",
        1,
    )
    needle = "        self.query = query\n        self.sess: Session = Session(query, self.debugger)"
    if needle not in source:
        raise RuntimeError("tope agent __init__ body patch target not found")
    return source.replace(
        needle,
        "        self.query = query\n        self._forced_challenge = forced_challenge\n        self.sess: Session = Session(query, self.debugger)",
        1,
    )


def _patch_agent_init_a2(source: str) -> str:
    old = "    def __init__(self, query: str) -> None:\n        self.query = query"
    if old not in source:
        raise RuntimeError("a2 agent __init__ patch target not found")
    return source.replace(
        old,
        "    def __init__(self, query: str, forced_challenge: Optional[Challenge]=None) -> None:\n        self.query = query\n        self._forced_challenge = forced_challenge",
        1,
    )


def _patch_classify(source: str, sniff_name: str) -> str:
    patterns = [
        f"challenge = self.{sniff_name}()",
        f"            challenge = self.{sniff_name}()",
    ]
    for old in patterns:
        if old in source:
            indent = old.split("challenge")[0]
            new = f"{indent}challenge = self._forced_challenge if self._forced_challenge is not None else self.{sniff_name}()"
            return source.replace(old, new, 1)
    raise RuntimeError(f"classify patch target not found: {sniff_name}")


def _collect_imports() -> str:
    return (
        '"""Combined ORO agent: product from ag_shichen.py; shop from ag_tope.py; voucher from ag_a2.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import logging\n"
        "import math\n"
        "import re\n"
        "import time\n"
        "import threading\n"
        "import dataclasses\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict, deque\n"
        "from collections.abc import Callable, Sequence\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from functools import lru_cache, wraps\n"
        "from itertools import islice, product\n"
        "from os import getenv\n"
        "from types import NoneType\n"
        "from typing import Any, Callable, ClassVar, NamedTuple, Optional, List, Dict, Set, Tuple, Union, Type, TypeGuard, get_origin, get_args\n"
        "from typing import NamedTuple as _NamedTuple\n"
        "from urllib.parse import quote_plus\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool\n\n"
    )


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_shichen.py; shop: ag_tope.py; voucher: ag_a2.py
        # =============================================================================

        import re as _combo_re

        _COMBO_RX_SHOP = _combo_re.compile(
            r"\\b(both|these|offering|offers|sells|same|together|along\\s+with)\\b",
            _combo_re.I,
        )
        _COMBO_RX_MULTI = _combo_re.compile(
            r"(?:,?\\s*and\\s+also\\s+|,?\\s*also,?\\s*|Second(?:ly)?,\\s*|Third(?:ly)?,\\s*|"
            r"First,\\s*|\\(\\d+\\)\\s*|\\d+\\.\\s*|Additionally,\\s*|Furthermore,\\s*|"
            r"Moreover,\\s*|In\\s+addition,?\s*|Plus,\\s*|On\\s+top\\s+of\\s+that,?\\s*|"
            r"[.]\\s*Next,\\s*|[.]\\s*Lastly,\\s*|[.]\\s*Finally,\\s*|[.]\\s*Last,\\s*|"
            r"\\bThen\\s*,?\\s*I\\s+(?:need|want|also)\\b|\\bI\\s+also\\s+(?:want|need)\\b)",
            _combo_re.I,
        )


        def _combo_route(query: str) -> str:
            q = (query or "").lower()
            if any(sig in q for sig in ("voucher", "budget", "discount")):
                return "voucher"
            if "shop" in q and (
                _COMBO_RX_SHOP.search(q) is not None
                or _COMBO_RX_MULTI.search(query or "") is not None
            ):
                return "shop"
            return "product"


        def _install_shichen_product_tools() -> None:
            register_tool("find_product", SH_find_product)
            register_tool("view_product_information", SH_view_product_information)
            register_tool("recommend_product", SH_recommend_product)
            register_tool("terminate", SH_terminate)


        def _install_tope_shop_tools() -> None:
            register_tool("find_product", TO_find_product)
            register_tool("view_product_information", TO_view_product_information)
            register_tool("recommend_product", TO_recommend_product)
            register_tool("terminate", TO_terminate)


        def _install_a2_voucher_tools() -> None:
            register_tool("find_product", A2_find_product)
            register_tool("view_product_information", A2_view_product_information)
            register_tool("calculate_voucher", A2_calculate_voucher)
            register_tool("recommend_product", A2_recommend_product)
            register_tool("terminate", A2_terminate)


        def _shichen_product_agent_main(problem_data: dict) -> list[dict]:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list[dict] = []
            try:
                agent = SH_Agent(query, forced_challenge=SH_Challenge.PRODUCT)
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
            return dialog


        def _tope_shop_agent_main(problem_data: dict) -> list[dict]:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list[dict] = []
            try:
                agent = TO_Agent(query, forced_challenge=TO_Challenge.SHOP)
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
            return dialog


        def _a2_voucher_agent_main(problem_data: dict) -> list[dict]:
            global A2__pipeline_start
            A2__pipeline_start = time.monotonic()
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list[dict] = []
            try:
                agent = A2_Agent(query, forced_challenge=A2_Challenge.VOUCHER)
                agent.run()
                dialog = agent.get_dialog()
            except Exception as exc:
                A2__log_pipeline_event("agent_main", "error", error=f"{type(exc).__name__}: {exc}")
                dialog = [
                    create_dialogue_step(
                        think=f"Unhandled agent error: {type(exc).__name__}: {exc}",
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
            kind = _combo_route(query)
            if kind == "product":
                _install_shichen_product_tools()
                return _shichen_product_agent_main(problem_data)
            if kind == "shop":
                _install_tope_shop_tools()
                return _tope_shop_agent_main(problem_data)
            _install_a2_voucher_tools()
            return _a2_voucher_agent_main(problem_data)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    shichen_raw = _strip_tool_decorators(
        _strip_leading_imports(_slice_lines(SHICHEN, SHICHEN_END))
    )
    shichen_raw = _patch_engine_map(shichen_raw, "PRODUCT")
    shichen_raw = _trim_lane(shichen_raw, SHICHEN_PRODUCT_ROOTS, SHICHEN_ENTRY_METHODS)
    shichen_raw = _patch_agent_init_shichen(shichen_raw)
    shichen_raw = _patch_classify(shichen_raw, "__sniff_challenge_kind__")
    compile(shichen_raw, "shichen_product.py", "exec")
    shichen_body = _prefix_source(shichen_raw, "SH_")

    tope_raw = _strip_tool_decorators(
        _strip_leading_imports(_slice_lines(TOPE, TOPE_END))
    )
    tope_raw = _patch_engine_map(tope_raw, "SHOP")
    tope_raw = _trim_lane(tope_raw, TOPE_SHOP_ROOTS, TOPE_ENTRY_METHODS)
    tope_raw = _patch_agent_init_tope(tope_raw)
    tope_raw = _patch_classify(tope_raw, "__identify_challenge__")
    compile(tope_raw, "tope_shop.py", "exec")
    tope_body = _prefix_source(tope_raw, "TO_")

    a2_raw = _strip_leading_imports(_slice_lines(A2, A2_END))
    a2_raw = a2_raw.replace(
        "from src.agent.agent_interface import register_tool as registerSandboxTool\n",
        "",
        1,
    )
    a2_raw = _patch_engine_map(a2_raw, "VOUCHER")
    a2_raw = _trim_lane(a2_raw, A2_VOUCHER_ROOTS, A2_ENTRY_METHODS)
    a2_raw = _patch_agent_init_a2(a2_raw)
    a2_raw = _patch_classify(a2_raw, "__identify_challenge__")
    compile(a2_raw, "a2_voucher.py", "exec")
    a2_body = _prefix_source(a2_raw, "A2_")

    parts = [
        _collect_imports(),
        "# " + "=" * 77 + "\n",
        "# Product lane (ag_shichen.py — SH_*)\n",
        "# " + "=" * 77 + "\n\n",
        shichen_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop lane (ag_tope.py — TO_*)\n",
        "# " + "=" * 77 + "\n\n",
        tope_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Voucher lane (ag_a2.py — A2_*)\n",
        "# " + "=" * 77 + "\n\n",
        a2_body,
        "\n",
        _build_router(),
    ]
    combined = "".join(parts)
    OUT.write_text(combined, encoding="utf-8")

    tree = ast.parse(combined)
    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    from collections import Counter

    dups = [k for k, v in Counter(funcs).items() if v > 1]
    if dups:
        raise RuntimeError(f"duplicate top-level functions in output: {dups}")

    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    size = OUT.stat().st_size
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
