#!/usr/bin/env python3
"""Build my_04.py: ag_marz product lane + ag_london shop/voucher lanes."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARZ = ROOT / "ag_marz.py"
MY03 = ROOT / "my_03.py"
OUT = ROOT / "my_04.py"

# ag_marz.py — product lane slices (exclude knapsack/shop/voucher engines)
MARZ_TOOLS = (19, 57)
MARZ_CORE = (96, 2651)
MARZ_PRODUCT_ENGINE = (2951, 3221)
MARZ_AGENT = (3516, 3932)

# my_03.py — already-embedded ag_london shop/voucher lane (stop before prefetch cheat block)
MY03_LONDON = (3049, 6251)


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


def _patch_marz_tools(source: str) -> str:
    source = source.replace("@Tool\ndef find_product", "def _marz_find_product")
    source = source.replace("@Tool\ndef view_product_information", "def _marz_view_product_information")
    source = source.replace("@Tool\ndef recommend_product", "def _marz_recommend_product")
    source = source.replace("@Tool\ndef terminate", "def _marz_terminate")
    return source


def _patch_marz_proxy(source: str) -> str:
    source = re.sub(
        r"\n    @staticmethod\n    def emit_calculate_voucher\([\s\S]*?return tool_res\n",
        "\n",
        source,
        count=1,
    )
    return source


def _patch_marz_agent(source: str) -> str:
    source = source.replace(
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "        Challenge.SHOP: ShopEngine,\n"
        "        Challenge.VOUCHER: VoucherEngine\n"
        "    }",
        "    ENGINE_MAP: Dict[Challenge, Type[BaseEngine]] = {\n"
        "        Challenge.PRODUCT: ProductEngine,\n"
        "    }",
    )
    source = source.replace(
        "            if challenge == Challenge.VOUCHER:\n"
        "                query_e, vopt = self.__parse_voucher__()\n"
        "            else:\n"
        "                query_e, vopt = self.query, None\n",
        "            query_e, vopt = self.query, None\n",
    )
    source = source.replace(
        "        if qi.challenge == Challenge.VOUCHER and best_p and qi.vopt is not None:\n"
        "            ProxyUtil.emit_calculate_voucher(self.sess, best_p, qi.vopt)\n\n",
        "",
    )
    source = re.sub(
        r"\n    def __parse_voucher__\(self\)[\s\S]*?return query_e, vopt\n",
        "\n",
        source,
        count=1,
    )
    source = source.replace(
        "    def __identify_challenge__(self) -> Challenge:\n"
        "        if re.search(r\"\\bmy\\s+budget\\s+is\\b\", self.query, re.I):\n"
        "            return Challenge.VOUCHER\n"
        "        elif re.search(\n"
        "            r\"\\b(?:look(?:ing)?(?:\\s+for)?|find|show|same)\\b(?:\\s+\\w+){0,10}\\s+shops?\\b\",\n"
        "            self.query, re.I\n"
        "        ):\n"
        "            return Challenge.SHOP\n"
        "        else:\n"
        "            return Challenge.PRODUCT",
        "    def __identify_challenge__(self) -> Challenge:\n"
        "        return Challenge.PRODUCT",
    )
    return source


def _collect_imports() -> str:
    header = (
        '"""Combined ORO agent: product from ag_marz.py, shop + voucher from ag_london.py."""\n\n'
    )
    header += "from __future__ import annotations\n\n"
    header += textwrap.dedent(
        """
        from src.agent.agent_interface import ToolCallResult, Tool, execute_tool_call, create_dialogue_step, register_tool
        from src.agent.proxy_client import ProxyClient

        from typing import Any, Optional, List, Dict, Tuple, Union, Type, ClassVar, get_origin, get_args
        from os import getenv
        from dataclasses import dataclass, fields, field, is_dataclass
        from bisect import bisect_left, bisect_right
        from collections import defaultdict
        from itertools import product
        from types import NoneType
        from enum import Enum

        import time
        import json
        import math
        import re

        """
    ).strip("\n") + "\n\n"
    return header


def _patch_london_lane(source: str) -> str:
    return source.replace(
        '{"product_ids": _query_resolve_final_ids(pid_str)}',
        '{"product_ids": pid_str}',
    )


def _build_router() -> str:
    return textwrap.dedent(
        '''
        # =============================================================================
        # Combined entry router (ag_marz product + ag_london shop/voucher)
        # =============================================================================


        def _combo_classify_task(query: str) -> str:
            if re.search(r"\\bmy\\s+budget\\s+is\\b", query or "", re.I):
                return "voucher"
            if re.search(
                r"\\b(?:look(?:ing)?(?:\\s+for)?|find|show|same)\\b(?:\\s+\\w+){0,10}\\s+shops?\\b",
                query or "",
                re.I,
            ):
                return "shop"
            return "product"


        def _install_marz_product_tools() -> None:
            register_tool("find_product", _marz_find_product)
            register_tool("view_product_information", _marz_view_product_information)
            register_tool("recommend_product", _marz_recommend_product)
            register_tool("terminate", _marz_terminate)


        def _install_ldn_tools() -> None:
            register_tool("find_product", ldn_find_product)
            register_tool("view_product_information", ldn_view_product_information)
            register_tool("recommend_product", ldn_recommend_product)
            register_tool("terminate", ldn_terminate)


        def _combo_ensure_dialog(dialog: list, query: str, lane: str) -> list:
            if dialog:
                return dialog
            return [
                create_dialogue_step(
                    think=f"Agent returned no dialogue steps ({lane} lane).",
                    tool_results=[],
                    response="Done.",
                    query=query,
                    step=1,
                )
            ]


        def _marz_product_agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list = []
            try:
                agent = Agent(query)
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
            return _combo_ensure_dialog(dialog, query, "product")


        def _ldn_shop_voucher_agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            dialog: list = []
            try:
                LdnProxyUtil.arm_deadline()
                agent = LdnAgent(query)
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
            return _combo_ensure_dialog(dialog, query, "shop/voucher")


        def agent_main(problem_data: dict) -> list:
            query = problem_data.get("query", "") if isinstance(problem_data, dict) else ""
            if not isinstance(query, str):
                query = str(query)
            kind = _combo_classify_task(query)
            if kind in ("shop", "voucher"):
                _install_ldn_tools()
                return _ldn_shop_voucher_agent_main(problem_data)
            _install_marz_product_tools()
            return _marz_product_agent_main(problem_data)
        '''
    ).strip("\n") + "\n"


def main() -> None:
    product_body = _patch_marz_agent(
        _patch_marz_proxy(
            _patch_marz_tools(
                _strip_leading_imports(_slice_lines(MARZ, *MARZ_TOOLS))
            )
            + "\n"
            + _strip_leading_imports(_slice_lines(MARZ, *MARZ_CORE))
            + "\n"
            + _strip_leading_imports(_slice_lines(MARZ, *MARZ_PRODUCT_ENGINE))
            + "\n"
            + _strip_leading_imports(_slice_lines(MARZ, *MARZ_AGENT))
        )
    )

    london_body = _patch_london_lane(
        _slice_lines(MY03, *MY03_LONDON).replace(
            "# Shop + voucher lane (ag_london.py)",
            "# Shop + voucher lane (ag_london.py — Ldn_ prefixed)",
            1,
        )
    )

    combined = "".join(
        [
            _collect_imports(),
            "# " + "=" * 77 + "\n",
            "# Product lane (ag_marz.py)\n",
            "# " + "=" * 77 + "\n\n",
            product_body,
            "\n\n",
            london_body,
            "\n\n",
            _build_router(),
        ]
    )

    OUT.write_text(combined, encoding="utf-8")
    line_count = OUT.read_text(encoding="utf-8").count("\n") + 1
    print(f"Wrote {OUT} ({line_count} lines)")


if __name__ == "__main__":
    main()
