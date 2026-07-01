#!/usr/bin/env python3
"""Build my_dep.py: product from ag_london.py; shop + voucher from ag_penalty.py."""

from __future__ import annotations

import ast
import re
import textwrap
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PENALTY = ROOT / "ag_penalty.py"
LONDON = ROOT / "ag_london.py"
OUT = ROOT / "my_dep.py"

# ag_london.py — product lane only (skip Shop/Voucher engines + dead enhancement helpers)
LONDON_RANGES: list[tuple[int, int]] = [
    (1, 2674),
    (2849, 3263),
    (3918, 3940),
]

# ag_penalty.py — shop/voucher lane only (skip ProductEngine)
PENALTY_RANGES: list[tuple[int, int]] = [
    (1, 9022),
    (9023, 9821),
    (10084, 11123),
    (11124, 12945),
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
        "types",
        "product",
        "ProxyClient",
        "create_dialogue_step",
        "execute_tool_call",
        "register_tool",
        "TypeVar",
        "wraps",
        "ClassVar",
        "Enum",
        "math",
        "unicodedata",
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
        "SimpleNamespace",
        "TypeGuard",
        "generate_tool_call_id",
        "_NamedTuple",
        "_KokoNamedTuple",
        "_cartesian_product",
        "ToolCallResult",
        "NoneType",
        "is_dataclass",
        "bisect_left",
        "bisect_right",
        "PIG_DOUBLE_RED",
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


class CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.attrs: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.attrs.add(node.func.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.attrs.add(node.attr)
        self.generic_visit(node)


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _strip_pig_dead_code(source: str) -> str:
    """Remove PIG_DOUBLE_RED-gated blocks and the constant itself (always 0 at runtime)."""
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


def _slice_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> str:
    picked: list[str] = []
    for start, end in ranges:
        picked.extend(lines[start - 1 : end])
    return "".join(picked)


def _strip_imports(source: str) -> tuple[str, list[str]]:
    tree = ast.parse(source)
    import_lines: list[str] = []
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_lines.append(ast.unparse(node))
            continue
        body.append(node)
    tree.body = body
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), import_lines


def _top_level_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        out: set[str] = set()
        for target in node.targets:
            if isinstance(target, ast.Name):
                out.add(target.id)
        return out
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _class_members(class_node: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(item.name)
    return members


def _prune_unreachable(source: str, entry_points: list[str]) -> str:
    tree = ast.parse(source)
    top_nodes: dict[str, list[ast.stmt]] = defaultdict(list)
    class_members: dict[str, set[str]] = {}

    for node in tree.body:
        for name in _top_level_names(node):
            top_nodes[name].append(node)
        if isinstance(node, ast.ClassDef):
            class_members[node.name] = _class_members(node)

    refs_from: dict[str, set[str]] = {}
    for name, nodes in top_nodes.items():
        cc = CallCollector()
        for node in nodes:
            cc.visit(node)
        refs: set[str] = set()
        for ref in cc.names:
            if ref in top_nodes:
                refs.add(ref)
        for attr in cc.attrs:
            for cls_name, members in class_members.items():
                if attr in members and cls_name in top_nodes:
                    refs.add(cls_name)
        refs_from[name] = refs

    reachable: set[str] = set()
    queue = [ep for ep in entry_points if ep in top_nodes]
    reachable.update(queue)
    while queue:
        cur = queue.pop()
        for nxt in refs_from.get(cur, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    pruned_body = [node for node in tree.body if _top_level_names(node) & reachable]
    tree.body = pruned_body
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _prefix_source(source: str, prefix: str) -> str:
    tree = ast.parse(source)
    defined: set[str] = set()
    imported: set[str] = set()
    for node in tree.body:
        defined.update(_top_level_names(node))
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


def _patch_tool_decorators(text: str) -> str:
    replacements = (
        ("@Tool\ndef find_product", '@Tool("find_product")\ndef find_product'),
        ("@Tool\ndef view_product_information", '@Tool("view_product_information")\ndef view_product_information'),
        ("@Tool\ndef recommend_product", '@Tool("recommend_product")\ndef recommend_product'),
        ("@Tool\ndef terminate", '@Tool("terminate")\ndef terminate'),
    )
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"tool decorator patch target not found: {old!r}")
        text = text.replace(old, new, 1)
    return text


def _patch_penalty_after_prefix(text: str) -> str:
    old = "    def pnl_terminate(status: str='success') -> ToolCallResult:"
    new = "    def terminate(status: str='success') -> ToolCallResult:"
    if old not in text:
        raise RuntimeError("penalty ProxyUtil.terminate patch target not found")
    return text.replace(old, new, 1)


def _patch_london_after_prefix(text: str) -> str:
    old_proxy_terminate = (
        "    def ldn_terminate(status: str='success') -> ToolCallResult:\n"
        "        return execute_tool_call('terminate', {'status': status})"
    )
    new_proxy_terminate = (
        "    def terminate(status: str='success') -> ToolCallResult:\n"
        "        return execute_tool_call('terminate', {'status': status})"
    )
    if old_proxy_terminate not in text:
        raise RuntimeError("london ProxyUtil.terminate patch target not found")
    return text.replace(old_proxy_terminate, new_proxy_terminate, 1)


def _patch_london_engine_map(text: str) -> str:
    old = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }"
    )
    new = "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {Challenge.PRODUCT: ProductEngine}"
    if old not in text:
        raise RuntimeError("london ENGINE_MAP patch target not found")
    return text.replace(old, new, 1)


def _patch_penalty_engine_map(text: str) -> str:
    old = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = "
        "{Challenge.PRODUCT: ProductEngine, Challenge.SHOP: ShopEngine, Challenge.VOUCHER: VoucherEngine}"
    )
    new = (
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = "
        "{Challenge.SHOP: ShopEngine, Challenge.VOUCHER: VoucherEngine}"
    )
    if old not in text:
        raise RuntimeError("penalty ENGINE_MAP patch target not found")
    return text.replace(old, new, 1)


def _collect_imports(*sources: str) -> str:
    import_lines: list[str] = []
    for src in sources:
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                import_lines.append(s)
    typing_names: set[str] = set()
    typing_aliases: list[str] = []
    ordered: list[str] = []
    seen: set[str] = set()
    for line in import_lines:
        if line == "from __future__ import annotations":
            continue
        if line.startswith("from src.agent.agent_interface import"):
            continue
        if line.startswith("from src.agent import proxy_client"):
            continue
        if line.startswith("from src.agent.proxy_client import"):
            continue
        if line.startswith("from src.agent.agent_shared import"):
            continue
        if line.startswith("from typing import"):
            names_part = line.split("import", 1)[1].strip()
            for chunk in names_part.split(","):
                chunk = chunk.strip()
                if " as " in chunk:
                    typing_aliases.append(chunk)
                else:
                    typing_names.add(chunk)
            continue
        if line in seen:
            continue
        if line.startswith("logging.basicConfig"):
            continue
        seen.add(line)
        ordered.append(line)
    for name in (
        "Any",
        "Callable",
        "Dict",
        "List",
        "Optional",
        "Tuple",
        "Type",
        "TypeGuard",
        "TypeVar",
        "Union",
    ):
        typing_names.add(name)
    typing_block = ["from typing import " + ", ".join(sorted(typing_names, key=str.lower))]
    for alias in ("NamedTuple as _NamedTuple", "NamedTuple as _KokoNamedTuple"):
        if alias not in typing_aliases:
            typing_aliases.append(alias)
    typing_block.extend(f"from typing import {alias}" for alias in typing_aliases)
    header = (
        '"""Combined ORO agent: product from ag_london.py; shop + voucher from ag_penalty.py."""\n\n'
    )
    header += "from __future__ import annotations\n"
    iface = (
        "from src.agent.agent_interface import "
        "Tool, ToolCallResult, create_dialogue_step, execute_tool_call, register_tool"
    )
    extras = [
        *typing_block,
        "import threading",
        "from dataclasses import dataclass, field, fields, is_dataclass",
        "from functools import wraps",
    ]
    deduped_extras: list[str] = []
    seen_extra: set[str] = set()
    for line in extras:
        if line not in seen_extra:
            seen_extra.add(line)
            deduped_extras.append(line)
    deduped_ordered: list[str] = []
    seen_ordered: set[str] = set()
    for line in ordered:
        if line.startswith("from dataclasses import"):
            continue
        if line.startswith("from functools import"):
            continue
        if line not in seen_ordered:
            seen_ordered.add(line)
            deduped_ordered.append(line)
    proxy = "from src.agent.proxy_client import ProxyClient\n"
    return header + "\n".join(deduped_ordered + deduped_extras + [proxy, _build_agent_shared_block(), iface]) + "\n\n"


def _build_agent_shared_block() -> str:
    return textwrap.dedent(
        """
        try:
            from src.agent.agent_shared import (
                attach_proxy_calls_to_dialogue as _attach_proxy_calls_to_dialogue,
                patch_request_log_preserve_usage,
                reasoning_events_for_thread,
                proxy_call_get,
                proxy_call_post,
            )
        except Exception:
            _THREAD_LOCAL_PROXY_EVENTS = threading.local()

            def reasoning_events_for_thread() -> list[dict]:
                events = getattr(_THREAD_LOCAL_PROXY_EVENTS, "events", None)
                if isinstance(events, list):
                    return events
                events = []
                setattr(_THREAD_LOCAL_PROXY_EVENTS, "events", events)
                return events

            def patch_request_log_preserve_usage(_: Any) -> None:
                return None

            def _extract_usage_for_fallback(response: Any) -> tuple[int | None, dict | None]:
                if not isinstance(response, dict):
                    return (None, None)
                usage = response.get("usage")
                if not isinstance(usage, dict):
                    return (None, None)
                completion_tokens = usage.get("completion_tokens")
                if not isinstance(completion_tokens, int):
                    completion_tokens = None
                return (completion_tokens, usage)

            def _extract_result_product_ids_for_fallback(path: str, response: Any) -> list[str]:
                if "/search/find_product" not in path or not isinstance(response, list):
                    return []
                out: list[str] = []
                for row in response:
                    if not isinstance(row, dict):
                        continue
                    product_id = row.get("product_id")
                    if product_id:
                        out.append(str(product_id))
                return out

            def _record_proxy_event_for_fallback(
                *,
                kind: str,
                method: str,
                path: str,
                started: float,
                response: Any,
                params: Any = None,
                json_data: Any = None,
            ) -> None:
                completion_tokens, usage = _extract_usage_for_fallback(response)
                now = time.time()
                event: dict[str, Any] = {
                    "kind": kind,
                    "method": method,
                    "path": path,
                    "duration_ms": round((now - started) * 1000, 1),
                    "completion_tokens": completion_tokens,
                    "status_code": 200 if isinstance(response, (dict, list)) else None,
                    "timestamp": int(now * 1000),
                    "t": now,
                }
                if isinstance(params, dict) and params:
                    event["params"] = {k: v for k, v in params.items() if v is not None}
                if isinstance(json_data, dict) and json_data.get("model"):
                    event["json_data"] = {"model": json_data["model"]}
                if usage is not None:
                    event["response"] = {"usage": usage}
                product_ids = _extract_result_product_ids_for_fallback(path, response)
                if product_ids:
                    event["result_product_ids"] = product_ids
                reasoning_events_for_thread().append(event)

            def proxy_call_get(
                inner: Any, kind: str, path: str, params: Any = None, **kw: Any
            ) -> Any:
                started = time.time()
                response = None
                try:
                    response = inner.get(path, params=params, **kw)
                    return response
                finally:
                    _record_proxy_event_for_fallback(
                        kind=kind,
                        method="GET",
                        path=path,
                        started=started,
                        response=response,
                        params=params,
                    )

            def proxy_call_post(
                inner: Any, kind: str, path: str, json_data: Any = None, **kw: Any
            ) -> Any:
                started = time.time()
                response = None
                try:
                    response = inner.post(path, json_data=json_data, **kw)
                    return response
                finally:
                    _record_proxy_event_for_fallback(
                        kind=kind,
                        method="POST",
                        path=path,
                        started=started,
                        response=response,
                        json_data=json_data,
                    )

            _PROXY_CALL_KEYS = (
                "method",
                "path",
                "status_code",
                "duration_ms",
                "timestamp",
                "params",
                "json_data",
                "response",
                "completion_tokens",
                "result_product_ids",
            )

            def _attach_proxy_calls_to_dialogue(steps: Any) -> None:
                if not isinstance(steps, list) or not steps:
                    return
                calls: list[dict] = []
                for event in reasoning_events_for_thread():
                    call = {key: event[key] for key in _PROXY_CALL_KEYS if key in event}
                    if call:
                        calls.append(call)
                if not calls:
                    return
                first = steps[0]
                if not isinstance(first, dict):
                    return
                extra_info = first.get("extra_info")
                if not isinstance(extra_info, dict):
                    extra_info = {}
                    first["extra_info"] = extra_info
                extra_info["proxy_calls"] = calls
        """
    ).strip() + "\n"


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product: ag_london.py; shop + voucher: ag_penalty.py
        # =============================================================================

        import re as _combo_re

        _COMBO_VOUCHER_PATTERNS = (
            r"\\bmy\\s+budget\\s+is\\b",
            r"\\bbudget\\s+of\\s+\\d+",
            r"\\bwithin\\s+(?:my\\s+)?budget\\b",
            r"\\b(?:percent(?:age)?|fixed)\\s+discount\\b",
        )
        _COMBO_SHOP_PATTERNS = (
            r"\\b(?:look(?:ing)?(?:\\s+for)?|find|show|same)\\b(?:\\s+\\w+){0,10}\\s+shops?\\b",
            r"\\bsame\\s+shop\\b",
            r"\\bfrom\\s+the\\s+same\\s+shop\\b",
        )


        def _combo_route(query: str) -> str:
            q = query or ""
            for pat in _COMBO_VOUCHER_PATTERNS:
                if _combo_re.search(pat, q, _combo_re.I):
                    return "voucher"
            for pat in _COMBO_SHOP_PATTERNS:
                if _combo_re.search(pat, q, _combo_re.I):
                    return "shop"
            return "product"


        def _install_london_tools() -> None:
            register_tool("find_product", ldn_find_product)
            register_tool("view_product_information", ldn_view_product_information)
            register_tool("recommend_product", ldn_recommend_product)
            register_tool("terminate", ldn_terminate)


        def _install_penalty_tools() -> None:
            register_tool("find_product", pnl_find_product)
            register_tool("view_product_information", pnl_view_product_information)
            register_tool("recommend_product", pnl_recommend_product)
            register_tool("terminate", pnl_terminate)


        def london_product_agent_main(problem_data: dict) -> list[dict]:
            _orig_identify = ldn_Agent.__identify_challenge__

            def _product_only_identify(self: ldn_Agent) -> ldn_Challenge:
                return ldn_Challenge.PRODUCT

            ldn_Agent.__identify_challenge__ = _product_only_identify
            try:
                return ldn_agent_main(problem_data)
            finally:
                ldn_Agent.__identify_challenge__ = _orig_identify


        def penalty_shop_voucher_agent_main(problem_data: dict) -> list[dict]:
            return pnl_agent_main(problem_data)


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_route(query)
            if kind == "product":
                _install_london_tools()
                return london_product_agent_main(problem_data)
            _install_penalty_tools()
            return penalty_shop_voucher_agent_main(problem_data)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    penalty_lines = _read_lines(PENALTY)
    london_lines = _read_lines(LONDON)

    london_raw = _slice_ranges(london_lines, LONDON_RANGES)
    london_raw = _patch_tool_decorators(london_raw)
    london_raw = _patch_london_engine_map(london_raw)
    london_stripped, _ = _strip_imports(london_raw)
    london_pruned = _prune_unreachable(
        london_stripped,
        [
            "agent_main",
            "find_product",
            "view_product_information",
            "recommend_product",
            "terminate",
        ],
    )
    london_body = _prefix_source(london_pruned, "ldn_")
    london_body = _patch_london_after_prefix(london_body)
    london_body = _prune_unreachable(
        london_body,
        [
            "ldn_agent_main",
            "ldn_find_product",
            "ldn_view_product_information",
            "ldn_recommend_product",
            "ldn_terminate",
        ],
    )

    penalty_raw = _slice_ranges(penalty_lines, PENALTY_RANGES)
    penalty_raw = _strip_pig_dead_code(penalty_raw)
    penalty_raw = _patch_tool_decorators(penalty_raw)
    penalty_raw = _patch_penalty_engine_map(penalty_raw)
    penalty_stripped, _ = _strip_imports(penalty_raw)
    penalty_pruned = _prune_unreachable(
        penalty_stripped,
        [
            "agent_main",
            "find_product",
            "view_product_information",
            "recommend_product",
            "terminate",
        ],
    )
    penalty_body = _prefix_source(penalty_pruned, "pnl_")
    penalty_body = _patch_penalty_after_prefix(penalty_body)
    penalty_body = _prune_unreachable(
        penalty_body,
        [
            "pnl_agent_main",
            "pnl_find_product",
            "pnl_view_product_information",
            "pnl_recommend_product",
            "pnl_terminate",
        ],
    )

    parts = [
        _collect_imports(penalty_raw, london_raw),
        "# " + "=" * 77 + "\n",
        "# Product lane (ag_london.py — ldn_*)\n",
        "# " + "=" * 77 + "\n\n",
        london_body,
        "\n\n# " + "=" * 77 + "\n",
        "# Shop + voucher lane (ag_penalty.py — pnl_*)\n",
        "# " + "=" * 77 + "\n\n",
        penalty_body,
        "\n",
        _build_router(),
    ]
    combined = "".join(parts)
    ast.parse(combined)
    OUT.write_text(combined, encoding="utf-8")
    line_count = sum(1 for _ in OUT.open(encoding="utf-8"))
    size = OUT.stat().st_size
    print(f"Wrote {OUT} ({size} bytes, {line_count} lines)")


if __name__ == "__main__":
    main()
