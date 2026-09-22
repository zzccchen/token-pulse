"""English translations keyed by Chinese source templates."""

MESSAGES = {
    ("\n采集检查 {checked} · 当前范围 {value} 段"): (
        "\nChecked {checked} · {value} records in this range"
    ),
    (" · 加权平均 {weighted_tps:.1f} TPS · 中位数 {median_tps:.1f} TPS"): (
        " · Weighted mean {weighted_tps:.1f} TPS · Median {median_tps:.1f} TPS"
    ),
    " · 包含多个模型，选择一个模型查看平均速度": " · Select one model to see its average speed",
    " · 复用 {backfill_reused} 个": " · {backfill_reused} reused",
    " · 已过期": " · Stale",
    " · 最近采样 {stamp}": " · Last sample {stamp}",
    " · 此范围无记录": " · No records in this range",
    "30 分钟": "30 min",
    "1 天": "1 day",
    "7 天": "7 days",
    "API 密钥": "API key",
    "ChatGPT 登录": "ChatGPT login",
    "Codex 数据目录": "Codex data directory",
    "Codex 数据目录，默认 CODEX_HOME 或 ~/.codex": (
        "Codex data directory; defaults to CODEX_HOME or ~/.codex"
    ),
    "TPS 数据不足": "Insufficient TPS data",
    "TokenPulse  词脉": "TokenPulse",
    "TokenPulse · 词脉": "TokenPulse",
    "TokenPulse · 词脉（演示数据）": "TokenPulse (demo data)",
    "TokenPulse 词脉": "TokenPulse",
    "TokenPulse 词脉 — 本机 Codex 只读托盘监视器": (
        "TokenPulse — a local, read-only Codex tray monitor"
    ),
    "token 口径": "Token scope",
    "token 口径不支持合并测速": "Token scope excluded from speed statistics",
    "token 计数无效": "Invalid token count",
    "{active} 个活动": "Active: {active}",
    ("{at} · {value}\n{tps:.1f} tokens/s\n{output_tokens:,} 总输出 tokens · {seconds:.2f} 秒"): (
        "{at} · {value}\n{tps:.1f} tokens/s\n"
        "{output_tokens:,} total output tokens · "
        "{seconds:.2f} s"
    ),
    "{count} 个{value}": "{value}: {count}",
    "{count} 段可测速 · {observed_conversations} 个对话": (
        "Measured: {count} · Conversations: {observed_conversations}"
    ),
    "{excluded} 段未计入": "Excluded: {excluded}",
    "{output_tokens:,} tokens · {seconds:.2f} 秒": "{output_tokens:,} tokens · {seconds:.2f} s",
    "{stamp} — {end} · 按完成时间归档 · 对话标识已匿名化": (
        "{stamp} — {end} · By completion time · Anonymous conversation IDs"
    ),
    "{stamp} 区间样本 · 词脉": "{stamp} range samples · TokenPulse",
    ("{value}\n{boundary} · 解析版本 {parser_revision}"): (
        "{value}\n{boundary} · Parser revision {parser_revision}"
    ),
    "{value} {count} 段": "{value}: {count}",
    "{value} 段 · {valid} 段有效 · {value2} 段数据不足": (
        "Records: {value} · Valid: {valid} · Insufficient data: {value2}"
    ),
    "。完成一次本机输出后查看，或调整筛选。": ". Complete a local output or adjust the filters.",
    ("中位数 {median_tps:.1f} · 范围 {low_tps:.1f}–{high_tps:.1f} TPS"): (
        "Median {median_tps:.1f} · Range {low_tps:.1f}–{high_tps:.1f} TPS"
    ),
    "中位数 — · 范围 —": "Median — · Range —",
    "事件时间顺序冲突": "Conflicting event order",
    "事件结构或时间无法解析": "Unparseable event structure or timestamp",
    "仅使用普通窗口": "Use a normal window without a tray icon",
    "仅供验证：演示窗口启动后自动退出": (
        "For verification: exit automatically after opening the demo"
    ),
    "仅修改词脉的设置，不修改 Codex 配置。无系统托盘时使用普通窗口。": (
        "Changes TokenPulse settings only. A normal "
        "window is used when no system tray is available."
    ),
    "仅有工具调用项通知，不是完整生成计时": (
        "Tool call notification only; full generation timing unavailable"
    ),
    "仅本机统计 · 保留 30 天 / 最多 10,000 段 · 任务标识已匿名化": (
        "Local statistics · Retained for 30 days / up to 10,000 records · Anonymous task IDs"
    ),
    "任务": "Task",
    "任务索引暂不可用，正在读取近三天会话。": (
        "Task index unavailable; reading sessions from the last three days."
    ),
    "使用隔离的合成演示数据": "Use isolated synthetic demo data",
    "保存": "Save",
    "全局配置暂不可读，配置层级显示未知。": (
        "Global configuration unreadable; configured tier is unknown."
    ),
    "全部{title}": "All: {title}",
    ("共 {value} 段记录 · 汇总此模型全部输出\n{evidence} · 总输出 tokens · 按输出时长加权"): (
        "Records: {value} · All outputs for this model\n"
        "{evidence} · Total output tokens · Weighted by "
        "output duration"
    ),
    "关闭": "Close",
    "其他": "Other",
    "其他 / 未记录": "Other / Unknown",
    "其他：{tier}（{evidence}）": "Other: {tier} ({evidence})",
    "Fast（{evidence}）": "Fast ({evidence})",
    "减少动态效果，直接显示交互结果": ("Reduce motion; show interaction results immediately"),
    "减少透明效果，提高表面与边界的清晰度": (
        "Reduce transparency for clearer surfaces and borders"
    ),
    "区间样本": "Range samples",
    "单段输出": "Single output",
    "单段输出 · 词脉": "Single output · TokenPulse",
    "历史暂不可写入，请检查词脉数据目录的访问权限。": (
        "Cannot save history; check access to the TokenPulse data directory."
    ),
    "历史观测": "History",
    "取消": "Cancel",
    "口径": "Scope",
    "只读诊断，输出不含任务标题的统计后退出": (
        "Print read-only diagnostic statistics without task titles, then exit"
    ),
    "合成边界": "Synthetic boundaries",
    "同一输出有冲突的结束时间": "Conflicting end times for the same output",
    "启动时仅显示托盘；无托盘则显示窗口": (
        "Start in the tray; show a window if no tray is available"
    ),
    "响应期间发生明确错误": "Explicit error during response",
    "外观与观测": "Appearance & monitoring",
    "存在冲突，未采用": "Conflicting evidence; not used",
    "实际层级": "Actual tier",
    "客户端提交层级": "Client-submitted tier",
    "导出失败": "Export failed",
    "导出当前筛选 CSV": "Export filtered CSV",
    "导出统计": "Export statistics",
    "尚未发现模型": "No model found yet",
    "尚未发现活动对话": "No active conversations found yet",
    "已发现输出记录，缺少可测速证据": "Outputs found; timing evidence insufficient",
    "已导出 {value} 段统计，任务标识已匿名化。": (
        "Exported {value} records with anonymous task IDs."
    ),
    "强度": "Effort",
    "当前全局配置": "Current global config",
    "当前范围无提交记录": "No submission records in this range",
    "思考强度": "Reasoning effort",
    "总输出": "Total output",
    "所选散点对应的已完成输出 · 任务标识已匿名化": (
        "Completed output for the selected point · Anonymous task IDs"
    ),
    "打开词脉": "Open TokenPulse",
    "托盘显示平均 TPS": "Show average TPS in the tray",
    "执行工具": "Running tools",
    "接入渠道": "Connection channel",
    "推理强度": "Reasoning effort",
    "提交层级": "Submitted tier",
    (
        "提交层级来自客户端轮次提交日志，不等于出站请求或服务端实际执行层级。主面板按模型汇总全部模式、渠"
        "道和思考强度；原始值保留供追溯。全局配置也不能证明请求实际使用。平均速度按有效总输出 "
        "tokens 和对应输出时长加权。登录方式及其缺失记录不拆组，原始证据保留在历史与 "
        "CSV。历史最多保留 30 天 / 10,000 段；自动分批补读近 7 "
        "天索引中的会话，包括归档任务。索引遗漏、记录损坏或日志已清理时仍可能缺失。"
    ): (
        "Submitted tiers come from client turn-submission"
        " logs; they do not establish outbound request "
        "values or server execution tiers. The panel "
        "combines all modes, channels and reasoning "
        "efforts by model, keeping original evidence for "
        "inspection. Global configuration also does not "
        "prove request values. Average speed is total "
        "valid output tokens divided by their combined "
        "output duration. Login methods, including "
        "missing values, do not split statistics; "
        "original evidence remains in history and CSV. "
        "History is retained for up to 30 days / 10,000 "
        "records. Indexed sessions from the last 7 days, "
        "including archived tasks, are read in batches. "
        "Missing index entries, damaged records or "
        "cleared logs can leave gaps."
    ),
    "提交设置": "Submitted setting",
    "数据不足": "Insufficient data",
    "证据不足": "Insufficient evidence",
    "数据来源": "Data source",
    "数据目录不可写，请通过 --data-dir 指定可写目录。": (
        "Data directory is not writable. Use --data-dir to select a writable directory."
    ),
    "无": "None",
    "无当前快照": "No current snapshot",
    "无法写入所选文件，请检查路径与权限。": (
        "Cannot write the selected file. Check its path and permissions."
    ),
    "无法写入词脉数据目录，请检查访问权限。": (
        "Cannot write the TokenPulse data directory. Check permissions."
    ),
    "无法启动词脉": "Cannot start TokenPulse",
    "无法唯一匹配输出": "Output cannot be uniquely matched",
    "无近期状态": "No recent status",
    "日志估算": "Log estimate",
    "旧版记录": "Legacy record",
    "时长无效": "Invalid duration",
    "时间": "Time",
    "时间无效": "Invalid timestamp",
    "明确的请求异常发生时通知一次": "Notify once for each explicit request error",
    "显示当前模型和时间范围内的平均速度，有有效数据时持续显示": (
        "Show average speed for the selected model and range while valid data remains"
    ),
    "普通": "Standard",
    "普通（{evidence}）": "Standard ({evidence})",
    "普通（已清除层级覆盖）": "Standard (tier override cleared)",
    "普通（提交设置已清除覆盖）": "Standard (submitted override cleared)",
    "暂无可匹配边界": "No matching boundaries yet",
    "暂无输出记录": "No output records yet",
    "最后一轮已读样本，不代表完整历史": ("Samples read in the last poll; not the complete history"),
    "最近输出 {stamp}": "Last output {stamp}",
    "服务商": "Provider",
    "未指定（不推断继承值）": "Unspecified (inherited value not inferred)",
    "未知": "Unknown",
    "未确认": "Unconfirmed",
    "未获服务端确认": "Not confirmed by the server",
    "未计入均速": "Excluded from average",
    "未记录": "Not recorded",
    "未记录：未获取请求层级证据": "Not recorded: no request tier evidence",
    "本机只读观测；数据留在本地，不上传统计或日志": (
        "Read-only local monitoring; no statistics or logs are uploaded"
    ),
    "查看历史": "History",
    "样本覆盖": "Sample coverage",
    "模型": "Model",
    "模型未知": "Unknown model",
    "模式冲突": "Conflicting mode",
    "模式未知": "Unknown mode",
    "模式未记录或未明确指定": "Mode not recorded or not explicitly specified",
    "模式证据冲突": "Conflicting mode evidence",
    "正在检查近 7 天记录 · {completed}/{value} 个会话文件": (
        "Checking the last 7 days · Session files: {completed}/{value}"
    ),
    "正在生成": "Generating",
    "此模型没有近期状态证据": "No recent status evidence for this model",
    "此范围没有输出记录": "No outputs in this range",
    "每点是一段已完成输出；左右方向键浏览样本，Enter 查看记录，Home/End 跳至首末样本。": (
        "Each point is a completed output. Use arrow keys"
        " to browse, Enter to open a record, and Home/End"
        " to jump to the first/last sample."
    ),
    "流秒数": "Stream seconds",
    "浅色": "Light",
    "测速边界": "Timing boundaries",
    "深色": "Dark",
    "渠道": "Channel",
    "演示数据 · ": "Demo data · ",
    "演示样本": "Demo sample",
    "状态待确认": "Status unconfirmed",
    "状态未知": "Unknown status",
    "用量与输出轮次不匹配": "Usage and output turns do not match",
    "用量计数在输出期间重置": "Usage counter reset during output",
    "界面外观": "Appearance",
    "登录方式记录": "Recorded login methods",
    "目录不可用": "Directory unavailable",
    "空闲": "Idle",
    "等待可匹配的输出样本": "Waiting for matching output samples",
    "等待响应": "Waiting for response",
    "等待观测": "Waiting",
    "累计增量与本次用量不一致": "Cumulative increment differs from response usage",
    "统计方式": "Calculation",
    "缺少前一次用量快照": "Previous usage snapshot missing",
    "缺少或跳过了会话记录": "Missing or skipped session records",
    "缺少首个输出起点": "First output start missing",
    "观测详情": "Details",
    "观测详情 · 词脉": "Observation details · TokenPulse",
    "记录或输出边界不完整": "Incomplete records or output boundaries",
    "设置": "Settings",
    "设置 · 词脉": "Settings · TokenPulse",
    "设置未保存": "Settings not saved",
    "诊断日志暂不可用，仍会尝试从会话输出事件匹配测速边界。": (
        "Diagnostic logs unavailable; trying session output events for timing boundaries."
    ),
    "诊断日志起点": "Diagnostic log start",
    "词脉 · 请求异常": "TokenPulse · Request error",
    "词脉已运行": "TokenPulse is already running",
    "词脉自己的数据目录": "TokenPulse data directory",
    "说明": "Notes",
    "请从系统托盘打开现有窗口。": "Open the existing window from the system tray.",
    "请求值": "Request value",
    "请求失败或中断": "Request failed or interrupted",
    "请求失败或中断，请在 Codex 中查看详情。": (
        "Request failed or interrupted. See Codex for details."
    ),
    "请求层级": "Requested tier",
    "请求层级被客户端忽略": "Requested tier ignored by the client",
    "请求异常": "Request error",
    "请求的服务层级被客户端忽略。": ("The requested service tier was ignored by the client."),
    "请稍后重启词脉以应用新的数据目录。": (
        "Restart TokenPulse shortly to apply the new data directory."
    ),
    "请选择一个存在的 Codex 数据目录。": "Select an existing Codex data directory.",
    "跳过了损坏或过大的记录，相关样本可能不完整。": (
        "Skipped damaged or oversized records; affected samples may be incomplete."
    ),
    "输出 tokens": "Output tokens",
    "输出事件起点": "Output event start",
    "输出历史": "Output history",
    "输出历史 · TokenPulse 词脉": "Output history · TokenPulse",
    "输出缺少关联标识": "Output correlation ID missing",
    "输出边界不完整": "Incomplete output boundaries",
    "输出速度时间散点图": "Output speed over time",
    "输出项目超过解析上限": "Output item count exceeds parser limit",
    (
        "近 {range_name} · 平均输出速度（估算）\n有效总输出 tokens ÷ "
        "对应输出流累计秒数；不包含工具执行时间"
    ): (
        "Last {range_name} · Average output speed "
        "(estimated)\nValid total output tokens ÷ combined"
        " output stream seconds; excludes tool execution"
    ),
    "近 {range_name} 平均 {rate:.1f} TPS": "Last {range_name}: average {rate:.1f} TPS",
    "近 {range_name}平均输出速度，估算值": (
        "Average output speed over the last {range_name}, estimated"
    ),
    "还没有历史样本": "No history samples yet",
    "还没有找到任务。运行一次本机 Codex，或在设置中选择数据目录。": (
        "No tasks found. Run Codex locally or select its data directory in Settings."
    ),
    "退出词脉": "Quit TokenPulse",
    "选择 Codex 数据目录": "Select Codex data directory",
    "选择目录": "Browse",
    "部分会话文件暂不可读，恢复后将自动继续。": (
        "Some session files are unreadable; collection will resume automatically."
    ),
    "采集仍在退出": "Collector is still stopping",
    "采集暂时失败，正在重试。可检查数据目录和访问权限。": (
        "Collection failed; retrying. Check the data directory and permissions."
    ),
    "采集状态": "Collection status",
    "首项为工具调用，完整生成起点未确认": (
        "First item is a tool call; full generation start unconfirmed"
    ),
    "首项仅有完成时刻，缺少生成起点": (
        "First item has only a completion time; generation start missing"
    ),
    "首项起止时间重合，完整生成起点未确认": (
        "First item boundaries coincide; full generation start unconfirmed"
    ),
    "首项起止时间重合，日志通知未提供更早起点": (
        "First item boundaries coincide; logs provide no earlier start"
    ),
    "（已合并统计）": " (combined in statistics)",
    "；仅供追溯，不参与分组": "; for inspection only, not used for grouping",
    "界面语言": "Language",
    "跟随系统": "System default",
    "选择语言后立即预览；保存以保留，取消则恢复。": (
        "Preview language changes immediately. Save to keep them, or Cancel to restore."
    ),
    "界面语言（覆盖已保存设置，仅本次运行有效）": "Language override for this run only",
}
