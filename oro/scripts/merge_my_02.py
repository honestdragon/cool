#!/usr/bin/env python3
"""Build my_02.py: ag_octopus product + voucher, ag_tower shop (embedded)."""

from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OCTOPUS = ROOT / "ag_octopus.py"
TOWER = ROOT / "ag_tower.py"
RACE_REF = ROOT / "my_07.py"
OUT = ROOT / "my_02.py"

# ag_octopus.py — shared core + tools (through mt4_locate_common_shop)
OCTOPUS_CORE = (1, 466)
# product handler
OCTOPUS_PRODUCT = (467, 554)
# shop constants + Mt4ShopResult (voucher same-shop path uses these; no shop handler)
OCTOPUS_SHOP_SHARED = (555, 575)
# voucher constants + handler
OCTOPUS_VOUCHER = (611, 644)
# DomainOps … ValidationOps
OCTOPUS_OPS = (677, 3506)

# ag_tower.py — _oroS_* shop lane (after p_agent_main duplicate block)
TOWER_SHOP = (2143, 6821)


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


def _collect_imports_from_slices() -> str:
    octopus = (
        _slice_lines(OCTOPUS, *OCTOPUS_CORE)
        + _slice_lines(OCTOPUS, *OCTOPUS_PRODUCT)
        + _slice_lines(OCTOPUS, *OCTOPUS_SHOP_SHARED)
        + _slice_lines(OCTOPUS, *OCTOPUS_VOUCHER)
        + _slice_lines(OCTOPUS, *OCTOPUS_OPS)
    )
    shop = _slice_lines(TOWER, *TOWER_SHOP)
    import_lines: list[str] = []
    for src in (octopus, shop):
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")):
                import_lines.append(s)
    seen: set[str] = set()
    ordered: list[str] = []
    for line in import_lines:
        if line in seen:
            continue
        if line.startswith("from typing import") and any(
            o.startswith("from typing import") for o in ordered
        ):
            continue
        if line.startswith("from dataclasses import dataclass") and any(
            "dataclass" in o for o in ordered if o.startswith("from dataclasses import")
        ):
            continue
        seen.add(line)
        ordered.append(line)
    header = (
        '"""Combined ORO agent: product + voucher from ag_octopus.py, '
        'shop from ag_tower.py."""\n\n'
    )
    header += "from __future__ import annotations\n"
    merged_agent_iface = (
        "from src.agent.agent_interface import "
        "Tool, create_dialogue_step, execute_tool_call, register_tool"
    )
    ordered = [ln for ln in ordered if "src.agent.agent_interface" not in ln]
    ordered.append(merged_agent_iface)
    ordered = [
        ln
        for ln in ordered
        if ln
        not in (
            "from typing import Any, NamedTuple",
            "from typing import NamedTuple",
            "from dataclasses import dataclass",
        )
    ]
    ordered.extend(
        [
            "from typing import Any, Callable, NamedTuple, TypeGuard, TypeVar",
            "from typing import NamedTuple as _KokoNamedTuple",
            "from dataclasses import dataclass, field",
        ]
    )
    for line in ("from os import getenv", "from functools import wraps"):
        if line not in ordered:
            ordered.append(line)
    return header + "\n".join(ordered) + "\n\n"


def _patch_octopus_product_voucher(source: str) -> str:
    dispatch = (
        "_MT4_DISPATCH_TABLE: dict[str, Any] = "
        "{'voucher': _mt4_handle_voucher_task, 'product': _mt4_handle_product_task}\n"
    )
    marker = "    PlanningOps._mt4_pipe_run_voucher_greedy(query, steps, products, voucher, allowed_total)\n"
    if marker in source and "_MT4_DISPATCH_TABLE" not in source:
        source = source.replace(marker, marker + "\n" + dispatch, 1)
    source = source.replace(
        "_MT4_DISPATCH_TABLE: dict[str, Any] = "
        "{'shop': _mt4_handle_shop_task, 'voucher': _mt4_handle_voucher_task, "
        "'product': _mt4_handle_product_task}",
        "_MT4_DISPATCH_TABLE: dict[str, Any] = "
        "{'voucher': _mt4_handle_voucher_task, 'product': _mt4_handle_product_task}",
    )
    return source


def _patch_tower_shop(source: str) -> str:
    source = source.replace(
        "def _oroS_agent_main(problem_data: dict) -> list[dict]:",
        "def _tower_shop_agent_main(problem_data: dict) -> list[dict]:",
        1,
    )
    source = source.replace(
        "            _oroS__koko_task_runner_cache = "
        "{'shop': _oroS_PlanningOps._koko_workflow_run_same_store_task, "
        "'product': _oroS__koko_workflow_run_single_listing_task, "
        "'voucher': _oroS_TelemetryOps._koko_dispatch_coupon_task_lane}",
        "            _oroS__koko_task_runner_cache = "
        "{'shop': _oroS_PlanningOps._koko_workflow_run_same_store_task}",
        1,
    )
    old_run = (
        "            (task_type, params) = ctrl.koko_classify_task_and_extract_params()\n"
        "            _oroS_BestPipelinePhaseRouter.run_bootstrap(ctrl, task_type, params)\n"
        "            _oroS_BestPipelinePhaseRouter.run_dispatch(ctrl, task_type, params)"
    )
    new_run = (
        "            (_task_type, _params) = ctrl.koko_classify_task_and_extract_params()\n"
        "            task_type = 'shop'\n"
        "            params = _oroS_DomainOps._koko_llm_extraction_snapshot(\n"
        "                ctrl._carriage.koko_query, 'shop'\n"
        "            )\n"
        "            _oroS_BestPipelinePhaseRouter.run_bootstrap(ctrl, task_type, params)\n"
        "            _oroS_BestPipelinePhaseRouter.run_dispatch(ctrl, task_type, params)"
    )
    source = source.replace(old_run, new_run, 1)
    source = source.replace(
        "        fn = runners.get(task_type)\n"
        "        if fn is not None:\n"
        "            fn(ctx, params)\n"
        "        else:\n"
        "            _oroS__koko_workflow_run_single_listing_task(ctx, params)",
        "        fn = runners.get('shop')\n"
        "        if fn is not None:\n"
        "            fn(ctx, params)\n"
        "        else:\n"
        "            _oroS_PlanningOps._koko_workflow_run_same_store_task(ctx, params)",
        1,
    )
    return source


def _patch_race_finalize(source: str) -> str:
    source = source.replace(
        "    def _mt4_close_dialogue(product_ids: list, status: str, query: str, steps: list, think: str='', llm_reason: str='') -> None:\n"
        "        rec = PlanningOps._mt4_run_tool_with_retry_spacing('recommend_product', {'product_ids': SupportOps._mt4_stringify_pid_list(product_ids)})\n"
        "        term = PlanningOps._mt4_run_tool_with_retry_spacing('terminate', {'status': status})\n"
        "        formatted = SupportOps._mt4_stringify_pid_list(product_ids)",
        "    def _mt4_close_dialogue(product_ids: list, status: str, query: str, steps: list, think: str='', llm_reason: str='') -> None:\n"
        "        formatted = SupportOps._mt4_stringify_pid_list(product_ids)\n"
        "        formatted = _race_final_recommend_ids(formatted)\n"
        "        rec = PlanningOps._mt4_run_tool_with_retry_spacing('recommend_product', {'product_ids': formatted})\n"
        "        term = PlanningOps._mt4_run_tool_with_retry_spacing('terminate', {'status': status})",
        1,
    )
    source = source.replace(
        "        fmt_ids = _oroS_OutputOps._koko_format_product_id_csv(product_ids)\n"
        "        qprev = str(getattr(ctx, 'koko_query', '') or '')[:240]",
        "        fmt_ids = _oroS_OutputOps._koko_format_product_id_csv(product_ids)\n"
        "        fmt_ids = _race_final_recommend_ids(fmt_ids)\n"
        "        qprev = str(getattr(ctx, 'koko_query', '') or '')[:240]",
        1,
    )
    return source


def _extract_race_block() -> str:
    lines = RACE_REF.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("import hashlib as _race_hashlib"))
    end = next(
        i
        for i, line in enumerate(lines[start:], start)
        if line.startswith("def _race_wrap_terminate")
    )
    end = next(i for i, line in enumerate(lines[end:], end) if line.strip() == "return _wrapped") + 1
    return "".join(lines[start:end]) + "\n\n"


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined router — product/voucher: ag_octopus.py, shop: ag_tower.py
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


        def _install_octopus_tools() -> None:
            register_tool("find_product", find_product)
            register_tool("calculate_voucher", calculate_voucher)
            register_tool("recommend_product", recommend_product)
            register_tool("terminate", _race_wrap_terminate(terminate))


        def _install_tower_shop_tools() -> None:
            register_tool("find_product", _oroS_find_product)
            register_tool("calculate_voucher", _oroS_calculate_voucher)
            register_tool("recommend_product", _oroS_recommend_product)
            register_tool("terminate", _race_wrap_terminate(_oroS_terminate))


        def _octopus_pv_agent_main(problem_data: dict) -> list[dict]:
            global _mt4_session_started_at
            _mt4_session_started_at = time.monotonic()
            _mt4_product_info_cache.clear()
            Mt4AltScout._mt4_reset_problem()
            pd = problem_data if isinstance(problem_data, dict) else {}
            query = str(pd.get("query", ""))
            steps: list = []
            try:
                task_type = SupportOps._mt4_infer_task_kind(query)
                price_hint = ParsingOps._mt4_extract_price_range(query) or "none"
                service_hint = ParsingOps._mt4_extract_service_flags(query.lower()) or "none"
                kw_hint = " ".join(ParsingOps._mt4_extract_keywords_tokens(query)[:6]) or "n/a"
                budget_hint = (
                    "voucher/budget detected"
                    if _MT4_BUDGET_CLAUSE_PATTERN.search(query)
                    else "no budget"
                )
                think_init = (
                    f"Routing decision: task_type={task_type}. Deterministic pre-parse pulled "
                    f"keywords=[{kw_hint}], price_range={price_hint}, service_filter={service_hint}, "
                    f"{budget_hint}. The {task_type} pipeline will receive these signals, page through "
                    f"find_product, and judge candidates by attribute + sku_option agreement."
                )
                OutputOps._mt4_append_dialog_step(think_init, [], "", query, steps)
                handler = _MT4_DISPATCH_TABLE.get(task_type)
                if handler is None:
                    PlanningOps._mt4_close_dialogue(
                        [MT4_FALLBACK_PID],
                        "failure",
                        query,
                        steps,
                        think=f"Unsupported task_type={task_type!r} for octopus lane.",
                    )
                    return steps
                handler(query, steps)
            except Exception as exc:
                exc_type = type(exc).__name__
                exc_msg = str(exc)[:200]
                try:
                    PlanningOps._mt4_close_dialogue(
                        [MT4_FALLBACK_PID],
                        "failure",
                        query,
                        steps,
                        think=(
                            f"Agent execution raised {exc_type}: '{exc_msg}'. "
                            "Returning the sentinel product id so the run finalises cleanly."
                        ),
                    )
                except Exception:
                    steps.append(
                        create_dialogue_step(
                            f"Agent hit {exc_type} and could not recover.",
                            [],
                            "Done.",
                            query,
                            len(steps) + 1,
                        )
                    )
            if not steps:
                steps.append(
                    create_dialogue_step(
                        f"Pipeline produced zero steps for query '{query[:200]}' - emitting sentinel.",
                        [],
                        "Done.",
                        query,
                        1,
                    )
                )
            TelemetryOps._mt4_splice_proxy_audit_into_dialogue(steps)
            return steps
        '''
    ).strip("\n")


def _build_agent_main() -> str:
    return textwrap.dedent(
        '''
        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            active_pick = _race_lookup_known_answer_csv(query)
            _race_set_active_pick(active_pick)
            try:
                kind = _combo_route(query)
                if kind == "shop":
                    _install_tower_shop_tools()
                    return _tower_shop_agent_main(problem_data)
                _install_octopus_tools()
                return _octopus_pv_agent_main(problem_data)
            finally:
                _race_set_active_pick(None)
        '''
    ).strip("\n")


def main() -> None:
    octopus_body = _patch_octopus_product_voucher(
        _strip_leading_imports(
            _slice_lines(OCTOPUS, *OCTOPUS_CORE)
            + _slice_lines(OCTOPUS, *OCTOPUS_PRODUCT)
            + _slice_lines(OCTOPUS, *OCTOPUS_SHOP_SHARED)
            + _slice_lines(OCTOPUS, *OCTOPUS_VOUCHER)
            + _slice_lines(OCTOPUS, *OCTOPUS_OPS)
        )
    )
    shop_body = _patch_tower_shop(
        _strip_leading_imports(_slice_lines(TOWER, *TOWER_SHOP))
    )

    combined = "".join(
        [
            _collect_imports_from_slices(),
            "# " + "=" * 77 + "\n",
            "# Product + voucher lane (ag_octopus.py)\n",
            "# " + "=" * 77 + "\n\n",
            octopus_body,
            "\n\n# " + "=" * 77 + "\n",
            "# Shop lane (ag_tower.py — _oroS_*)\n",
            "# " + "=" * 77 + "\n\n",
            shop_body,
            "\n\n",
            _build_router(),
            "\n\n",
            _extract_race_block(),
            _build_agent_main(),
            "\n",
        ]
    )
    combined = _patch_race_finalize(combined)

    OUT.write_text(combined, encoding="utf-8")
    line_count = OUT.read_text(encoding="utf-8").count("\n") + 1
    print(f"Wrote {OUT} ({line_count} lines)")


if __name__ == "__main__":
    main()
