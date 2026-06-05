# LangGraph Workflow

当前线上流程采用 B 方案：上传文件先进入附件预处理，PDF 会渲染成 PNG 页图；随后按页/按图识别，合并成统一图纸事实，再生成报价草稿并进入自动审核闭环。

## Nodes

```text
START
  -> prepare_attachment_files
  -> extract_drawing_facts
  -> merge_drawing_facts
  -> generate_draft_report
  -> review_candidate_report
       pass -> finalize_passed -> END
       fail and retry remains -> refresh_drawing_facts -> regenerate_report -> review_candidate_report
       fail and no retry remains -> finalize_unverified -> END
```

## State Fields

| Field | Meaning |
|---|---|
| `prompt` | 用户原始报价要求 |
| `files` | 原始上传文件路径 |
| `vision_files` | 实际送入 vision 的文件；PDF 会变成渲染后的 PNG 页图 |
| `assets` | 前端/API 可预览的原图、原 PDF 和 PDF 页图索引 |
| `preprocess_report` | 附件预处理摘要 |
| `page_summaries` | 每页/每张图的独立识别摘要 |
| `vision_context` | 合并后的图纸事实摘要 |
| `working_prompt` | 用户需求加合并图纸事实后的报价提示 |
| `candidate_report` | 当前候选报价报告；第一版会先输出给用户查看 |
| `review` | 最近一轮审核结果 |
| `audit_log` | 所有审核轮次记录 |
| `revision_count` | 已自动重写次数 |
| `max_review_rounds` | 审核失败后允许自动重写的最大次数 |
| `final_report` | 最终报告 |

## Runtime Knobs

```env
QUOTE_PDF_RENDER_DPI=200
QUOTE_PDF_MAX_PAGES=20
QUOTE_PAGE_VISION_CONCURRENCY=2
```

`QUOTE_PAGE_VISION_CONCURRENCY` 控制同一个任务内并行识别页数。多页 PDF 变慢时可以适当调高；如果上游模型 429 或 502 增多，就调低。

LangGraph CLI / Studio 可以读取项目根目录的 `langgraph.json`，图名称是 `quote_agent`。
