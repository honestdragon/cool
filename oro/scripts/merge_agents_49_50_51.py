#!/usr/bin/env python3
"""Merge ag_49 (voucher), ag_50 (product), ag_51 (shop) into one standalone agent file."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ag_52.py"

# ag_49: voucher subsystem only (exclude embedded cobalt product block)
AG49_VOUCHER_END_LINE = 6418  # inclusive; cobalt product starts ~6420

SOURCES = [
    {
        "path": ROOT / "ag_50.py",
        "prefix": "P50_",
        "label": "ag_50 product subsystem",
        "slice": None,
    },
    {
        "path": ROOT / "ag_49.py",
        "prefix": "V49_",
        "label": "ag_49 voucher subsystem",
        "slice": (1, AG49_VOUCHER_END_LINE),
    },
    {
        "path": ROOT / "ag_51.py",
        "prefix": "S51_",
        "label": "ag_51 shop subsystem",
        "slice": None,
    },
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
        "ThreadPoolExecutor",
        "as_completed",
        "quote_plus",
        "getenv",
        "re",
        "json",
        "time",
        "threading",
        "logging",
        "math",
        "unicodedata",
        "product",
        "ProxyClient",
        "create_dialogue_step",
        "execute_tool_call",
        "generate_tool_call_id",
    }
)


def _collect_toplevel_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Tuple):
                    for elt in t.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _collect_imported_names(tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    return imported


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


def _strip_module_docstring(source: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        tree.body.pop(0)
    return ast.unparse(tree)


def _strip_imports(source: str) -> tuple[str, list[str]]:
    """Remove only module-level import statements; keep imports inside try/except blocks."""
    tree = ast.parse(source)
    import_lines: list[str] = []
    new_body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_lines.append(ast.unparse(node))
            continue
        new_body.append(node)
    tree.body = new_body
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), import_lines


def _prefix_source(source: str, prefix: str) -> tuple[str, set[str]]:
    tree = ast.parse(source)
    defined = _collect_toplevel_names(tree)
    imported = _collect_imported_names(tree)
    rename_map = {
        name: f"{prefix}{name}"
        for name in defined
        if name not in imported and name not in SKIP_RENAME and not name.startswith("__")
    }
    new_tree = PrefixRenamer(rename_map).visit(tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree), set(rename_map.values())


def _dedupe_imports(import_blocks: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for block in import_blocks:
        for line in block:
            key = line.strip()
            if key not in seen:
                seen.add(key)
                out.append(line)
    # Drop duplicate future imports; keep a single one at the top via header.
    return [ln for ln in out if not ln.strip().startswith("from __future__")]


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # ═══════════════════════════════════════════════════════════════════════════════
        # Unified entry point — product: ag_50, voucher: ag_49, shop: ag_51
        # ═══════════════════════════════════════════════════════════════════════════════


        def _combined_classify_task(query: str) -> str:
            """Shared task classifier (ag_49 rules)."""
            query_lower = query.lower()
            voucher_signals = {"voucher", "budget", "discount"}
            if any(sig in query_lower for sig in voucher_signals):
                return "voucher"
            shop_keywords = re.search(
                r"\\b(both|these|offering|offers|sells|same|together|along\\s+with)\\b",
                query_lower,
            )
            if "shop" in query_lower and (
                shop_keywords is not None or V49__RE_SPLIT.search(query) is not None
            ):
                return "shop"
            return "product"


        def _combined_run_product(problem_data: dict) -> list[dict]:
            """Product problems: ag_50 pipeline."""
            try:
                P50_log_agent_flow("combined_router", "route=product")
                ctx = P50_DialogueRunContext()

                def _pipeline(ctx: "P50_DialogueRunContext", problem_data: dict) -> list[dict]:
                    P50_log_agent_flow("ShoppingDialogueOrchestrator.pipeline", "enter", query=str(problem_data.get("query", ""))[:120])
                    P50_dialogue_run_state.reset_for_run()
                    P50_clear_thread_local_http_journal()
                    ctx.steps = []
                    ctx.query = problem_data.get("query", "")
                    try:
                        task_type = "product"
                        params = P50_llm_parse_full_shopping_parameters(ctx.query, task_type)
                        P50_logger.info("run_pipeline -> params: %s", params)
                        products_info = params.get("products", [])
                        keyword_list = [e.get("keywords") or e.get("q", "") for e in products_info]
                        price_list = [e.get("price_range") for e in products_info]
                        service_list = [e.get("service") for e in products_info]
                        init_fallback = P50_build_task_intro_narration_fallback(task_type, ctx, keyword_list, price_list, service_list)
                        init_ctx: dict = {"keywords": keyword_list, "price_constraints": price_list, "service_filters": service_list}
                        if products_info and bool(products_info[0].get("only_product_type")):
                            init_ctx["only_product_type"] = True
                            init_ctx["only_product_type_reason"] = P50_ONLY_PRODUCT_TYPE_SEARCH_NOTE
                        P50_append_dialogue_step_tool_results(ctx, P50_format_dialogue_step_reasoning_text(ctx.query, init_ctx, fallback=init_fallback), [])
                        P50_run_single_product_task_branch(ctx, params)
                    except Exception:
                        P50_logger.error("product pipeline: unhandled exception", exc_info=True)
                        try:
                            P50_finalize_dialogue_product_recommendation(ctx, [P50_NO_MATCH_PRODUCT_ID_SENTINEL], "failure")
                        except Exception:
                            ctx.steps.append(P50_create_dialogue_step("Done.", [], "Done.", ctx.query, len(ctx.steps) + 1))
                    if not ctx.steps:
                        ctx.steps.append(P50_create_dialogue_step("Done.", [], "Done.", ctx.query, 1))
                    P50_merge_http_journal_into_first_dialogue_step(ctx.steps)
                    return ctx.steps

                return _pipeline(ctx, problem_data)
            except Exception:
                P50_logger.error("_combined_run_product failed", exc_info=True)
                raise


        def _combined_run_voucher(problem_data: dict) -> list[dict]:
            """Voucher problems: ag_49 pipeline."""
            V49_logger.info("combined_router: route=voucher")
            ctx = V49__SessionState()
            V49__init_session(ctx, problem_data)
            try:
                task_type = "voucher"
                params = V49__extract_params_llm(ctx.query, task_type)
                V49_logger.info("run_pipeline -> params: %s", params)
                init_fallback, init_ctx = V49__build_init_ctx(task_type, ctx, params)
                V49__emit_step(ctx, V49__step_text(ctx.query, init_ctx, fallback=init_fallback), [])
                vblock = params.get("voucher") or {}
                same_shop = (
                    bool(params.get("is_shop_voucher"))
                    or str(vblock.get("voucher_type", "")).lower() == "shop"
                    or "same shop" in ctx.query.lower()
                )
                handler = V49__run_shop_voucher if (same_shop and len(params.get("products") or []) > 1) else V49__run_voucher
                handler(ctx, params)
            except Exception:
                V49__recover_from_crash(ctx)
            if not ctx.steps:
                ctx.steps.append(V49_create_dialogue_step("Done.", [], "Done.", ctx.query, 1))
            V49__attach_trace_to_output(ctx.steps)
            return ctx.steps


        def _combined_run_shop(problem_data: dict) -> list[dict]:
            """Shop problems: ag_51 pipeline."""
            S51_ModuleLogger.info("combined_router: route=shop")
            S51_PipelineClock.pipeline_start_monotonic = time.monotonic()
            qraw = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            ctx = S51_SessionState(query=S51_utilNormalizeUserQuery(qraw))
            ctx.task = "shop"
            difficulty = problem_data.get("difficulty") if isinstance(problem_data, dict) else None
            policy = S51_pipeGetPolicy(difficulty)
            S51_RUNTIME_TRANSPORT_BUNDLE.budget_state.policy = policy
            S51_llmResetLlmBudget("shop")
            try:
                parsed = S51_utilParseRequest(ctx.query, "shop")
            except Exception:
                S51_ModuleLogger.exception("shop pipeline: parse failure")
                parsed = {}
            S51_pipeEmitIntroStep(ctx, parsed)
            try:
                S51_shopRunShopTask(ctx, parsed)
            except Exception:
                S51_ModuleLogger.exception("shop pipeline: task handler failure")
                try:
                    S51_pipeCloseInteraction(ctx, ["0"], "failure")
                except Exception:
                    ctx.steps.append(S51_create_dialogue_step("Done.", [], "Done.", ctx.query, len(ctx.steps) + 1))
            if not ctx.steps:
                ctx.steps.append(S51_create_dialogue_step("Done.", [], "Done.", ctx.query, 1))
            attachProxyCallsToDialogue(ctx.steps)
            return ctx.steps


        def agent_main(problem_data: dict) -> list[dict]:
            raw_query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            # Normalize using ag_51 normalizer (most complete)
            query = S51_utilNormalizeUserQuery(raw_query)
            task = _combined_classify_task(query)
            S51_ModuleLogger.info("agent_main: task=%s query_len=%d", task, len(query or ""))
            if task == "product":
                steps = _combined_run_product(problem_data)
            elif task == "voucher":
                steps = _combined_run_voucher(problem_data)
            else:
                steps = _combined_run_shop(problem_data)
            # Attach proxy calls if not already attached
            try:
                attachProxyCallsToDialogue(steps)
            except Exception:
                try:
                    P50_merge_http_journal_into_first_dialogue_step(steps)
                except Exception:
                    V49__attach_trace_to_output(steps)
            return steps
        '''
    ).strip("\n")


def main() -> None:
    header = textwrap.dedent(
        '''\
        """
        Combined ORO shopping agent.
        - product tasks: methodology from ag_50.py
        - voucher tasks: methodology from ag_49.py
        - shop tasks: methodology from ag_51.py
        Standalone file — does not import ag_49/ag_50/ag_51.
        """
        from __future__ import annotations

        '''
    )

    all_imports: list[list[str]] = []
    sections: list[str] = []

    for spec in SOURCES:
        raw = spec["path"].read_text(encoding="utf-8")
        if spec["slice"]:
            start, end = spec["slice"]
            raw = "".join(raw.splitlines(keepends=True)[start - 1 : end])
        raw = _strip_module_docstring(raw)
        body, imports = _strip_imports(raw)
        all_imports.append(imports)
        prefixed, _ = _prefix_source(body, spec["prefix"])
        sections.append(
            f"\n\n# {'=' * 78}\n"
            f"# {spec['label']} ({spec['path'].name}, prefix {spec['prefix']})\n"
            f"# {'=' * 78}\n\n"
            f"{prefixed}\n"
        )

    merged_imports = _dedupe_imports(all_imports)
    # Collapse duplicate identical imports (e.g. repeated dataclass / Tool lines).
    parts = [header, "\n".join(merged_imports) + "\n"]
    parts.extend(sections)
    parts.append("\n\n" + _build_router() + "\n")

    OUT.write_text("".join(parts), encoding="utf-8")
    line_count = OUT.read_text(encoding="utf-8").count("\n") + 1
    print(f"Wrote {OUT} ({line_count} lines)")


if __name__ == "__main__":
    main()
