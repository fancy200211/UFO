UFO3 纠错、排故与恢复机制调查

调查日期：2026-11-07（Asia/Shanghai）。调查对象：本地 UFO3 工作树，HEAD 为 `eae3c0a`，包含已有的未提交修改。覆盖 Windows HostAgent/AppAgent、LinuxAgent、MobileAgent、Galaxy 编排以及 MCP/AIP 工具和通信链路。

本文关注系统已经具备哪些机制：如何让模型发现执行偏差，如何捕获工具或连接故障，如何调整计划、重试、降级、重连，以及何时结束任务、反馈结果或交由用户决定。源码中的机制、当前配置是否启用和实际作用范围分别说明；本文不涉及系统修复。

**1. 机制分布：模型判断与程序处理共同参与**

UFO3 的纠错逻辑分散在模型提示、动作执行、通信、任务编排和会话管理等层次。模型主要负责理解当前状态、解释错误和选择下一步；程序主要负责返回错误信息、限制执行、恢复连接、维护任务状态和记录过程。

| 层次 | 已有机制 | 主要处理的问题 | 决策主体 |
|---|---|---|---|
| 模型决策 | 对比截图、检查动作历史、修改 plan、选择 FAIL | 操作没有生效、走错步骤、任务无法完成 | 模型依据 prompt 和执行反馈判断 |
| 工具与处理流水线 | 错误结果包装、批内失败跳过、阶段异常处理、截图降级 | 工具报错、数据不完整、采集异常 | 程序执行预设规则，再视路径将反馈交给模型 |
| 模型调用 | 请求超时、有限尝试、备用引擎路径 | 模型服务不可用、响应无法解析 | 程序，受配置控制 |
| 设备通信 | 心跳、断连清理、有限重连 | 网络中断、客户端掉线、暂时无法连接 | 程序 |
| Galaxy 编排 | 任务完成/失败事件驱动重规划、补救任务、DAG 校验、编辑回滚 | 子任务失败、设备能力不符、后续任务需要调整 | 模型制定修改，程序校验并调度 |
| 会话与交互 | 步数上限、失败终态、取消入口、PENDING、CONFIRM | 执行过长、无法继续、需要用户信息或确认 | 模型、程序或用户，取决于触发入口 |
| 复盘 | 轨迹日志、失败说明、事后评价、可选经验提炼 | 定位错误步骤、判断完成情况、积累后续参考 | 程序记录，模型总结或评价 |

这里需要区分几种“恢复”：恢复通信、重新取得有效观察、重新规划后续任务、恢复待确认动作，各自对应不同代码路径。连接重新建立，并不自动意味着原任务从断点继续；模型结束规划，也不自动意味着远端所有动作已经停止。

**2. 模型如何发现走偏并调整行为**

模型侧纠偏首先依赖每步反馈。AppAgent 的 prompt 要求参考此前计划，但允许根据当前观察修改计划；还要求比较当前截图与上一张截图，检查历史动作是否已经生效，删除冗余步骤、补充遗漏步骤，避免重复执行已完成的动作。它也要求观察实际结果后再判断 FINISH，避免把“已经发出动作”直接当成“已经完成任务”。

当任务无法完成时，prompt 提供 FAIL 作为退出选项：应用状态不合适、权限不足、应用崩溃或反复无效操作，都可以成为失败原因。模型需要在 result/results 中解释任务结果，为上层规划器或用户提供上下文。

| Agent | 提供给模型的诊断线索 | 已有纠偏引导 |
|---|---|---|
| Windows AppAgent | 当前与上一步截图、控件信息、动作历史、工具结果、已有计划 | 对照实际界面修订计划；避免重复；无法完成或多次操作无效时 FAIL |
| Windows HostAgent | 整体用户请求、子任务信息、黑板历史、当前应用信息 | 分解和调整后续任务；信息不足时可以通过 PENDING 提问 |
| LinuxAgent | 上一步命令及结果、stdout/stderr/退出码、计划和黑板历史 | 解释命令错误；避免重复无效命令；状态无效、权限不足或重复失败时 FAIL |
| MobileAgent | 截图、控件、已安装应用、动作历史 | 界面加载时使用 wait；多次操作后屏幕不变时换方法或 FAIL；识别应用崩溃 |
| Galaxy ConstellationAgent | 设备信息、任务图、任务状态及结果 | 能力不足时增加其他设备上的任务；可恢复失败时新增补救任务；无法安全继续时 FAIL |

源码依据：[AppAgent prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/share/base/app_agent.yaml:7)、[HostAgent prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/share/base/host_agent.yaml:38)、[LinuxAgent prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/third_party/linux_agent.yaml:12)、[MobileAgent prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/third_party/mobile_agent.yaml:44)、[Galaxy 补救任务引导](/home/zhou/GUI-Agent/UFO3/galaxy/prompts/constellation/share/constellation_editing.yaml:153)。

这些属于模型判断机制。比如 Galaxy prompt 用“失败次数过多，例如 ≥3”说明何时选择 FAIL，但没有据此形成统一的程序计数阈值。代码中的 `count_repeat_times()` 可以统计连续相同 function/arguments，主要用于动作记录；本次没有发现它直接触发强制退出或暂停的执行分支。

依据：[Galaxy 失败条件](/home/zhou/GUI-Agent/UFO3/galaxy/prompts/constellation/share/constellation_editing.yaml:123)、[重复动作计数](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/schemas/actions.py:243)。

**3. 执行错误如何返回模型，形成下一步纠错依据**

普通工具执行路径会把返回值和错误整理成统一的动作记录。其主要流向是：

```text
工具返回值或调用异常
  → MCP CallToolResult / AIP Result
  → 动作文本：Action、Status、Error、Result
  → Agent memory 与 blackboard 中的执行历史
  → 下一步 prompt
  → 模型选择继续、修改操作或报告失败
```

`ActionCommandInfo.to_representation()` 会写入动作、目标、状态、错误和结果；下一步的 `_get_last_success_actions()` 实际读取的是 `action_representation`，因此这个函数虽然带有 success 字样，当前实现并不只向模型提供成功动作。历史还会按 `HISTORY_KEYS` 写入黑板，支持模型结合多步过程判断。

当 dispatcher 遇到调用异常或超时时，`generate_error_results()` 会生成 FAILURE 结果，并在错误文本中提示重试或使用其他命令。这使工具故障可以成为下一次决策的输入，而不只停留在后台日志里。

依据：[动作错误文本](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/schemas/actions.py:75)、[上一步动作读取](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:1131)、[黑板更新](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:1838)、[dispatcher 错误包装](/home/zhou/GUI-Agent/UFO3/ufo/module/dispatcher.py:43)。

这条反馈路径成立的前提是相关调用已经返回，且后续记忆和 prompt 构造继续进行。工具一直阻塞时，模型尚未获得新结果，及时处理需要依靠程序层的超时、断连检测或取消机制。

**4. 工具与处理流水线中的故障控制**

CommandRouter 默认以 `early_exit=True` 执行命令批次。如果某个工具返回 MCP 协议错误，当前命令被标记为 FAILURE，同批后续命令被标记为 SKIPPED。这能减少前一步失败后继续执行依赖动作的情况。批次结果随后可以交给模型重新判断；批内停止不等于整个任务立即失败。

工具异常由 Computer 包装为 `CallToolResult(is_error=True)`。Router 据此构造 AIP Result，保留错误内容和调用标识。当前判断主要依赖协议层的 `is_error`；工具 JSON 内部的 `success=false` 或非零退出码仍可能需要模型解释，不能把所有业务失败都视为已经触发批内跳过。

依据：[工具异常捕获](/home/zhou/GUI-Agent/UFO3/ufo/client/computer.py:238)、[批内跳过与结果构造](/home/zhou/GUI-Agent/UFO3/ufo/client/computer.py:734)。

Agent 的处理流程还按数据采集、LLM 交互、动作执行、记忆更新等阶段划分。策略发生异常时，可以根据 `fail_fast` 抛出带阶段信息的 `ProcessingException`，或者返回 `ProcessingResult(success=False)`；框架据此调用错误中间件，记录阶段、策略、原始异常和 traceback。顶层收到失败阶段结果后会停止继续遍历后续阶段。

组合策略另外提供“遇错停止”或“保留部分成功结果后继续其他组件”的能力。不过，`fail_fast=False` 本身不表示自动重试，也不保证整个流水线继续。例如 Mobile 的部分采集组件设为 False，但外层组合策略仍设为 True，需要结合调用层次理解其行为。

依据：[策略异常处理](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/processing_strategy.py:108)、[组合策略部分结果处理](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/processing_strategy.py:247)、[顶层阶段中断](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/core/processor_framework.py:369)、[Mobile 策略配置](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/customized/customized_agent_processor.py:148)。

这一层的主要价值是控制错误影响范围、保留诊断上下文，让上层能够决定后续处理。它没有为每一种应用 bug 或机器配置错误预先实现专门的自动修复流程。

**5. 已有的局部恢复与降级路径**

截图采集有明确的替代路径。Windows 应用截图返回了无效图像格式或尺寸过小的图像时，会尝试采集桌面截图；多屏桌面截图尺寸异常时，会改为只采集主屏。最终采集失败时，相关函数记录日志并返回空白占位图，使调用方获得一个可处理的返回值。并非所有截图异常都走同一条 fallback，部分异常直接进入占位处理。

这属于“恢复观察输入”的机制：它有机会绕过窗口或多屏采集问题，但空白占位图仅表示降级结果，不能作为界面已经恢复正常的证据。

依据：[窗口截图降级](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:205)、[多屏到主屏重试](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:334)。

LLM 调用也有几种恢复手段，启用情况由配置决定：

| 机制 | 代码行为 | 当前本地配置 |
|---|---|---|
| 请求超时与错误说明 | 对连接、读取以及授权等错误进行捕获和包装 | 常用 OpenAI 调用路径配置 TIMEOUT=60 秒、READ_TIMEOUT=180 秒 |
| JSON/响应有限尝试 | 请求响应并尝试解析，失败后在次数范围内再次请求 | JSON_PARSING_RETRY=1，即总共尝试一次，没有额外重试 |
| SDK 重试 | 将重试设置传入模型服务客户端 | MAX_RETRY=0 |
| 备用模型服务 | 主服务异常后可以切到 BACKUP，并在递归调用时关闭再次切换 | USE_BACKUP_ENGINE=false，当前未启用 |

响应重试的作用是处理调用或解析失败；代码使用原 prompt 再次请求，并不等同于已经向模型提供了新的任务排故方案。模型调用恢复与工具操作恢复也应分别理解。

依据：[响应尝试循环](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:1221)、[备用引擎路径](/home/zhou/GUI-Agent/UFO3/ufo/llm/llm_call.py:117)、[模型异常说明](/home/zhou/GUI-Agent/UFO3/ufo/llm/openai.py:268)、[当前配置](/home/zhou/GUI-Agent/UFO3/config/ufo/system.yaml:3)。

配置中另有 `MCP_FALLBACK_TO_UI=True`，但本次在所检查的工具执行链路中没有找到消费该开关的通用恢复分支。因此，本文将已确认的截图降级、模型备用引擎路径与这个配置项分别列示，不将后者认定为已接通的通用 MCP→UI 自动切换。

**6. 延迟、无响应与连接故障的恢复**

对于等待过长，系统使用多层超时，并在部分工具内部执行清理：

| 位置 | 当前超时或限制 | 超时后的处理 |
|---|---|---|
| 通用 Computer MCP 调用 | `_tool_timeout=6000` 秒 | 返回工具超时错误；调用在线程池运行，以减少对主事件循环的阻塞 |
| 本地/远程 dispatcher | 默认 6000 秒 | 生成失败结果；远程路径清理等待的 Future |
| Galaxy 单个 TaskStar | 未指定时向设备管理器传入 1000 秒 | 将超时转换成任务失败结果，供上层处理 |
| Linux execute_command | 默认 30 秒，参数限制在 1–120 秒 | 超时或取消时清理读写任务并终止子进程；支持进程组清理 |
| Galaxy 计划修改同步等待 | 600 秒 | 记录警告并解除等待，防止修改同步一直阻塞 |

`system.yaml` 虽声明 `MCP_TOOL_TIMEOUT=30`，当前通用 Computer 路径使用的是写死的 6000 秒。上述值描述当前代码路径，不代表所有工具都必须等到该上限：Linux 命令等内部保护可以更早返回；部分 Mobile ADB 调用则直接等待 `proc.communicate()`，依赖外层保护。

依据：[Computer 超时定义](/home/zhou/GUI-Agent/UFO3/ufo/client/computer.py:65)、[dispatcher 等待](/home/zhou/GUI-Agent/UFO3/ufo/module/dispatcher.py:206)、[TaskStar 超时](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/task_star.py:210)、[Linux 超时与清理](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/linux_mcp_server.py:570)、[Linux 超时参数](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/linux_mcp_server.py:770)、[Mobile ADB 等待示例](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:301)、[同步等待超时](/home/zhou/GUI-Agent/UFO3/galaxy/session/observers/constellation_sync_observer.py:247)。

对于明确的连接中断，已有较完整的通信恢复过程：

1. WebSocket 接收/发送、ping/pong 或连接操作发现异常；应用心跳用于维持连接并更新心跳记录。
2. 设备状态更新为 DISCONNECTED，清理连接和等待任务；相关 Future 收到 ConnectionError，避免仍然等待一个已经断开的响应。
3. UFO 服务器发现目标设备断开时，取消相关会话，并向仍连接的 Galaxy 请求方回传失败原因。
4. 设备管理器按上限进行重连，成功后重置连接尝试计数；耗尽尝试后将设备标记为 FAILED。

重连分布在两个层次：UFO 设备客户端默认最多尝试 3 次，并使用 `2**retry_count` 的退避等待；Galaxy 设备管理器使用设备的 `max_retries`，AgentProfile 默认值为 5，每次按 `reconnect_delay` 等待，当前配置为 5 秒。它们是不同连接角色的逻辑，不能相加为一个统一重试预算。

依据：[客户端连接与重试](/home/zhou/GUI-Agent/UFO3/ufo/client/websocket.py:72)、[客户端退避](/home/zhou/GUI-Agent/UFO3/ufo/client/websocket.py:410)、[断连等待任务清理](/home/zhou/GUI-Agent/UFO3/galaxy/client/components/connection_manager.py:518)、[服务器断连取消](/home/zhou/GUI-Agent/UFO3/ufo/server/ws/handler.py:288)、[Galaxy 重连](/home/zhou/GUI-Agent/UFO3/galaxy/client/device_manager.py:469)、[设备默认尝试次数](/home/zhou/GUI-Agent/UFO3/galaxy/client/components/types.py:30)。

这些机制恢复的是通信条件。已经失败或取消的原任务是否重新执行，还取决于模型重规划或显式任务调度；连接仍在线也不证明被控应用没有卡死。

**7. Galaxy 如何根据失败结果重新规划**

Galaxy 将长任务表示为任务依赖图。执行过程中，任务完成和失败都会产生事件，进而唤醒 ConstellationAgent 对后续计划进行编辑。这是系统中较明确的跨子任务恢复机制。

```text
TaskStar 执行完成或失败
  → 编排器发布 TASK_COMPLETED / TASK_FAILED
  → ProgressObserver 将事件放入模型的完成事件队列
  → Continue 状态收集事件，取得当前任务图
  → process_editing() 让模型判断是否需要补救或修改后续任务
  → 执行计划编辑、校验、同步
  → 编排器基于更新后的依赖图调度后续任务
```

编辑 prompt 明确引导模型保留已完成工作，对可恢复失败新增补救任务，而不是改写失败历史；设备能力不满足原先假设时，可增加其他设备上的任务；前序结果揭示了新的文件路径、数据或约束时，应更新可修改任务的 description/tips。系统把这些内容传给后续设备 Agent，使其具备继续执行所需的信息。

例如，一个子任务已经返回失败结果，模型可以据此增加检查或替代任务，调整尚未运行任务的依赖，或者判断条件不足并返回 FAIL。这里的“检查什么、改用什么方法”主要由模型结合可用工具决定，程序提供的是事件、任务编辑接口和调度能力。

依据：[编排器发布任务事件](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/orchestrator/orchestrator.py:631)、[事件进入 Agent 队列](/home/zhou/GUI-Agent/UFO3/galaxy/session/observers/base_observer.py:50)、[Continue 状态处理](/home/zhou/GUI-Agent/UFO3/galaxy/agents/constellation_agent_states.py:182)、[编辑执行流程](/home/zhou/GUI-Agent/UFO3/galaxy/agents/constellation_agent.py:340)、[补救与跨设备任务引导](/home/zhou/GUI-Agent/UFO3/galaxy/prompts/constellation/share/constellation_editing.yaml:153)。

程序对重规划提供两类保护。其一，执行前以及图编辑过程中校验 DAG、任务引用等约束；编辑命令先备份，若修改导致无效图则恢复备份。其二，修改同步器让调度器等待模型处理相关完成事件，再合并计划结构和执行状态，以减少尚未修改完就启动下游任务的情况；等待有超时解除机制。

这种回滚针对任务图编辑，不会撤销已经在远程应用中发生的点击、文件写入等副作用。DAG 校验也只处理结构依赖，不能直接判断模型是否在语义上反复走错。

依据：[DAG 校验](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/task_constellation.py:483)、[编辑备份与回滚](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/editor/commands.py:134)、[调度前同步](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/orchestrator/orchestrator.py:434)。

TaskStar 还提供 `should_retry()` 和 `retry()`，可在重试次数范围内把 FAILED 重置为 PENDING，并清理错误和执行时间。但本次在 Galaxy 主执行链路中没有找到自动调用这些接口的通用重试循环。因此，明确可见的任务恢复主线是“失败事件驱动模型补救/重规划”，不能仅凭 retry 接口断言所有失败任务都会自动重跑。

依据：[TaskStar 重试接口](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/task_star.py:496)。

**8. 无法恢复时如何停止，何时由用户介入**

系统提供了多种停止入口，作用范围如下：

| 触发条件或入口 | 已有行为 | 作用范围 |
|---|---|---|
| 模型判断无法完成，返回 FAIL | Agent 状态机进入失败/结束路径；Windows AppAgent 的 FAIL 最终转入 HostAgent 的结束状态 | 结束对应决策流程或当前轮次 |
| 达到执行预算 | 普通 UFO 当前 MAX_STEP=50、MAX_ROUND=1；Galaxy MAX_STEP=15 | 在循环边界限制继续执行 |
| 设备或请求方断连 | 服务端取消关联的后台会话，按断连原因决定是否回传结果 | 关联会话与等待任务 |
| Galaxy request_cancellation() | 设置结束标记、结束当前轮次，在有当前任务图时调用编排器取消 | 会话与编排器所管理的执行任务 |
| WebUI 用户点击停止 | shutdown(force=True)，随后重新初始化客户端并建立新会话 | 用户主动停止和恢复会话环境 |

依据：[AppAgent FAIL 状态](/home/zhou/GUI-Agent/UFO3/ufo/agents/states/app_agent_state.py:413)、[会话执行预算](/home/zhou/GUI-Agent/UFO3/ufo/module/basic.py:813)、[Galaxy 轮次预算](/home/zhou/GUI-Agent/UFO3/galaxy/session/galaxy_session.py:136)、[服务端取消](/home/zhou/GUI-Agent/UFO3/ufo/server/services/session_manager.py:388)、[Galaxy 取消入口](/home/zhou/GUI-Agent/UFO3/galaxy/session/galaxy_session.py:495)、[编排器取消](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/orchestrator/orchestrator.py:99)、[WebUI 停止与重建](/home/zhou/GUI-Agent/UFO3/galaxy/webui/services/galaxy_service.py:127)。

这些入口不能统一理解为“关闭整个系统”。步数上限在循环边界检查，无法打断一个尚未返回的等待；模型 FAIL 的处理没有自动覆盖所有后台编排取消入口；外层等待取消也不保证正在执行的线程或远程动作已经终止。具体停止效果要沿对应路径判断。

用户介入则有两种不同机制：

- **PENDING 澄清信息。** Windows Host/App 状态机可以调用 `process_asker()`，收集问题回答并写入黑板，然后回到 CONTINUE。当前 `ASK_QUESTION=False`，因此实际填入的是“答案不可用，依靠已有知识继续”的替代回答。Linux/Mobile prompt 明确要求不询问用户，Galaxy 也没有完整的 PENDING 交互流程。
- **CONFIRM 确认敏感动作。** 当前 `SAFE_GUARD=True`。Windows AppAgent 在派发前保存待确认动作批次，等待用户决定；接受后恢复执行已保存的批次，拒绝则将动作标为 SKIPPED。批次有已消费标记，避免同一恢复过程再次派发。它主要控制敏感操作，并非遇到任意工具故障都会自动进入的排故询问。

依据：[询问与替代回答](/home/zhou/GUI-Agent/UFO3/ufo/agents/agent/basic.py:270)、[Pending 状态](/home/zhou/GUI-Agent/UFO3/ufo/agents/states/host_agent_state.py:240)、[确认前保存批次](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/strategies/app_agent_processing_strategy.py:1349)、[确认与恢复](/home/zhou/GUI-Agent/UFO3/ufo/agents/agent/app_agent.py:388)、[当前开关](/home/zhou/GUI-Agent/UFO3/config/ufo/system.yaml:86)。

因此，对于“模型能否发现异常后停止并询问用户”，现有代码的回答需要按平台和入口区分：模型具备请求失败退出的表达方式，程序提供多种停止保护，Windows 有可配置的用户交互；尚不能据此认定所有故障都会自动暂停并等待用户意见。

**9. 排故记录、结果总结与经验积累**

排故信息主要来自三个时间尺度：

| 时间尺度 | 机制 | 提供的信息或作用 |
|---|---|---|
| 单步执行 | 动作结果、阶段异常、控制台展示、结构化日志 | 哪条命令失败、错误文本、处理阶段、耗时、相关上下文 |
| 任务结束 | result/results、TASK_END、Galaxy 任务事件、Markdown 轨迹 | 已完成内容、失败原因、产物位置、任务状态和执行过程 |
| 会话复盘 | EvaluationAgent、可选 ExperienceSummarizer 和经验检索 | 评价是否完成；提炼正确步骤、去除冗余、生成后续参考提示 |

终态总结由 prompt 要求模型提供。服务器可以通过 TASK_END 发送 status、error、result；Galaxy 的任务事件和观察者使任务结果进入后续规划或界面展示。异常中间件记录原始异常及 traceback，适合开发者定位采集、模型调用或动作执行中的故障。

依据：[异常中间件](/home/zhou/GUI-Agent/UFO3/ufo/agents/processors/core/processing_middleware.py:130)、[服务端结果消息](/home/zhou/GUI-Agent/UFO3/ufo/server/services/session_manager.py:525)、[AppAgent 结果要求](/home/zhou/GUI-Agent/UFO3/ufo/prompts/share/base/app_agent.yaml:65)。

EvaluationAgent 根据请求、轨迹和截图输出 `complete=yes/no/unsure`、分项判断及原因。会话评价发生异常时，还存在以 `eva_all_screenshots=False` 再次评价的降级路径。评价发生在任务运行之后；Linux/Mobile 平台基类中的评价和 Markdown 导出仍为占位实现，不能把 Windows 的复盘能力直接推广到所有平台。

依据：[评价 prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/evaluation/evaluate.yaml:22)、[评价降级](/home/zhou/GUI-Agent/UFO3/ufo/module/basic.py:845)、[平台实现差异](/home/zhou/GUI-Agent/UFO3/ufo/module/sessions/platform_session.py:74)。

经验总结侧有直接的纠错意图：ExperienceSummarizer 的 prompt 要求从可能包含错误或冗余步骤的轨迹中整理正确步骤，并给出风险提示、替代方法和必要知识；AppAgent 也提供经验检索接口，用于后续任务参考。当前配置为 `SAVE_EXPERIENCE=always_not`、`RAG_EXPERIENCE=False`，所以这些属于已有但当前未启用的可选能力，不能说系统正在自动从每次失败中学习。

依据：[经验提炼 prompt](/home/zhou/GUI-Agent/UFO3/ufo/prompts/experience/experience_summary.yaml:3)、[经验保存入口](/home/zhou/GUI-Agent/UFO3/ufo/module/sessions/session.py:70)、[经验检索](/home/zhou/GUI-Agent/UFO3/ufo/agents/agent/app_agent.py:298)、[经验检索配置](/home/zhou/GUI-Agent/UFO3/config/ufo/rag.yaml:14)。

结果传递的实现边界也需要保留：当前部分路径会把“会话结束”汇总为 completed，而模型实际可能返回 FAIL 或耗尽步数；Galaxy 某些失败返回只在嵌套事件中保留原因，没有保存到任务节点。因此，调查和排故时应结合动作错误、Agent 状态、任务结果与日志阅读，不能只依据最外层完成标志判断恢复成功。这些局部行为已在前期离线验证中观察到。

依据：[服务端状态汇总](/home/zhou/GUI-Agent/UFO3/ufo/server/services/session_manager.py:460)、[Galaxy 任务结果传递](/home/zhou/GUI-Agent/UFO3/galaxy/constellation/orchestrator/orchestrator.py:640)。

**10. 对两类典型异常的机制对应**

| 原始场景 | 系统已有的应对链路 | 主要依赖 |
|---|---|---|
| 长规划走错，但工具调用本身成功 | 新截图/结果进入历史 → 模型对比并修改计划 → 继续下一步或 FAIL；Galaxy 可编辑后续任务 | 模型能否从观察中识别偏差 |
| 重复操作且无进展 | prompt 要求检查历史并避免重复 → 模型换方法或 FAIL；持续完成步骤时有 MAX_STEP 兜底 | 模型判断与循环边界预算 |
| 应用加载慢、界面暂时未变化 | Mobile prompt 引导 wait；随后重新观察；调用层另有超时 | 等待策略与再次观察 |
| 工具抛异常、配置或权限错误 | 工具/dispatcher 包装错误 → 批内跳过或阶段中断 → 日志与模型反馈 | 错误返回形式及所在处理阶段 |
| 设备断连或客户端掉线 | 连接异常 → 清理等待任务、取消关联会话、反馈失败 → 有限重连 → 后续由调度或模型决定是否重新安排工作 | 通信层恢复与任务层重规划 |
| 子任务失败但整体仍可完成 | TASK_FAILED → Galaxy 编辑 → 新增补救/替代设备任务 → 校验、同步后调度 | 模型补救方案与可用设备能力 |
| 无法继续或用户主动停止 | FAIL、执行预算、取消入口或 WebUI stop → 结束相应执行流程并记录结果 | 具体停止入口和取消传播范围 |

**11. 未来可以考虑的优化方向**

以下是基于现有机制的延伸方向，不代表本次已经实施或安排修复：

1. **让故障分类更直接地指导恢复。** 统一工具业务错误、协议错误、超时、断连和任务失败的表达，保留步骤、根因和尝试次数，使模型和调度器更容易选择等待、重试、替代方法或终止。
2. **补充模型外的无进展监测。** 在现有动作历史和步数预算基础上，结合画面变化、连续同类错误、等待时长及依赖阻塞状态，形成可解释的暂停条件，并将依据提供给模型或用户。
3. **明确恢复与停止的范围。** 对重连后的任务处理、远程取消确认和可能已部分执行的动作建立一致规则，让“已恢复连接”“可继续任务”“已确认停止”分别有清楚的状态。
4. **把用户介入和复盘连贯起来。** 对需要人工处理的故障给出当前进展、原因和可选下一步；结合已有评价与经验提炼能力，逐步积累经过验证的恢复案例。

**12. 调查与验证范围**

本文以源码、prompt 和当前本地配置为依据。已有的[离线观察脚本](/home/zhou/GUI-Agent/UFO3/documents/study/error-handling-probes.py)使用真实状态判定、CommandRouter、FastMCP 内存返回以及任务图/编排代码，隔离模型和设备连接，用于确认部分错误返回、批内跳过和状态传递行为；脚本没有在本次文档重写中修改。

前期验证包括协议错误后的批内跳过，以及失败状态汇总、业务错误映射、Galaxy 错误保存和依赖等待的实现边界。它们是局部行为观察，不代表完整端到端恢复测试。当前 ufo3 环境缺少 pytest，前期未运行完整 pytest 套件；真实断网、死机、Windows GUI 恢复、远程停止时延和模型异常识别率仍不在已验证范围内。

本次重写仅更新调查文档，未更改系统运行代码或配置。
