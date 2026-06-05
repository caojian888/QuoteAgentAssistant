from __future__ import annotations

import operator
import asyncio
import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - dependency is optional until enabled.
    END = None  # type: ignore[assignment]
    START = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]

from .qc import (
    ReviewOutcome,
    append_audit_summary,
    build_prompt_with_vision_context,
    build_retry_prompt,
    extract_vision_context,
    generate_once,
    review_once,
    unverified_report,
)
from .attachments import prepare_attachments
from .asset_classifier import classify_asset_manifest, format_asset_manifest_for_prompt


class QuoteGraphState(TypedDict, total=False):
    prompt: str
    files: list[Path]
    original_files: list[Path]
    vision_files: list[Path]
    assets: list[dict[str, Any]]
    preprocess_report: str
    asset_manifest: dict[str, Any]
    vision_model: Any
    vision_model_name: str
    work_model: Any
    work_model_name: str
    review_model: Any
    review_model_name: str
    max_review_rounds: int
    include_audit: bool
    page_summaries: list[dict[str, str]]
    vision_context: str
    working_prompt: str
    candidate_report: str
    review: ReviewOutcome
    audit_log: Annotated[list[ReviewOutcome], operator.add]
    revision_count: int
    final_report: str
    status: str


def _files(state: QuoteGraphState) -> list[Path]:
    return list(state.get("files") or [])


def _vision_files(state: QuoteGraphState) -> list[Path]:
    return list(state.get("vision_files") or state.get("files") or [])


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _review_feedback(review: ReviewOutcome) -> str:
    issues = "\n".join(f"- {issue}" for issue in review.issues)
    if review.revision_prompt:
        return f"{issues}\n\nRevision request:\n{review.revision_prompt}".strip()
    return issues


def _asset_label(path: Path, assets: list[dict[str, Any]]) -> str:
    target = str(path)
    for asset in assets:
        if str(asset.get("path") or "") == target:
            return str(asset.get("label") or path.name)
    return path.name


def _page_prompt(base_prompt: str, label: str, index: int, total: int, asset_manifest_text: str = "") -> str:
    manifest_block = ""
    if asset_manifest_text:
        manifest_block = f"""

Prepared asset classification:
{asset_manifest_text}

Use the classification to separate technical drawings, real photos, CAD renders,
BOM tables, notes, and title blocks. Dimensions and BOM facts should come from
technical drawing / BOM regions. Real photos are appearance reference only.
"""
    return f"""
{base_prompt}

Prepared attachment context:
- This is item {index} of {total} in the prepared vision inputs.
- Attachment label: {label}
{manifest_block}

Extract only facts visible in this single image/page. Keep page-specific dimensions,
materials, process notes, title-block data, and uncertain items clearly separated.
Do not quote a price in this vision step.
""".strip()


def prepare_attachment_files(state: QuoteGraphState) -> QuoteGraphState:
    return prepare_attachments(_files(state))


async def classify_assets(state: QuoteGraphState) -> QuoteGraphState:
    manifest = await classify_asset_manifest(
        image_assets=list(state.get("assets") or []),
        vision_model_name=state.get("vision_model_name"),
    )
    return {"asset_manifest": manifest}


async def extract_drawing_facts(state: QuoteGraphState) -> QuoteGraphState:
    files = _vision_files(state)
    if not files:
        return {"page_summaries": [], "vision_context": "", "working_prompt": state["prompt"]}

    assets = list(state.get("assets") or [])
    review = state.get("review")
    review_feedback = _review_feedback(review) if review else None
    previous_summary = state.get("vision_context") if review_feedback else None
    concurrency = max(_env_int("QUOTE_PAGE_VISION_CONCURRENCY", 2), 1)
    semaphore = asyncio.Semaphore(concurrency)
    total = len(files)
    asset_manifest_text = format_asset_manifest_for_prompt(state.get("asset_manifest"), max_chars=3600)

    async def extract_one(index: int, path: Path) -> dict[str, str]:
        label = _asset_label(path, assets)
        async with semaphore:
            summary = await extract_vision_context(
                _page_prompt(state["prompt"], label, index, total, asset_manifest_text),
                [path],
                state["vision_model"],
                state["vision_model_name"],
                previous_summary=previous_summary,
                review_feedback=review_feedback,
            )
        return {"label": label, "path": str(path), "summary": summary.strip()}

    page_summaries = await asyncio.gather(
        *(extract_one(index, path) for index, path in enumerate(files, start=1))
    )
    if not any(item["summary"] for item in page_summaries):
        raise RuntimeError("Vision summaries are empty; cannot generate a quote report.")

    return {"page_summaries": page_summaries}


def merge_drawing_facts(state: QuoteGraphState) -> QuoteGraphState:
    page_summaries = list(state.get("page_summaries") or [])
    if not page_summaries:
        return {"vision_context": "", "working_prompt": state["prompt"]}

    sections: list[str] = []
    preprocess_report = str(state.get("preprocess_report") or "").strip()
    if preprocess_report:
        sections.append(f"Attachment preprocessing:\n{preprocess_report}")

    asset_manifest_text = format_asset_manifest_for_prompt(state.get("asset_manifest"))
    if asset_manifest_text:
        sections.append(asset_manifest_text)

    page_sections = []
    for index, item in enumerate(page_summaries, start=1):
        label = item.get("label") or f"page {index}"
        summary = item.get("summary") or ""
        page_sections.append(f"## {index}. {label}\n{summary}")

    sections.append("Page-by-page drawing recognition:\n\n" + "\n\n".join(page_sections))
    vision_context = "\n\n".join(sections).strip()
    return {
        "vision_context": vision_context,
        "working_prompt": build_prompt_with_vision_context(state["prompt"], vision_context),
    }


async def generate_draft_report(state: QuoteGraphState) -> QuoteGraphState:
    report = await generate_once(state["working_prompt"], [], state["work_model"], state["work_model_name"])
    return {"candidate_report": report, "revision_count": 0, "status": "draft_ready"}


async def review_candidate_report(state: QuoteGraphState) -> QuoteGraphState:
    review = await review_once(
        state["working_prompt"],
        state["candidate_report"],
        [],
        state["review_model"],
        state["review_model_name"],
    )
    return {"review": review, "audit_log": [review]}


async def refresh_drawing_facts(state: QuoteGraphState) -> QuoteGraphState:
    files = _vision_files(state)
    if not files:
        return {}

    page_state = await extract_drawing_facts(state)
    merged_state = merge_drawing_facts({**state, **page_state})
    return {**page_state, **merged_state}


async def regenerate_report(state: QuoteGraphState) -> QuoteGraphState:
    next_revision_count = int(state.get("revision_count", 0)) + 1
    retry_prompt = build_retry_prompt(
        state["working_prompt"],
        state["candidate_report"],
        state["review"],
        next_revision_count + 1,
    )
    report = await generate_once(retry_prompt, [], state["work_model"], state["work_model_name"])
    return {"candidate_report": report, "revision_count": next_revision_count}


def route_after_review(
    state: QuoteGraphState,
) -> Literal["finalize_passed", "refresh_drawing_facts", "finalize_unverified"]:
    review = state["review"]
    if review.passed:
        return "finalize_passed"

    if int(state.get("revision_count", 0)) < int(state.get("max_review_rounds", 0)):
        return "refresh_drawing_facts"

    return "finalize_unverified"


def finalize_passed(state: QuoteGraphState) -> QuoteGraphState:
    report = state["candidate_report"]
    if state.get("include_audit"):
        report = append_audit_summary(report, state.get("audit_log", []))
    return {"final_report": report, "status": "completed"}


def finalize_unverified(state: QuoteGraphState) -> QuoteGraphState:
    return {
        "final_report": unverified_report(state["candidate_report"], state.get("audit_log", [])),
        "status": "failed_review",
    }


def build_quote_graph() -> Any:
    if StateGraph is None or START is None or END is None:
        raise RuntimeError("LangGraph is not installed. Run `pip install -r requirements.txt` first.")

    workflow = StateGraph(QuoteGraphState)
    workflow.add_node("prepare_attachment_files", prepare_attachment_files)
    workflow.add_node("classify_assets", classify_assets)
    workflow.add_node("extract_drawing_facts", extract_drawing_facts)
    workflow.add_node("merge_drawing_facts", merge_drawing_facts)
    workflow.add_node("generate_draft_report", generate_draft_report)
    workflow.add_node("review_candidate_report", review_candidate_report)
    workflow.add_node("refresh_drawing_facts", refresh_drawing_facts)
    workflow.add_node("regenerate_report", regenerate_report)
    workflow.add_node("finalize_passed", finalize_passed)
    workflow.add_node("finalize_unverified", finalize_unverified)

    workflow.add_edge(START, "prepare_attachment_files")
    workflow.add_edge("prepare_attachment_files", "classify_assets")
    workflow.add_edge("classify_assets", "extract_drawing_facts")
    workflow.add_edge("extract_drawing_facts", "merge_drawing_facts")
    workflow.add_edge("merge_drawing_facts", "generate_draft_report")
    workflow.add_edge("generate_draft_report", "review_candidate_report")
    workflow.add_conditional_edges(
        "review_candidate_report",
        route_after_review,
        {
            "finalize_passed": "finalize_passed",
            "refresh_drawing_facts": "refresh_drawing_facts",
            "finalize_unverified": "finalize_unverified",
        },
    )
    workflow.add_edge("refresh_drawing_facts", "regenerate_report")
    workflow.add_edge("regenerate_report", "review_candidate_report")
    workflow.add_edge("finalize_passed", END)
    workflow.add_edge("finalize_unverified", END)
    return workflow.compile()


def _compile_graph_if_available() -> Any | None:
    try:
        return build_quote_graph()
    except RuntimeError:
        return None


graph = _compile_graph_if_available()


async def run_quote_langgraph(
    *,
    prompt: str,
    files: list[Path],
    vision_model: Any,
    vision_model_name: str,
    work_model: Any,
    work_model_name: str,
    review_model: Any,
    review_model_name: str,
    max_review_rounds: int,
    include_audit: bool = False,
) -> str:
    compiled_graph = build_quote_graph()
    result = await compiled_graph.ainvoke(
        {
            "prompt": prompt,
            "files": files,
            "vision_model": vision_model,
            "vision_model_name": vision_model_name,
            "work_model": work_model,
            "work_model_name": work_model_name,
            "review_model": review_model,
            "review_model_name": review_model_name,
            "max_review_rounds": max_review_rounds,
            "include_audit": include_audit,
            "audit_log": [],
            "revision_count": 0,
        }
    )
    return str(result["final_report"])
