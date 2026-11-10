# LinuxAgent 最小开发方案：纯视觉与 Linux 十题

按 MVP 原则补齐执行能力，先跑通真实案例。只增加当前案例必需的代码和测试；遇到实际阻塞再局部修复，不预先建设校验、审计、隔离或运行管理框架。以下路径均相对于 `UFO3/`。

## 1. 目标与链路

复用现有 Galaxy → LinuxAgent → MCP 链路，让 LinuxAgent 支持四阶段：

```text
收集数据：获取当前桌面截图
    → 模型交互：截图 + 请求 + 历史执行结果
    → 执行：MCP 调用 automator 操作桌面
    → 更新记忆：记录动作和结果，进入下一轮
```

现有模型交互、执行和 `AppMemoryUpdateStrategy` 继续复用，补入截图采集与图像输入。GUI 任务通过截图和鼠标键盘完成，CLI 任务继续使用命令。当前先适配本机 X11 桌面，不扩展跨平台兼容。

## 2. 开发任务与位置

| 任务 | 代码位置 | 最小改动 |
| --- | --- | --- |
| 桌面操作 | 新增 `ufo/automator/linux/desktop.py`、`__init__.py` | 用 PyAutoGUI 封装截图、点击、滚动、输入、组合键 |
| GUI MCP | 新增 `ufo/client/mcp/http_servers/linux_gui_mcp_server.py` | 暴露上述接口，复用现有 MCP 服务和认证方式 |
| 数据采集阶段 | `ufo/agents/processors/customized/customized_agent_processor.py`、`ufo/agents/processors/strategies/linux_agent_strategy.py` | 注册截图策略，保存本轮截图，向模型传递图片和执行结果 |
| 图像提示 | `ufo/agents/agent/customized_agent.py`、`ufo/prompter/customized/linux_agent_prompter.py` | 消息构造增加图片参数，生成实际的 `image_url` 内容块 |
| 提示与配置 | `ufo/prompts/third_party/linux_agent.yaml`、`linux_agent_example.yaml`，`config/ufo/mcp.yaml`、`third_party.yaml` | 补充视觉操作说明及示例，移除视觉模式下现有“禁止截图和 GUI”的要求，接入 GUI 服务 |
| 文件操作 | `ufo/client/mcp/http_servers/linux_mcp_server.py` | 增加 `cp/mkdir/chmod/mv/zip`，同步更新现有白名单、参数策略和工具说明 |
| 依赖 | `requirements.txt` 及启动说明 | 补齐 Linux PyAutoGUI、截图依赖、ZIP 和 LibreOffice Calc 的安装说明 |

不重新设计处理框架、公共 AIP 协议或 Galaxy 调度；CLI 直接复用 BashExecutor，无需给每个命令新增包装层。

## 3. 最小工具与案例对应

GUI MCP 提供以下接口：

| 工具 | 用途 |
| --- | --- |
| `take_screenshot()` | 返回 PNG data URL，采集策略保存并传给模型 |
| `click(x, y, button="left", clicks=1)` | 单击、双击 |
| `scroll(x, y, amount)` | 在指定位置滚动，正数向上、负数向下 |
| `type_text(text)` | 向当前焦点输入文本 |
| `press_keys(keys)` | 单键或组合键，例如 `["ctrl", "s"]` |

截图保持原始尺寸，坐标以左上角为原点。输入先覆盖当前题目需要的英文、数字和公式。动作结果沿用现有结构，失败原因进入下一轮上下文；不额外规定每轮动作数量、强制空动作结束或专用复核轮次。

| 案例 | 所需能力 |
| --- | --- |
| `logs`、`config`、`php_line_count` | 复用现有读取、查找、比较、统计命令 |
| `collect_jpg` | 查找并复制 JPG |
| `collect_failed_notebooks` | 查找、`mkdir -p`、按相对目录复制 |
| `normalize_permissions` | 对普通文件执行 `chmod` |
| `archive_by_age` | 查询 mtime、创建 ZIP、移动新文件 |
| `replace_text` | VSCode 截图、替换、保存 |
| `zoom_out`、`monthly_sales` | Calc 截图、缩放、单元格/公式输入、工作表操作、保存 |

新增 CLI 命令沿用现有执行与参数校验方式，保留 `shell=False`；具体路径由模型根据查找结果传入。GUI 复用同一组桌面工具，不增加直接生成文本或 XLSX 答案的接口。

## 4. 必要接入与职责

- 使用一个模式选择：`cli` 或 `visual`，默认 `cli`，通过 `UFO_LINUX_MODE` 传给本轮 UFO 进程。CLI 注册 BashExecutor，视觉模式注册 GUI 工具；按现有配置选择即可，不建设额外隔离或轨迹审计机制。
- AgentArena 将案例的 `cli/gui` 映射到上述模式。当前 `start_local()` 只启动 BashExecutor，需按模式补接 GUI MCP 启动命令，并将模式传给 UFO 服务和客户端。沿用现有逐题启动流程；已有服务按对应模式启动即可。
- 截图、动作和评测端打开的 VSCode/Calc 使用同一个桌面会话。应用初始化、素材准备和产物比较仍由 AgentArena 负责；UFO 操作当前打开的文件，并通过应用保存。
- 正确传递工具失败和 LinuxAgent `FAIL`。已发现基础会话的 `is_error()` 只识别 `ERROR`，应在 Linux 会话中最小修正，避免失败或步数耗尽被报告为完成。相关位置：`ufo/module/sessions/linux_session.py`、`ufo/module/basic.py`、`ufo/server/services/session_manager.py`；仅改实际需要的位置。
- 保留真实 stdout、退出码和最终回答；GUI 截图使用现有日志目录。复用已有超时和错误处理，不新增重试、取消或服务管理体系。
- 自然回答解析、读取证据误判、PHP 数字提取和现场清理由 AgentArena 修复；不要通过修改 UFO 输出迎合验收格式，也不将这些评测修复全部设为工具联调的前置条件。

## 5. 最小验证与交付

1. 先跑通 `replace_text` 的真实 Galaxy → LinuxAgent → GUI 工具链，检查模型收到截图，保存文件正确。
2. 用现有评测依次运行四道文件题及两道 Calc 题，再串行运行全部十题，保留每次实际结果。
3. 对新增文件工具做一次临时目录冒烟检查；为失败状态传播补一个针对性回归测试。沿用现有相关测试，不新增成套边界、权限、隔离或故障注入测试。
4. 评测端每题用一个明显错误或缺失产物确认不会误判通过；已有检查直接复用。修改公共代码时才补跑受影响的平台测试。

交付实现代码、必要依赖和启动说明、真实执行记录。模型或环境失败如实记录，单测通过不等于端到端跑通。无需新增审核流程或要求所有案例通过后才能交付进展。
