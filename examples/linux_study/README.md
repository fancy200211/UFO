# UFO3 Linux 能力与测试示例

需要从 Galaxy 编排入口开始、超过10步的复杂任务时，使用 [Galaxy 多步事故调查测试](../galaxy_incident/README.md)。它包含17份证据、错误线索、失败恢复、统计与因果复核；当前 Linux-only 模式不会运行 Windows HostAgent/AppAgent。

核查日期：2026-10-26。依据本地 UFO3（HEAD `eae3c0a`，包含现有工作区修改）与官方 main 的 Linux MCP 工具源码。这里的能力指当前默认 LinuxAgent 和 BashExecutor 配置，不包括自行扩展的 MCP 服务。

## 1. 实际能做什么

LinuxAgent 是通过命令行完成任务的单 Agent。没有 HostAgent / AppAgent 的桌面分工，也没有 Linux 截图、控件识别、鼠标点击或键盘输入工具。

| 类别 | 当前可用能力 | 典型工具或命令 |
|---|---|---|
| 系统查询 | 内核、运行时间、内存、磁盘、用户、进程查询 | `get_system_info`；`uname`、`free`、`df`、`ps` |
| 文件查看 | 列目录、读取文本、查看头尾、查找文件 | `ls`、`cat`、`head`、`tail`、`find` |
| 日志分析 | 搜索关键字、统计行数、按字段提取 | `grep`、`wc`、`cut`、`uniq` |
| 配置比较与文件校验 | 比较文本、查看属性和文件类型、计算哈希 | `diff`、`stat`、`file`、`sha256sum` |
| 网络诊断 | 连通性、路由、域名解析 | `ping`、`traceroute`、`nslookup`、`dig`、`host` |
| 多步推理 | 根据任务和上一步结果选择命令、更新记忆、继续或结束 | `CONTINUE` / `FINISH` / `FAIL` |
| 远程任务 | 由 Device Agent Server 下发任务；也可作为 Galaxy 的 Linux 设备 | HTTP 任务接口、WebSocket/AIP、设备侧 MCP |

命令在白名单中，并不代表本机已经安装；执行程序只从受信任的 `/usr/bin`、`/bin` 解析。

只有两个默认 MCP 工具：

- `execute_command(command, api_key, timeout=30, cwd=None)`：校验命令与参数后，通过 `asyncio.create_subprocess_exec` 执行，不经过 shell 解释器。正常执行返回 `success`、`exit_code`、`stdout`、`stderr`；校验失败、超时等情况返回 `success=false` 和 `error`。
- `get_system_info(api_key)`：依次查询 `uname -a`、`uptime`、`free -h`、`df -h`，返回 `uname`、`uptime`、`memory`、`disk` 四个文本字段。

### 不能按文档宣传直接推断的能力

当前 README/Prompt 中提到的备份、包管理、配置修改、服务管理，超出了默认执行白名单。当前不能直接运行 `tar`、`mkdir`、`cp`、`rm`、`apt`、`pip`、`systemctl`、`bash` 等命令；`sort` 也不在白名单。

`python` / `python3` 只允许 `--version` / `-V`，不能运行 Python 脚本、`-c` 或 `-m`。因此也不能让默认 LinuxAgent 直接运行 pytest、项目启动脚本或任意测试程序。本目录脚本由你在终端启动，随后调用 UFO 的接口；不是让 LinuxAgent 用 execute_command 执行这些脚本。

管道、命令串联和重定向被拒绝，如 `cat app.txt | wc -l`、`ls && pwd`、`echo x > out.txt`。应拆成独立工具调用；指定工作目录用 `cwd`，不是 `cd`。部分命令还限制参数，如 `find -delete` 被拒绝。

命令默认超时 30 秒，可配置范围 1–120 秒；stdout 和 stderr 合计超过 1,000,000 字节会终止命令并返回错误，不是简单截断。鉴权密钥来自 `UFO_MCP_API_KEY`。本地现有 `config/ufo/mcp.yaml` 和 `ufo/client/computer.py` 已有设备客户端自动注入密钥、从模型可见 schema 中移除密钥参数的逻辑。

## 2. 单步执行链路

```text
用户自然语言任务
  → LinuxAgent CONTINUE.handle()
  → LinuxAgentProcessor
      1. LLM：组装任务、工具说明、计划、历史上下文，生成 action JSON
      2. Action：分发给设备客户端 → BashExecutor MCP → 执行白名单命令
      3. Memory：记录动作和结果，更新状态
  → CONTINUE：再进行下一步
  → FINISH / FAIL：结束当前 Round
```

Linux 的独立处理策略没有截图感知阶段；其信息主要来自工具返回的文本。它有多步规划和反馈循环，但不是 Ubuntu 桌面视觉操作 Agent。当前 Linux LLM 策略还显式传入空的 `dynamic_examples` 和 `dynamic_knowledge`，不能据此声称它已启用与 Windows AppAgent 相同的动态 RAG 检索链路。

## 3. 不调用模型的 MCP 测试

在已经安装项目依赖的 `ufo3` 环境中运行：

```bash
cd /home/zhou/GUI-Agent/UFO3
conda activate ufo3
python examples/linux_study/mcp_smoke.py
```

脚本自动启动一个独立的本机 MCP 子进程，使用随机空闲端口和临时随机密钥，结束时关闭自己启动的服务。无需填写模型 API Key，也不会连接已经运行的 8010 服务。

| 用例 | 检查内容 | 预期 |
|---|---|---|
| system_info | 查询系统信息 | 存在四个系统信息字段 |
| read_log | `cat app.txt` | 返回示例日志 |
| count_errors | `grep -c ERROR app.txt` | 返回 3 |
| show_errors | `grep -n ERROR app.txt` | 返回 3 条带行号的日志 |
| config_diff | `diff -u before.conf after.conf` | 返回差异，退出码为 1 |
| missing_file | 读取不存在的文件 | `success=false`，stderr 非空 |
| reject_pipeline | 使用管道 | 策略拒绝 |
| reject_script | 请求 Python 执行脚本 | 策略拒绝，不启动脚本 |
| reject_bad_key | 使用错误密钥 | 鉴权失败 |

实测结果：**9/9 通过**，使用本地 Conda Python 3.10 和 FastMCP 2.11.3，经真实 streamable-http MCP 调用。脚本会显示实际工具返回值。这个结果只证明这些工具用例通过，不代表完整模型任务链路已验证。

特别注意：`diff` 返回 1 的含义是“发现差异”，而 MCP 的 `success` 直接由 `returncode == 0` 计算，所以返回 `success=false`。`grep` 没有匹配内容时也可能返回 1。Agent 需要结合 stdout、stderr 和命令语义判断任务结果。

## 4. 给 LinuxAgent 的自然语言测试任务

`agent_tasks.py` 默认只打印任务 JSON；加 `--send` 才会提交给正在运行的 UFO Server，进入实际模型调用。准备了五类任务：

| case | 用户任务 | 检查重点 |
|---|---|---|
| system | 查询系统、内存、磁盘 | 工具选择与实际数据汇总 |
| logs | 读取日志，列出 ERROR、统计数量 | 应返回 3 条 ERROR，包含数据库超时和上游不可用 |
| diff | 比较两个配置文件 | 应识别 timeout 30→60、debug false→true |
| recover | 先读不存在的文件，再查目录读取 app.txt | 能否利用失败反馈继续规划；此行为取决于模型 |
| unsupported | 操作 Ubuntu 计算器并截图 | 应如实说明缺少 GUI 工具，返回 FAIL，而不是虚构成功 |

先查看任务内容，不联网、不调用模型：

```bash
python examples/linux_study/agent_tasks.py --case logs
python examples/linux_study/agent_tasks.py --case recover
python examples/linux_study/agent_tasks.py --case unsupported
```

### 运行完整 Agent 链路的准备

需要先配置 `config/ufo/agents.yaml` 中的模型；LinuxAgent 使用 AppAgent 的模型配置。以下按 Server、Client、MCP 都在本机说明。各终端均先进入 UFO3 并激活 `ufo3` 环境。

终端 A：启动 MCP。

```bash
export UFO_MCP_API_KEY='<你设置的本机MCP密钥>'
python -m ufo.client.mcp.http_servers.linux_mcp_server --host 127.0.0.1 --port 8010
```

终端 B：启动 Device Agent Server。

```bash
python -m ufo.server.app --host 127.0.0.1 --port 5001 --platform linux
```

Server 会打印自动生成的 API Key。它与 MCP 密钥是两个不同层级的凭证。下面的 `UFO_SERVER_API_KEY` 是示例脚本使用的环境变量名，不是 UFO Server 自动读取的配置项。

终端 C：注册 Linux 设备。MCP 密钥与终端 A 一致，Server 密钥填终端 B 打印的值。

```bash
export UFO_MCP_API_KEY='<与终端A相同的MCP密钥>'
export UFO_SERVER_API_KEY='<终端B打印的Server密钥>'
python -m ufo.client.client --ws \
  --ws-server "ws://127.0.0.1:5001/ws?token=${UFO_SERVER_API_KEY}" \
  --client-id linux_agent_1 --platform linux
```

终端 D：提交任务并查询结果。

```bash
export UFO_SERVER_API_KEY='<终端B打印的Server密钥>'
python examples/linux_study/agent_tasks.py --case logs --send
```

提交返回 `task_name`；这只表示任务已下发。使用返回的实际名称查询一次结果：

```bash
python examples/linux_study/agent_tasks.py --result '<返回的task_name>'
```

结果接口的 `pending` 表示尚未取到结果，`done` 表示已有结果；是否完成用户任务还需检查内部结果，不能只看 HTTP 成功或 `done`。建议等一个任务结束后再提交下一个。设备 ID 不同可用 `--client-id` 修改；远程设备的资料路径不同则用 `--fixtures` 指向该设备上的示例资料目录。

本次没有提交这些模型任务；已验证任务 JSON 的生成。完整端到端效果需在你的模型和服务配置下实测。

## 5. 源码阅读入口

- `ufo/client/mcp/http_servers/linux_mcp_server.py`：两个 MCP 工具、命令白名单、参数校验、子进程执行、结果与超时处理。
- `config/ufo/mcp.yaml`：LinuxAgent → BashExecutor 服务配置。
- `ufo/agents/agent/customized_agent.py`：LinuxAgent 定义。
- `ufo/agents/states/linux_agent_state.py`：CONTINUE / FINISH / FAIL。
- `ufo/agents/processors/customized/customized_agent_processor.py`：Linux 单步三阶段编排。
- `ufo/agents/processors/strategies/linux_agent_strategy.py`：提示词组装、模型响应解析与动作执行。
- `ufo/prompts/third_party/linux_agent.yaml`：模型任务、状态、action 输出格式。
- `ufo/module/sessions/linux_session.py`：Linux 本地和服务会话。
- `ufo/server/services/api.py`：带鉴权的任务下发与结果查询接口。
- `tests/test_linux_mcp_command_policy.py`：仓库已有的命令策略回归测试。

官方参考：[Linux MCP 源码](https://github.com/microsoft/UFO/blob/main/ufo/client/mcp/http_servers/linux_mcp_server.py)。功能以代码和实际测试为准，文档中的旧例子可能与当前白名单不一致。
