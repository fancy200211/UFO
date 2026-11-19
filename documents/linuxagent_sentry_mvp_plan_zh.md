# LinuxAgent 接入 Sentry：MVP 开发方案（修订版）

本版只设计 LinuxAgent 的故障检测、诊断注入、恢复验证，以及验证成功后的经验总结、JSON playbook 落盘和标签检索。参考相邻 `Sentry/` 的 taxonomy、detector、verifier、updater 和 playbook；保留现有 Agent 主流程，不引入向量库、检索模型或自动动作回放。

**开发范围：只允许在 `UFO3` 仓库的 `sentry_version` 分支开发。修改前检查分支；不在其他分支写代码，不重置或覆盖已有未提交修改。**

**允许开发 Agent 自行编写和运行测试，但禁止任何真实模型调用，包括模型初始化探测、备用模型、真实 API 集成测试。测试必须 mock 模型入口；本功能的自动化测试使用假的 dispatcher 和观测数据即可。**

## 1. 调度规则：以本节为准

**硬故障最高优先级：程序检测、程序生成固定提示、下一轮纠错并由程序检验，全程不调用 Sentry 模型。发生硬故障的轮次不加入 `recent_steps`，不增加软恢复计数；已有软故障置为 `pending`。硬故障解除后恢复原软故障，保留已累计步数，不从零开始。**

**同时只有一个活动软故障；恢复期间不检测其他新软故障，不重复注入已有软故障建议。只有没有硬故障且不处于恢复期间时，才调用 Sentry 模型检测新软故障。**

**规划模型提出合法 `FINISH` 时，通过硬故障检查就直接放行。即使软故障尚未恢复或恰好到达第五个有效轮次，也不调用软故障检测／恢复验证，不拦截结束。待验证事件只记为 `incomplete`，不能宣称已恢复。**

```text
首轮：由 Sentry 观测 helper 初始化截图和桌面信息
每轮：复用观测缓存 → LinuxAgent 规划 → 程序检查响应与动作协议
    → 无效则跳过工具执行；合法则沿用原工具执行
    → SentryEvaluation（位于工具执行后、记忆更新前）
        1. 更新执行后的观测缓存
        2. 有硬故障：挂起软恢复、生成下一轮固定提示、不入窗口、不调用 Sentry LLM
        3. 无硬故障且 FINISH：直接结束
        4. 无硬故障且继续：恢复 pending 软事件、加入紧凑轨迹
           - 无活动软故障：文本检测；发现故障则按标签检索，诊断＋匹配经验排队一次注入
           - 有活动软故障：累计有效轮次，满五轮才验证（文本＋两张截图）
    → FailureSummary（仅本轮验证 resolved 时：总结经验 → 写入 playbook；其他情况跳过）
    → 原有记忆更新与状态迁移 → 下一轮
```

硬故障时的 `CONTINUE` 是下一轮重试，不在当前轮内建立重规划循环。原有 `FAIL`、步数上限、外部取消和基础设施异常处理保留；硬故障轮次仍消耗原有 session 步数，只是不消耗 Sentry 的五步恢复窗口。

计数示例：第 12 轮发现软故障；第 13、14 轮为恢复第 1、2 步；第 15 轮发生硬故障，挂起且不入窗口；第 16 轮纠错并通过硬检查，则恢复并计为第 3 步；第 17、18 轮为第 4、5 步，在第 18 轮末验证。若第 16 轮仍有硬故障，继续挂起。若任一轮合法 `FINISH`，立即结束，不再凑五步。

## 2. 职责与共享状态

`SentryAgent` 是新建的轻量旁路 Agent，提供 `async detect(...)`、`async verify(...)`、`async summarize_failure(...)`，通过现有 LLM 层调用模型；不执行任务工具、不注册 AgentFactory 路由。固定硬故障检查和提示由 `SentryHardGuard` 完成；playbook 的读写、检索由 `SentryPlaybook` 程序模块完成，二者不调用模型。

新增 `ContextNames.SENTRY_STATE`（现有源码没有该字段），值是当前任务共享 Context 中的字典。Processor 每轮重新创建，所以跨轮状态不能只放在其 local context。

| 字段 | 内容 |
|---|---|
| `task_key` | session ID、round ID、request；任务变化时重建状态 |
| `recent_steps` | 最近最多五个通过硬检查的继续执行轮次，存储下节定义的紧凑文本记录 |
| `active_soft` | 唯一软故障：程序生成的 event_id、原始类别／标签／证据、当前建议、首次检索的经验快照、`recovering/pending`、`recovery_count`、触发轮的紧凑记录 trigger_step 和触发截图 |
| `active_hard` | 当前硬故障的类型、错误详情、必要的非法输出片段；只供程序处理，不进入软故障轨迹 |
| `pending_guidance` | 下一轮使用一次的提示及 hard/soft 类型；规划请求使用后清空 |
| `latest_observation` | 最新整组观测：原图、标注图、UI 树、控件、窗口信息和采集轮次，供下一轮 LinuxAgent 复用 |
| `last_verification` | `resolved/unresolved/unverified/incomplete` 及简短理由，仅保存在内存 |

不再复制一套完整 `recovery_steps`：只有有效轮次会进入 `recent_steps`，当 `recovery_count == 5` 时，它恰好就是本次干预后的五步轨迹。硬故障挂起期间窗口和计数都不变。诊断不写入 LinuxAgent 普通 memory/blackboard，不把整个 SENTRY_STATE 交给模型。

软故障验证成功时，**先深拷贝生成本轮只读 `verified_recovery` 快照，再清空活动事件**。快照通过 `ProcessingResult.data` 传入当前 processor 的 local context，交给紧接着的 FailureSummary；包含 event_id、任务目标、原始故障、trigger_step、当前建议、首次检索经验、最后五步轨迹和验证结论／证据，不含图片。trigger_step 仅额外保留触发轮的一条紧凑记录，避免窗口滑动后丢失当时的实际观测。不能用持久的 `last_verification == resolved` 触发总结，否则后续轮次会重复落盘。

未恢复则保留原始故障及触发截图，更新建议，排队一次新提示，计数归零重新观察五个有效轮次。硬恢复结束时不重新注入已消费的软故障提示。Playbook 跨任务保留，不放入 SENTRY_STATE；状态中只保留当前事件最多 PLAYBOOK_TOP_K 条检索结果（默认 3），不加载整个经验库。

## 3. 一步轨迹具体保存什么

**`reasoning` 直接来自规划响应 `parsed_response.thought`：即 LinuxAgent 在 JSON 中显式写出的行动理由。不是模型内部隐藏推理，不读取 provider 的 reasoning 通道，也不额外调用模型生成摘要。** 来源是 `ufo/agents/processors/schemas/response_schema.py::AppAgentResponse.thought`。

借鉴 MemoryItem 的字段筛选和动作文本表示，采用程序压缩；现有 MemoryItem 并没有自动语义摘要或结果长度控制，不能原样复制完整 memory item。

| 字段 | 数据来源和处理 |
|---|---|
| `step_id` | 当前 session_step，保留原编号；硬故障轮次被排除后编号可以不连续 |
| `reasoning` | `parsed_response.thought`，默认最多 600 字符 |
| `action.raw` | 实际规划的工具名、参数、action.status 的紧凑 JSON 文本；多工具仍属于同一步，默认最多 1200 字符 |
| `observation` | 程序拼接的真实工具返回状态、exit_code、错误及 stdout/stderr 摘录，加上执行后 active_window 和有限 UI 文本；默认最多 2000 字符 |

`observation` 必须区分成功、失败、未执行，不能只取成功动作，也不能用规划模型声称的 `observation` 替代真实执行证据。普通非零退出码或任务操作失败可以进入窗口，由软故障检测判断；**硬故障轮次整体排除**。

三个长度上限使用模块常量即可；超长文本保留首尾并写英文标记 `[truncated; original_chars=N]`。优先保留工具名、错误、退出码，UI 部分只选当前窗口及少量控件 name/role/state，不传完整 UI 树。压缩只作用于 Sentry 的输入副本，不截断实际工具参数、普通记忆或最终任务结果。action.raw 是供阅读的文本，不再解析执行。

轨迹不保存完整规划 JSON、完整计划、所有工具输出或图片 base64。图片独立缓存：检测消息不包含任何图片；验证消息只包含以下两张**原图**，均带 step_id：

1. 首次触发该软故障时，在工具执行后采集的截图；
2. 本次第五个有效恢复轮次结束后的当前截图。

标注图和完整控件信息继续供 LinuxAgent 使用，不送入本版 Sentry 检测或验证。无须保留五轮历史截图；未恢复再次观察时仍使用原始故障触发图作为对照。

## 4. 硬故障：程序检查与下一步修复

复用 JSON 解码、Pydantic 响应模型和现有工具 schema，仅覆盖少量明确错误：

| 程序错误类型 | 检查位置 | 固定建议要点 |
|---|---|---|
| `invalid_json` | Linux LLM 返回原文后的 JSON 解码 | 完整、闭合、正确转义的 JSON；无 markdown；缩短冗余文本 |
| `invalid_response` | 响应结构校验 | 指明缺失／类型错误字段，完整 LinuxAgent 响应，合法 action/status |
| `invalid_tool_call` | 执行前工具名和参数 schema 校验 | 使用已注册工具及正确必需参数、类型 |
| `invalid_target` | 可明确验证的控件目标／明确的环境动作合法性拒绝 | 使用最新观测中的有效目标，不沿用无效控件 ID |

`invalid_response` 至少检查 action 非空、status 属于现有三种值，以及终态所需 result，避免现有宽松默认值把缺字段当作正常结束。合法无动作 FINISH（function 为空）允许通过。工具参数校验可复用现有 jsonschema 依赖；只检查规划模型负责的参数，不把客户端注入的认证字段当作模型缺参。不要新增业务结果判定器或把所有工具异常都判为硬故障。

执行前已知无效则跳过工具，不交给 LLM 判断。环境明确拒绝非法动作时，转换为同一类程序错误；连接超时、服务不可用、权限／命令业务失败不凭失败状态强行归为动作协议错误，保留既有异常处理与工具结果。

第 t 轮发现硬故障 → 固定提示进入 pending_guidance → 第 t+1 轮 LinuxAgent 纠错 → 使用相同程序规则检验。需要执行的修正动作按原流程执行并检查明确的合法性拒绝；若仍有硬故障继续下一轮，若已通过则清除 active_hard、恢复 pending 软事件，并将当前非终态轮次作为有效恢复步。合法 FINISH 直接放行。

JSON 解析失败必须保留原始响应／错误供固定提示使用，返回阶段 `success=True`、状态 `CONTINUE`，不能触发错误 middleware 将子任务标成 FAIL；Action 阶段跳过执行，Linux 专用 MemoryUpdate 阶段跳过依赖 parsed_response 的普通记忆更新。其他有合法 parsed_response 的失败动作仍沿用原记忆路径，明确记录未执行／失败。

## 5. 全英文 Prompt 协议

模板、固定提示、标签定义、示例 JSON 文本均用英文；运行时填入的用户请求／工具输出保留原语言。软故障分类沿用 Sentry：`progress_failure` 包含 repetition_or_looping、planning_stall、over_exploration、termination_miscalibration；`reasoning_grounding_failure` 包含 hallucination、objective_drift、reasoning_action_mismatch。保留标签全集，但本版 FINISH 放行，因此不依靠 Sentry 拦截过早完成。

**检测模板：只有文本，无图片。**

```text
You are Sentry, a runtime failure-detection assistant for LinuxAgent.
The program has already checked action validity. Do not classify hard failures.
Inspect the recent compact trajectory for an ongoing soft failure and provide
concise recovery advice. Do not execute tools or judge final task success.
The reasoning field is LinuxAgent's reported rationale, not verified evidence.
Use actual tool results and observations as evidence. Truncated content is incomplete.

Task objective: {request}
Failure definitions: {soft_failure_taxonomy}
Recent eligible steps (up to five): {recent_steps}

Return only JSON with failure_type, labels, evidence, diagnosis, and advice.
failure_type must be no_failure, progress_failure, or reasoning_grounding_failure.
Use canonical labels and cite step_id in evidence. For no_failure, use empty
labels, evidence, and advice. A nonzero exit code alone is not a soft failure.
```

```json
{"failure_type":"progress_failure","labels":["repetition_or_looping"],"evidence":[{"step_id":12,"detail":"The same query produced no new information."}],"diagnosis":"The agent is repeating an unproductive query.","advice":["Use the existing results to inspect a concrete candidate."]}
```

**验证模板：紧凑五步文本＋两张原图，无其他历史图片。**

```text
You are Sentry, a recovery-verification assistant for LinuxAgent.
Determine whether the specific soft failure that triggered intervention has been
resolved. Incomplete overall task progress does not by itself mean failed recovery.

Task objective: {request}
Original failure, labels, and evidence: {original_failure}
Recovery advice given for this attempt: {recovery_advice}
Five eligible post-intervention steps: {recent_steps}
Label-specific resolution criteria: {resolution_criteria}

Two labeled images follow: the desktop at the original failure trigger and the
current desktop after five eligible recovery steps. Hard-failure steps were excluded;
step IDs may be nonconsecutive. Compare both images together with the textual evidence.
Do not infer unobserved intermediate changes or treat a changed screenshot alone
as proof of recovery. The reasoning field is a reported rationale, not ground truth.

Return only JSON with recovery_status (resolved or unresolved), evidence,
diagnosis, and advice. If unresolved, update the advice for the same failure.
If resolved, advice must be empty.
```

```json
{"recovery_status":"unresolved","evidence":[{"step_id":18,"detail":"The same unproductive query continues."}],"diagnosis":"The original loop persists.","advice":["Stop repeating the query and inspect the selected candidate."]}
```

通过多模态内容块依次发送 `Failure trigger — step {trigger_step_id}`、触发原图、`Current state — step {current_step_id}`、当前原图；不把图片编码塞进 JSON 文本。标签恢复标准取自 `Sentry/Sentry/taxonomy.py::resolution_criteria_for`。

**硬故障固定模板：程序填充，不调用 Sentry 模型。**

```text
[Sentry runtime feedback — programmatic action-validity check]
Sentry is a runtime monitor assisting LinuxAgent. This feedback was generated
by deterministic validation, not by another task-planning model.
Error type: {error_type}
Validation error: {error_detail}
Invalid output or action excerpt: {invalid_excerpt}
Required response format or relevant tool schema: {required_format}

Continue the original task. Correct the reported issue and return a COMPLETE
LinuxAgent JSON response, not only an action or a JSON fragment. Use correct field
types, escaping, and closing delimiters. Keep observation, thought, and plan concise;
avoid repeated output while preserving required task results. Output no markdown.
```

格式／长度建议是固定模板，不据文本长度猜测 provider 确定发生了截断，不自动修改模型 token 预算。

**软故障建议注入模板：每次干预仅使用一次。**

```text
[Sentry runtime feedback — soft-failure recovery]
Sentry is a runtime diagnostic assistant observing LinuxAgent's recent actions
and results. It provides recovery suggestions; LinuxAgent remains responsible
for planning and executing the original task.
Failure type and labels: {failure_type_and_labels}
Evidence: {evidence}
Diagnosis: {diagnosis}
Recovery advice: {advice}
Relevant verified recovery experiences: {retrieved_experiences}

These experiences come from other episodes with overlapping failure labels.
They are reusable suggestions, not proof of the current cause or executable scripts.
Use an experience only when its trigger pattern and applicability fit the current
evidence. Adapt its repair steps to the current task and observation; disregard
inapplicable experience. Verify progress using its success check. Never copy old
control IDs, coordinates, paths, or commands without grounding them in this task.

Use the current observation to choose the next corrective step. Continue the
original task and follow the existing COMPLETE LinuxAgent JSON response format.
```

新诊断与检索经验合并排队一次，下一轮规划发送后清空 pending_guidance；没有匹配记录时，省略经验段及其使用说明。后续恢复轮次不重复注入身份说明、旧建议或经验。验证未恢复产生的新建议可再注入一次，不重新检索或重复附带旧经验。硬故障修复优先；软故障 pending 期间只注入本次硬提示，不把旧软建议拼回 Prompt。

**FailureSummary 模板：只有文本，仅在恢复验证成功后调用一次。**

```text
You are Sentry's recovery-experience summarizer. Convert one verified recovery
episode into a concise, reusable playbook entry for a future LinuxAgent.
The specific soft failure was verified resolved; this does not establish overall
task completion, a unique root cause, or that every suggested action was useful.

Task objective (context only): {request}
Original failure type, labels, diagnosis, and trigger evidence: {original_failure}
Compact step and actual observation at the failure trigger: {trigger_step}
Recovery advice for the final attempt: {recovery_advice}
Previously retrieved experiences (suggestions, not evidence): {retrieved_experiences}
Final five eligible executed steps and observations: {recent_steps}
Successful verification result and supporting evidence: {verification}

Build the lesson from the observed transition from failure to recovery:
1. Describe the recognizable failure pattern and the conditions under which the
   repair applies. Preserve conditions that materially affect the choice of repair.
2. Separate proposed advice, reported rationale, executed actions, and observed
   results. Include only repair steps actually performed and supported by results.
   Do not turn unused advice or a retrieved lesson into newly verified experience.
3. Abstract the concrete repair into a practical principle and one to four ordered
   steps using semantic roles, such as "the selected search result" or "the target
   settings panel". Keep an operation-specific detail only when essential to repair.
   Replace episode-specific names, IDs, coordinates, paths, and URLs with semantic
   roles. Preserve stable tool or option names when essential. Never include secrets.
   Do not save a command transcript, a full task plan, or vague advice such as
   "reason more carefully". Describe what to change and how to observe improvement.
4. State a concrete success check for escaping this failure, not for completing
   the entire task. Cite supplied step IDs and the observed change supporting it.
   If several actions preceded recovery, describe the supported sequence without
   claiming that one action alone caused recovery.
5. Evidence may be truncated and step IDs may have gaps. Earlier failed recovery
   windows and hard-failure steps are not supplied. Do not reconstruct missing
   events or claim that this record explains the entire recovery history.

Return only a JSON object with the fields in the supplied output schema.
Copy failure_type and labels exactly from the original failure. Write descriptive
fields in concise English, aiming for at most 1,800 characters of descriptive text.
If the supplied evidence cannot support an actionable, reusable repair, return
JSON null. Do not invent a lesson merely because verification reported resolved.
```

将 `SentryExperience` 的输出 schema 一并发送；结构及例子如下。`trigger_pattern` 描述可识别现象，`applicability` 限定适用条件，`repair_principle` 表达抽象修复方法，`repair_steps` 给出可操作步骤，`success_check` 指明恢复信号；`evidence` 保留少量事实依据，不存完整轨迹。

```json
{
  "failure_type": "progress_failure",
  "labels": ["repetition_or_looping"],
  "trigger_pattern": "Equivalent searches repeatedly return the same candidates without inspecting any candidate.",
  "applicability": "The current results already contain a plausible candidate that can be inspected directly.",
  "repair_principle": "Resolve uncertainty by inspecting an existing candidate before repeating a broad search.",
  "repair_steps": [
    "Select a candidate using the task's distinguishing attributes.",
    "Open its details and compare the observed attributes with the task requirements."
  ],
  "success_check": "Candidate inspection yields new task-relevant evidence and the equivalent-search loop stops.",
  "evidence": {
    "step_ids": [16, 17, 18],
    "observed_change": "After repeated searches, the agent opened a candidate, checked its attributes, and continued with new evidence."
  }
}
```

此例只展示结构，不作为所有事件的固定答案。总结阶段不重新传图片；复用验证模型给出的文本证据，不额外调用模型压缩轨迹或审查经验质量。仅做 JSON／schema 校验和原始类别、标签一致性检查；`null`、解析失败或字段不合法均跳过本次写入，不改变已经确认的恢复状态。

## 6. FailureSummary、落盘与检索

**写入链路：** `verify → resolved → verified_recovery → FailureSummaryStrategy → SentryAgent.summarize_failure → SentryPlaybook.append`。本轮直接 await 完成，不新建后台队列；没有 verified_recovery 就成功跳过阶段。`unresolved/unverified/incomplete`、硬故障恢复和直接 FINISH 均不生成经验。连续几次五步验证后才成功时，只总结最后一个成功窗口及原始故障，不额外保存所有失败窗口。

**文件与生命周期：** 默认 `UFO3/data/sentry/playbook.json`，通过 `SENTRY_AGENT.PLAYBOOK_PATH` 配置；相对路径以 UFO3 根目录解析。文件结构为 `{"version":1,"entries":[]}`，每个 entry 是上面的 JSON 加上程序填写的 `entry_id`（沿用 event_id）和 `created_at`（UTC 时间）。同一路径供本进程 LinuxAgent 的后续任务共享，session reset 不删除文件；不保存截图、原始响应或完整任务日志。文件不存在时视为空库，首次成功写入时创建。

`SentryPlaybook` 只提供 `retrieve(failure_type, labels, top_k)` 和 `append(entry)`：每次按需读取，不引入向量索引或常驻全库缓存。写入采用“读取现有 JSON → 追加 entry → 同目录临时文件 → 原子替换”，不是直接向 JSON 文件尾拼接对象；同进程按文件路径加锁保护整个读改写，已有相同 entry_id 时不重复追加。本版不协调多个进程共享写入同一文件，多进程部署须配置不同路径。文件损坏／不可读时报告现有日志并跳过检索或写入，不用空库覆盖；总结或落盘失败不让主任务 FAIL，不回滚恢复状态、不重新计数，也不建立模型重试循环。

**检索链路：** 仅新软故障触发时，在取得 Sentry 诊断之后、构造下一轮提示之前执行。先筛选相同 `failure_type`，再保留至少一个相同 canonical label 的记录；按标签交集数量降序，同分按文件内追加顺序从新到旧，取 `SENTRY_AGENT.PLAYBOOK_TOP_K`，默认 3。无标签交集就返回空，不跨标签兜底；这点比参考 `Sentry/playbook.py` 的同类别最近记录兜底更保守。检索不调用 LLM，不做 embedding、语义重排或经验合并。

检索读取失败也返回空经验；仍保留本轮新诊断、建议和软恢复状态，不能因读库异常丢弃 Sentry 已经生成的干预。

注入只包含每条经验的 `trigger_pattern/applicability/repair_principle/repair_steps/success_check`；不注入存储元数据、旧任务证据或完整 JSON 库。标签相同只代表候选经验，不能保证故障原因或环境相同，因此由已有 LinuxAgent 规划调用判断适用性并生成当前工具动作，不增加第二次选例调用。经验用于减少重复推导，不直接跳过规划或回放历史命令。

## 7. 最小接入与修改位置

以下路径均相对 `UFO3/`，定位以类／函数名为准。

| 文件与位置 | 修改内容 |
|---|---|
| 新增 `ufo/agents/agent/sentry_agent.py` | 轻量 detect/verify/summarize_failure；检测／总结仅文本，验证两张图，使用现有 LLM 层，在线程执行同步调用 |
| 新增 `ufo/agents/processors/schemas/sentry_schema.py` | Step 四类字段、软故障分类／响应、程序硬错误、SentryExperience／playbook entry 结构；复用参考 taxonomy 的定义和恢复标准 |
| 新增 `ufo/agents/processors/strategies/sentry_hard_guard.py` | 确定性响应／动作合法性检查、硬故障分类和固定提示；不依赖模型 |
| 新增 `ufo/agents/processors/strategies/sentry_evaluation_strategy.py` | SentryEvaluation、紧凑记录、状态／计数、检测／验证、检索与一次性提示；resolved 时输出本轮 verified_recovery；统一提供观测采集 helper |
| 新增 `ufo/agents/processors/strategies/failure_summary_strategy.py::FailureSummaryStrategy.execute` | 消费本轮 verified_recovery，调用总结并写入 playbook；无快照／无有效总结时成功跳过，不改变任务状态 |
| 新增 `ufo/experience/sentry_playbook.py::SentryPlaybook` | 第 6 节的 JSON 文件加载、按标签检索、追加与原子替换；独立于现有 ExperienceSummarizer 和向量 RAG |
| 新增 `ufo/prompter/sentry_prompter.py`、`ufo/prompts/sentry.yaml` | 本文五套英文模板、总结输出 schema 和英文标签定义；检测／总结零图，验证仅两图 |
| `ufo/agents/processors/context/processing_context.py::ProcessingPhase` | 枚举顺序 ACTION_EXECUTION → SENTRY_EVALUATION → FAILURE_SUMMARY → MEMORY_UPDATE |
| `ufo/module/context.py::ContextNames/default_value/type` | 新增 SENTRY_STATE，默认独立空字典 |
| `ufo/agents/agent/customized_agent.py::LinuxAgent.__init__/message_constructor` | 启用时创建 SentryAgent 和轻量 SentryPlaybook 文件接口；透传可选 sentry_guidance，不引入独立 Agent 状态机 |
| `ufo/agents/processors/customized/customized_agent_processor.py::LinuxAgentProcessor._setup_strategies` | 只在 Linux 且启用时注册 SentryEvaluation、FailureSummary 两个阶段，改用 LinuxMemoryUpdateStrategy；终止／步数上限清理待恢复状态，未验证则 incomplete |
| `ufo/agents/processors/strategies/linux_agent_strategy.py::LinuxDesktopCollectionStrategy.execute` | 开启 Sentry 时读取 latest_observation；首轮无缓存才调用 Sentry 观测 helper。读取缓存后维持原有日志字段／保存路径，不再执行每轮规划前截图 |
| 同文件 `LinuxLLMInteractionStrategy.execute/_get_llm_response` | 从共享状态取一次性提示；先取 raw_response/cost 再单独捕获解码／结构错误；调用 HardGuard，避免父类包装异常丢失原文；消费提示后清空 |
| 同文件 `LinuxActionExecutionStrategy.execute` | 执行前程序检查工具／参数／目标；已知硬错误跳过工具，状态 CONTINUE，记录未执行；执行后汇总明确的合法性拒绝，不让硬故障走 FAIL middleware |
| 同文件新增 `LinuxMemoryUpdateStrategy` | parsed_response 缺失时成功跳过，正常路径委托 AppMemoryUpdateStrategy；依赖声明允许解析失败分支，不改公共记忆策略 |
| `ufo/prompter/customized/linux_agent_prompter.py::user_prompt_construction/user_content_construction`；`ufo/prompts/third_party/linux_agent.yaml` | 增加可选 sentry_guidance，仅非空时加入；使用缓存原图／标注图／UI 信息；更新“invalid JSON 直接 crash”和每轮重复截图的旧描述 |
| `ufo/module/sessions/platform_session.py::LinuxBaseSession.reset` | 清空 SENTRY_STATE，不清除持久 playbook；task_key 改变时亦重新初始化，不能沿用上个任务的截图 |
| `config/ufo/agents.yaml`、`config/config_loader.py::_apply_legacy_transforms`、`config/config_schemas.py::UFOConfig/from_dict` | 增加 SENTRY_AGENT、ENABLED、PROMPT、PLAYBOOK_PATH、PLAYBOOK_TOP_K；默认继承 APP_AGENT 连接配置，可覆盖；补齐后沿用 API URL 规范化；验证支持视觉模型，总结复用同一模型路由 |
| `ufo/llm/__init__.py::AgentType`、`ufo/llm/config_helper.py::get_agent_config`、`ufo/llm/llm_call.py::get_completions` | 独立 SENTRY 命名空间及配置路由，不套用 EvaluationAgent 的完成评估 schema |
| `.gitignore` | 忽略默认运行数据目录 `/data/sentry/`，经验文件不作为源码提交 |
| 新增 `tests/test_linux_sentry.py`、`tests/test_sentry_playbook.py` | 下节的 mock 测试；临时目录验证真实 JSON 文件读写，不访问真实模型 |

观测 helper 只采集并更新缓存，不调用模型或新增落盘。每轮末尾更新整组截图／控件，下一轮直接复用，不能把新截图配旧控件 ID；初始观测也是同一 helper。开启 Sentry 时停止原 DATA_COLLECTION 的重复采集，关闭时保留原流程。验证输入的触发图单独保留，不随 latest_observation 更新而丢失。

Sentry 新阶段及硬错误处理返回 `success=True` 表示流水线继续，不表示任务成功；仅设置 fail_fast=False 仍可能触发 middleware 并产生 FAIL。解析失败或程序拦截时同时清除本轮伪终态 result，避免旧结果被上报；合法 FINISH 的结果按原流程返回。

Sentry 调用／响应解析失败不改变主任务为 FAIL、不冒充无故障或已恢复；检测失败本轮不注入，验证失败记为 unverified，保留原软故障但重新观察五个有效轮次，不重复注入旧建议。缺少触发／当前截图时不伪造双图验证，按 unverified 处理。每个新阶段保留一个异常边界即可，不新增重试器、熔断或评分阈值。检测、验证和总结的 cost 均累加到本轮 llm_cost，不覆盖规划成本。

## 8. 开发测试与交付

允许运行本功能相关 mock 测试，覆盖：

- 格式错误／非法工具：不执行无效动作、不调用 Sentry LLM、不写 recent_steps；下一轮使用固定提示并由程序检验。
- 软恢复已计两步后出现硬故障：状态 pending、窗口不变；纠错成功后从第三步继续，未重复注入旧软提示。
- 满五个有效轮次仅验证一次；恢复成功清空，未恢复排队新建议并重新计数。
- FINISH 通过硬检查后立即放行，包括恢复未满五步和恰好第五步；Sentry LLM 调用次数为零。
- 检测消息无 image_url；验证恰好两图且分别来自原始触发轮和当前轮；文本限长标记和失败结果保留正确。
- 首轮初始化一次观测；之后每轮末尾采集，下一轮 DATA_COLLECTION 不再调用观测工具；新任务不复用旧缓存。
- 提示只消费一次；解析失败跳过普通 memory；Sentry 异常不使主任务 FAIL；关闭功能时维持原流程。
- 只有本轮 resolved 快照触发一次总结和写入；后续轮次不因 last_verification 重复触发。硬故障恢复、unresolved、unverified、incomplete 和直接 FINISH 不写入。
- 总结请求仅文本，包含原始故障、最后五步和验证证据；`null`／非法 JSON／标签改变／模型异常均跳过写入，保持已恢复状态，成本累加正确。
- 临时目录测试首次建库、追加后重新加载、相同 event_id 不重复写、session reset 后仍可检索；同进程并发追加不丢记录，替换失败／文件损坏不覆盖原文件。
- 标签检索只匹配同类别且有标签交集的记录，交集数量／新旧排序／top_k 正确；无匹配保留 Sentry 原建议。经验随新故障提示仅注入一次，不在后续恢复轮次重复发送。

模型调用、初始化探测、备用模型和 dispatcher 都在测试中替换为 fake/mock，禁止真实模型请求。不改公共 Processor 执行算法、其他平台行为或 AIP 结果格式；新增持久数据仅为 playbook，不建立独立故障日志系统。交付列出变更文件、实际运行的 mock 测试和结果，明确没有调用真实模型。
