from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from inspect import isawaitable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .model_config import ModelConfig, build_model_config
from .qc import run_with_quality_loop


DEFAULT_WATCH_PROMPT = (
    "请识别这份图纸品类，并调用对应成本 Agent 生成报价/成本分析报告。"
    "如果缺少材料单价、工艺单价或关键尺寸，请列为待确认项。"
)


def has_any_model_key() -> bool:
    return any(
        os.getenv(name)
        for name in (
            "OPENAI_API_KEY",
            "QUOTE_VISION_API_KEY",
            "QUOTE_WORK_API_KEY",
            "QUOTE_REVIEW_API_KEY",
        )
    )


def model_config(args: argparse.Namespace) -> ModelConfig:
    legacy_model = getattr(args, "model", None)
    work_model = getattr(args, "work_model", None) or legacy_model
    vision_model = getattr(args, "vision_model", None) or legacy_model
    review_model = getattr(args, "review_model", None) or legacy_model
    return build_model_config(
        work_model_override=work_model,
        vision_model_override=vision_model,
        review_model_override=review_model,
    )


async def run_quote(
    prompt: str,
    files: list[Path],
    models: ModelConfig,
    max_review_rounds: int,
    include_audit: bool = False,
) -> str:
    if not has_any_model_key():
        raise SystemExit("请先设置至少一个模型 API key，或复制 .env.example 为 .env 后填写。")

    workflow_engine = os.getenv("QUOTE_WORKFLOW_ENGINE", "").strip().lower()
    if workflow_engine in {"langgraph", "graph"}:
        from .langgraph_workflow import run_quote_langgraph

        return await run_quote_langgraph(
            prompt=prompt,
            files=files,
            vision_model=models.vision_model,
            vision_model_name=models.vision_model_label,
            work_model=models.work_model,
            work_model_name=models.work_model_label,
            review_model=models.review_model,
            review_model_name=models.review_model_label,
            max_review_rounds=max_review_rounds,
            include_audit=include_audit,
        )

    return await run_with_quality_loop(
        prompt=prompt,
        files=files,
        vision_model=models.vision_model,
        vision_model_name=models.vision_model_label,
        work_model=models.work_model,
        work_model_name=models.work_model_label,
        review_model=models.review_model,
        review_model_name=models.review_model_label,
        max_review_rounds=max_review_rounds,
        include_audit=include_audit,
    )


async def quote_command(args: argparse.Namespace) -> None:
    prompt = args.prompt or sys.stdin.read().strip()
    if not prompt:
        raise SystemExit("请提供 prompt，或通过 stdin 输入。")

    files = [Path(file_path).resolve() for file_path in args.file]
    output = await run_quote(
        prompt,
        files,
        model_config(args),
        max_review_rounds=args.max_review_rounds,
        include_audit=args.audit,
    )
    print(output)


def output_name_for(path: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in path.stem)
    return f"{timestamp}-{safe_stem}.md"


async def watch_command(args: argparse.Namespace) -> None:
    inbox = Path(args.inbox).resolve()
    outbox = Path(args.outbox).resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching: {inbox}")
    print(f"Writing reports to: {outbox}")

    while True:
        for path in sorted(inbox.iterdir()):
            if not path.is_file() or path in seen:
                continue

            seen.add(path)
            print(f"Processing: {path.name}")
            try:
                report = await run_quote(
                    args.prompt,
                    [path],
                    model_config(args),
                    max_review_rounds=args.max_review_rounds,
                    include_audit=args.audit,
                )
                report_path = outbox / output_name_for(path)
                report_path.write_text(report, encoding="utf-8")
                print(f"Done: {report_path.name}")
            except Exception as exc:
                error_path = outbox / output_name_for(path.with_suffix(".error.md"))
                error_path.write_text(f"# 报价失败\n\n文件：{path}\n\n错误：{exc}\n", encoding="utf-8")
                print(f"Failed: {path.name}: {exc}")

        await asyncio.sleep(args.interval)


def serve_command(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "quote_assistant.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


async def rag_sync_command(args: argparse.Namespace) -> None:
    from .external_rag import seed_documents_preview, sync_seed_documents_to_external

    if args.preview:
        print(seed_documents_preview())
        return
    result = await sync_seed_documents_to_external()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def rag_init_command(args: argparse.Namespace) -> None:
    from .external_rag import init_rag_store

    result = init_rag_store()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立多 Agent 自动报价助手")
    parser.add_argument("--model", help="兼容旧参数：同时覆盖工作模型和审核模型")
    parser.add_argument("--work-model", help="覆盖工作模型：识别、路由、成本推理、初版报告")
    parser.add_argument("--vision-model", help="覆盖视觉识别模型：图片/PDF/附件事实提取")
    parser.add_argument("--review-model", help="覆盖审核模型：质量复核")

    subparsers = parser.add_subparsers(dest="command", required=True)

    quote = subparsers.add_parser("quote", help="手动发起一次报价")
    quote.add_argument("prompt", nargs="?", help="报价需求；省略时从 stdin 读取")
    quote.add_argument("--file", action="append", default=[], help="图纸/PDF/图片路径，可重复传入")
    quote.add_argument("--max-review-rounds", type=int, default=2, help="自动审核不通过后的最多重跑轮数")
    quote.add_argument("--audit", action="store_true", help="输出中附加自动审核记录")
    quote.set_defaults(func=quote_command)

    watch = subparsers.add_parser("watch", help="监听 inbox 文件夹，自动生成 outbox 报告")
    watch.add_argument("--inbox", default="inbox", help="待报价图纸目录")
    watch.add_argument("--outbox", default="outbox", help="报价报告输出目录")
    watch.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒数")
    watch.add_argument("--prompt", default=DEFAULT_WATCH_PROMPT, help="自动报价默认提示词")
    watch.add_argument("--max-review-rounds", type=int, default=2, help="自动审核不通过后的最多重跑轮数")
    watch.add_argument("--audit", action="store_true", help="报告中附加自动审核记录")
    watch.set_defaults(func=watch_command)

    serve = subparsers.add_parser("serve", help="启动 Web/API 服务")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址；对外服务可设为 0.0.0.0")
    serve.add_argument("--port", type=int, default=8000, help="监听端口")
    serve.add_argument("--reload", action="store_true", help="开发时自动重载")
    serve.set_defaults(func=serve_command)

    rag_init = subparsers.add_parser("rag-init", help="Initialize the configured local pgvector RAG store")
    rag_init.set_defaults(func=rag_init_command)

    rag_sync = subparsers.add_parser("rag-sync", help="Sync built-in quote knowledge to the configured RAG store")
    rag_sync.add_argument("--preview", action="store_true", help="Print seed knowledge documents without uploading")
    rag_sync.set_defaults(func=rag_sync_command)

    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
        if isawaitable(result):
            asyncio.run(result)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
