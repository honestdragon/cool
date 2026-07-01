#!/usr/bin/env python3
"""Build my_08.py: ag_writing product (W_); ag_miso shop (S_) + voucher (V_) embedded."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITING = ROOT / "ag_writing.py"
MISO = ROOT / "ag_miso.py"
OUT = ROOT / "my_08.py"

# ag_writing.py — monolith product lane (HostedEvaluatorBootstrap entry)
WRITING_PRODUCT = (2195, 6163)

# ag_miso.py — minimal p_* helpers referenced by shop lane
MISO_SHOP_HELPERS = [
    (17, 18),
    (84, 85),
    (587, 655),
    (850, 1000),
    (1074, 1081),
    (1390, 1441),
]

# ag_miso.py — S_ shop lane
MISO_SHOP = (2148, 5058)

# ag_miso.py — V_ voucher lane (through tool defs, before agent_main)
MISO_VOUCHER = (5059, 8688)

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
        "get_origin",
        "get_args",
        "ThreadPoolExecutor",
        "as_completed",
        "cartesian_product",
        "fields",
        "is_dataclass",
    }
)


class PrefixRenamer(ast.NodeTransformer):
    def __init__(self, rename_map: dict[str, str]) -> None:
        self.rename_map = rename_map
        self._scope_depth = 0

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.rename_map:
            return ast.copy_location(ast.Name(id=self.rename_map[node.id], ctx=node.ctx), node)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if self._scope_depth == 0 and node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        if self._scope_depth == 0 and node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        if self._scope_depth == 0 and node.name in self.rename_map:
            node.name = self.rename_map[node.name]
        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1
        return node

    def visit_Global(self, node: ast.Global) -> ast.AST:
        node.names = [self.rename_map.get(name, name) for name in node.names]
        return node


def _slice_lines(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[start - 1 : end])


def _slice_ranges(path: Path, ranges: list[tuple[int, int]]) -> str:
    return "".join(_slice_lines(path, start, end) for start, end in ranges)


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
        '"""Combined ORO agent: product from ag_writing.py; shop + voucher from ag_miso.py."""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "import logging\n"
        "import math\n"
        "import re\n"
        "import time\n"
        "import threading\n"
        "import dataclasses\n"
        "import unicodedata\n"
        "from bisect import bisect_left, bisect_right\n"
        "from collections import defaultdict, deque\n"
        "from collections.abc import Callable, Sequence\n"
        "from concurrent.futures import ThreadPoolExecutor, as_completed\n"
        "from dataclasses import dataclass, field, fields, is_dataclass\n"
        "from enum import Enum\n"
        "from functools import wraps\n"
        "from itertools import product as cartesian_product\n"
        "from os import getenv\n"
        "import types\n"
        "from types import NoneType\n"
        "from typing import Any, Callable, ClassVar, NamedTuple, Optional, List, Dict, Tuple, Union, Type, TypeVar, get_origin, get_args\n"
        "from typing import NamedTuple as _NamedTuple\n"
        "from urllib.parse import quote_plus\n"
        "from src.agent.proxy_client import ProxyClient\n"
        "from src.agent.agent_interface import Tool, create_dialogue_step, execute_tool_call, register_tool, generate_tool_call_id\n\n"
    )


def _patch_miso_shop(source: str) -> str:
    old = (
        "        task_type = s__AgentCore._route_task_kind(ctx.query)\n"
        "        params = self._coordinator.resolve(ctx.query, task_type)"
    )
    new = (
        "        task_type = 'shop'\n"
        "        params = self._coordinator.resolve(ctx.query, task_type)"
    )
    if old not in source:
        raise RuntimeError("miso shop execute patch target not found")
    return source.replace(old, new, 1)


def _patch_miso_voucher(source: str) -> str:
    old = "    task_type = classify_shopping_task_kind_from_query(ctx.query)\n"
    new = "    task_type = 'voucher'\n"
    if old not in source:
        raise RuntimeError("miso voucher session patch target not found")
    return source.replace(old, new, 1)


WRITING_PRODUCT_ROOTS = frozenset(
    {
        "HostedEvaluatorBootstrap",
        "ValidatedEvaluatorRunCoordinator",
        "run",
        "_d9_find_product",
        "calculate_voucher",
        "_d9_recommend_product",
        "_d9_terminate",
    }
)

WRITING_PRODUCT_ENTRY_METHODS = frozenset(
    {
        ("HostedEvaluatorBootstrap", "reset_session_state"),
        ("HostedEvaluatorBootstrap", "log_start"),
        ("HostedEvaluatorBootstrap", "run_problem"),
        ("ValidatedEvaluatorRunCoordinator", "execute"),
        ("ValidatedEvaluatorRunCoordinator", "_validate_payload"),
    }
)


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


def _trim_writing_product(source: str) -> str:
    """Drop shop/voucher-only monolith code unreachable from the product entry path."""
    tree = ast.parse(source)
    module_funcs: dict[str, ast.FunctionDef] = {}
    module_classes: dict[str, ast.ClassDef] = {}
    class_methods: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    nested_classes: dict[tuple[str, str], ast.ClassDef] = {}
    aliases: dict[str, tuple[str, str]] = {}
    func_aliases: dict[str, str] = {}

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
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
                    aliases[target.id] = (node.value.value.id, node.value.attr)
                elif isinstance(node.value, ast.Name):
                    func_aliases[target.id] = node.value.id

    reachable: set[str] = set(WRITING_PRODUCT_ROOTS)
    reachable_methods: set[tuple[str, str]] = set(WRITING_PRODUCT_ENTRY_METHODS)
    reachable_nested: set[tuple[str, str]] = set()
    instance_vars: dict[str, str] = {}
    for cls_name, _meth in WRITING_PRODUCT_ENTRY_METHODS:
        reachable.add(cls_name)

    def _record_instance_var(var_name: str, cls_name: str) -> None:
        instance_vars[var_name] = cls_name
        reachable.add(cls_name)

    def _resolve_instance_method(var_name: str, meth_name: str) -> None:
        cls_name = instance_vars.get(var_name)
        if cls_name is None:
            return
        reachable.add(cls_name)
        reachable_methods.add((cls_name, meth_name))

    def _follow_alias(name: str) -> None:
        if name in aliases:
            cls_name, meth_name = aliases[name]
            reachable.add(cls_name)
            reachable.add(meth_name)
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
            owner, meth = pair
            if owner in instance_vars and meth != "__init__":
                _resolve_instance_method(owner, meth)
                continue
            if pair in nested_classes:
                reachable_nested.add(pair)
                reachable.add(pair[0])
            elif pair in class_methods:
                reachable_methods.add(pair)
                reachable.add(pair[0])

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
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                cls_name = value.func.id
                for target in targets:
                    _record_instance_var(target, cls_name)
            _absorb_refs(value)
        after = (frozenset(reachable), frozenset(reachable_methods), frozenset(reachable_nested))
        if after == before:
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


def _patch_writing_product(source: str) -> str:
    old_execute = (
        "        task_type = _monolith_route_task_kind(ctx.query)\n"
        "        params = _monolith_llm_param_snapshot(ctx.query, task_type)"
    )
    new_execute = (
        "        task_type = 'product'\n"
        "        params = _monolith_llm_param_snapshot(ctx.query, task_type)"
    )
    if old_execute not in source:
        raise RuntimeError("writing product execute_session_core patch target not found")
    source = source.replace(old_execute, new_execute, 1)

    old_dispatch = (
        "    def dispatch_task(ctx: 'SandboxEvaluatorTransientStateBag', task_type: str, params: dict) -> None:\n"
        "        specs_raw = params.get('products', [])\n"
        "        specs_n = len(specs_raw) if isinstance(specs_raw, list) else -1\n"
        "        _simple_routes = {'shop': _monolith_run_same_shop_multi_product_flow, 'product': _monolith_browse_single_requested_product_flow}\n"
        "        if task_type == 'voucher':\n"
        "            _monolith_route_shop_or_platform_voucher(ctx, params)\n"
        "            return\n"
        "        runner = _simple_routes.get(task_type, _monolith_browse_single_requested_product_flow)\n"
        "        if runner is _monolith_browse_single_requested_product_flow and task_type not in _simple_routes:\n"
        "            pass\n"
        "        runner(ctx, params)"
    )
    new_dispatch = (
        "    def dispatch_task(ctx: 'SandboxEvaluatorTransientStateBag', task_type: str, params: dict) -> None:\n"
        "        _monolith_browse_single_requested_product_flow(ctx, params)"
    )
    if old_dispatch not in source:
        raise RuntimeError("writing product dispatch_task patch target not found")
    source = source.replace(old_dispatch, new_dispatch, 1)

    return source


def _patch_time_deadlines(source: str) -> str:
    replacements = (
        ("session_timeout_sec: float = 250.0", "session_timeout_sec: float = 235.0"),
        ("product_probe_elapsed_max: float = 220.0", "product_probe_elapsed_max: float = 205.0"),
        ("product_finalise_elapsed_max: float = 250.0", "product_finalise_elapsed_max: float = 235.0"),
        ("SESSION_TIMEOUT_SEC: float = 250.0", "SESSION_TIMEOUT_SEC: float = 235.0"),
        ("PRODUCT_PROBE_ELAPSED_MAX: float = 220.0", "PRODUCT_PROBE_ELAPSED_MAX: float = 205.0"),
        ("PRODUCT_FINALISE_ELAPSED_MAX: float = 250.0", "PRODUCT_FINALISE_ELAPSED_MAX: float = 235.0"),
        ("DIALOGUE_SESSION_TIMEOUT_SECONDS = 250.0", "DIALOGUE_SESSION_TIMEOUT_SECONDS = 235.0"),
        ("SINGLE_PRODUCT_PROBE_MAX_ELAPSED_SECONDS = 220.0", "SINGLE_PRODUCT_PROBE_MAX_ELAPSED_SECONDS = 205.0"),
        ("SINGLE_PRODUCT_FINALIZE_MAX_ELAPSED_SECONDS = 250.0", "SINGLE_PRODUCT_FINALIZE_MAX_ELAPSED_SECONDS = 235.0"),
    )
    for old, new in replacements:
        if old in source:
            source = source.replace(old, new, 1)
    return source


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_writing.py; shop + voucher: ag_miso.py
        # =============================================================================

        import re as _combo_re

        _COMBO_RX_SHOP = _combo_re.compile(
            r"\\b(both|these|offering|offers|sells|same|together|along\\s+with)\\b",
            _combo_re.I,
        )
        _COMBO_RX_MULTI = _combo_re.compile(
            r"(?:,?\\s*and\\s+also\\s+|,?\\s*also,?\\s*|Second(?:ly)?,\\s*|Third(?:ly)?,\\s*|"
            r"First,\\s*|\\(\\d+\\)\\s*|\\d+\\.\\s*|Additionally,\\s*|Furthermore,\\s*|"
            r"Moreover,\\s*|In\\s+addition,?\\s*|Plus,\\s*|On\\s+top\\s+of\\s+that,?\\s*|"
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


        @Tool
        def W_find_product(q: str, page: int=1, shop_id: str | None=None, price: str | None=None, sort: str | None=None, service: str | None=None) -> list[dict]:
            return W__d9_find_product(q=q, page=page, shop_id=shop_id, price=price, sort=sort, service=service)


        @Tool
        def W_calculate_voucher(product_prices: str, voucher_type: str, discount_value: float, threshold: float, budget: float, cap: float=0) -> dict:
            return W_calculate_voucher_impl(product_prices, voucher_type, discount_value, threshold, budget, cap=cap)


        @Tool
        def W_recommend_product(product_ids: str) -> str:
            return W__d9_recommend_product(product_ids)


        @Tool
        def W_terminate(status: str='success') -> str:
            return W__d9_terminate(status)


        def _install_writing_product_tools() -> None:
            register_tool("find_product", W_find_product)
            register_tool("calculate_voucher", W_calculate_voucher)
            register_tool("recommend_product", W_recommend_product)
            register_tool("terminate", W_terminate)


        def _install_miso_shop_tools() -> None:
            register_tool("find_product", s_find_product)
            register_tool("calculate_voucher", s_calculate_voucher)
            register_tool("recommend_product", s_recommend_product)
            register_tool("terminate", s_terminate)


        def _install_miso_voucher_tools() -> None:
            register_tool("find_product", V_find_product)
            register_tool("calculate_voucher", V_calculate_voucher)
            register_tool("recommend_product", V_recommend_product)
            register_tool("terminate", V_terminate)


        def writing_product_agent_main(problem_data: dict) -> list[dict]:
            W_HostedEvaluatorBootstrap.reset_session_state()
            W_HostedEvaluatorBootstrap.log_start(problem_data)
            return W_HostedEvaluatorBootstrap.run_problem(problem_data)


        def miso_shop_agent_main(problem_data: dict) -> list[dict]:
            return s_AxiomSessionLauncher().launch(problem_data)


        def miso_voucher_agent_main(problem_data: dict) -> list[dict]:
            ctx = V_DialogueRunContext()
            return V_execute_shopping_dialogue_pipeline(ctx, problem_data)


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_route(query)
            if kind == "product":
                _install_writing_product_tools()
                return writing_product_agent_main(problem_data)
            if kind == "shop":
                _install_miso_shop_tools()
                return miso_shop_agent_main(problem_data)
            _install_miso_voucher_tools()
            return miso_voucher_agent_main(problem_data)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    shop_helpers = _strip_leading_imports(_slice_ranges(MISO, MISO_SHOP_HELPERS))
    shop_body = _patch_miso_shop(_strip_leading_imports(_slice_lines(MISO, *MISO_SHOP)))
    voucher_body = _prefix_source(_patch_miso_voucher(_strip_leading_imports(_slice_lines(MISO, *MISO_VOUCHER))), "V_")
    writing_raw = _patch_writing_product(_strip_leading_imports(_slice_lines(WRITING, *WRITING_PRODUCT)))
    writing_raw = _trim_writing_product(writing_raw)
    compile(writing_raw, "writing_product_trimmed.py", "exec")
    writing_body = _prefix_source(writing_raw, "W_")
    # calculate_voucher was prefixed to W_calculate_voucher — expose impl alias for router tool
    writing_body = writing_body.replace(
        "def W_calculate_voucher(product_prices: str, voucher_type: str, discount_value: float, threshold: float, budget: float, cap: float=0) -> dict:",
        "def W_calculate_voucher_impl(product_prices: str, voucher_type: str, discount_value: float, threshold: float, budget: float, cap: float=0) -> dict:",
        1,
    )

    parts = [
        _collect_imports(),
        "# " + "=" * 77 + "\n",
        "# Shop helpers (ag_miso.py p_* deps for S_ lane)\n",
        "# " + "=" * 77 + "\n\n",
        shop_helpers,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop lane (ag_miso.py — S_*)\n",
        "# " + "=" * 77 + "\n\n",
        shop_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Voucher lane (ag_miso.py — V_*)\n",
        "# " + "=" * 77 + "\n\n",
        voucher_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Product lane (ag_writing.py — W_* monolith pipeline)\n",
        "# " + "=" * 77 + "\n\n",
        writing_body,
        "\n\n",
        _build_router(),
    ]
    combined = "".join(parts)
    combined = _patch_time_deadlines(combined)
    OUT.write_text(combined, encoding="utf-8")
    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    size = OUT.stat().st_size
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
