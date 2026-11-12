# LinuxAgent 工具验证记录（2026-11-11）

核心实现已接入 UFO3。按用户最新要求，本轮交付范围为通用工具与 Agent 接入，评测由用户自行运行。
本记录保留工具级验证和停止前的历史运行情况；不将服务可用视为题目通过。

## 环境与启动

- 本机 GNOME/X11，DISPLAY=:0，截图 1920×1080。
- 采集：`/usr/bin/python3`（Python 3.12，pyatspi、gi、Pillow）。
- MCP、桌面动作、UFO/Galaxy：`/home/zhou/miniconda3/envs/ufo3/bin/python`（Python 3.10）。
- Bash MCP 8010、GUI MCP 8011、Linux Device Server 5002，设备 `linux_device_1`，手动启动；评测未使用 `--local-services`。
- 本次将 wmctrl、xclip 的发行版包解压到 `~/.local/share/ufo-linux-deps`，在 `~/.local/bin` 建立入口并加入服务 PATH；没有迁移 Conda 环境。安装了测试所需 pytest/pytest-asyncio。
- 保留既有模型配置：Galaxy `glm-5.3`、LinuxAgent `glm-5.3-flash`，MAX_STEP=50，每题 timeout=1800 秒。
- 本次临时服务和评测批次已停止，临时设备配置已移除，原有 Mobile 设备条目保持不变。正式启动方式见 [Linux quick start](../getting_started/quick_start_linux.md)。

## 核心工具检查

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 两个 Python 的依赖导入 | 通过 | 系统采集与 Conda MCP/动作分别运行成功 |
| MCP 鉴权、数据解析、图片进入提示、失败状态、文件操作回归 | 76 passed，5 subtests passed | [pytest.log](../../../logs/linux_tools_validation/pytest.log) |
| 真实 HTTP MCP → UFO CommandRouter → GTK 测试窗口 | 通过：编号点击、中文/英文多行粘贴、Ctrl+S、拖动、滚动、再次采集 | [desktop_smoke/result.json](../../../logs/linux_tools_validation/desktop_smoke/result.json) |
| 文件操作 | 临时目录实际完成 cp、mkdir -p、cp --parents、chmod、zip、mv | `tests/test_linux_mcp_command_policy.py` 中真实执行测试 |
| LibreOffice 活动弹窗 → 编号点击 OK → 再次观察主窗口 | 通过 | [modal_smoke/result.json](../../../logs/linux_tools_validation/modal_smoke/result.json) |

测试命令：

```bash
python -m pytest tests/test_linux_gui_integration.py tests/test_linux_mcp_command_policy.py tests/clients/test_mcp_api_key_injection.py -q --disable-warnings
```

五种应用均用独立测试实例/素材采集并查看标注图，窗口信息与截图坐标可对应：

| 应用 | 主窗口树节点 | 编号控件 | 实际限制 |
| --- | ---: | ---: | --- |
| VS Code | 370 | 74 | 编辑区和工具栏可见 |
| Calc | 876 | 114 | 虚拟表格只保留选中单元格，其他可见内容依靠截图 |
| Writer | 918 | 109 | 文档及工具栏可见 |
| Impress | 827 | 146 | 幻灯片对象及工具栏可见 |
| Chrome | 1 | 0 | 默认启动的实例只暴露窗口根节点；截图正常，需坐标操作 |

原始采集见 [app_probes_v2/results.json](../../../logs/linux_tools_validation/app_probes_v2/results.json)，
各应用目录包含 `observation.json`、`screen.png`、`annotated.png`。
这组数据记录主窗口；之后实际发现 LibreOffice 的活动弹窗属于同进程的另一个 AT-SPI 根节点，
已修正选窗优先级，并用上述 modal_smoke 验证关闭弹窗后的观察变化。

## 已停止的历史试跑

以下试跑发生在用户明确“评测由用户负责”之前，保留原始记录，不再继续批次或据此调整工具。

首次 `logs` 运行目录：
[arena-20261111T064122-f3c4ff7c](../../../../AgentArena/src/linux-arena-container/runs/arena-20261111T064122-f3c4ff7c/result.json)。

结果为 **verification_failed**，不能记为通过。Galaxy 首次探测曾 ReadTimeout；随后 LinuxAgent 首次响应
不是有效 JSON，原有中间件没有设置失败状态，产生了空结果的“成功结束”。Galaxy 重试读取素材，返回了
3 条 ERROR 的完整答案；验收因多个终态结果和首个空轨迹失败。真实 Bash 调用、截图输入、空动作最终回答回传已有证据。

针对该接入问题，Linux 日志中间件现将处理异常设置为 FAIL，并写入错误结果，使用现有 Linux 会话失败判断。
已重启服务加载修复，并添加回归测试；原失败记录保持不变。

修复后的 `logs`（`arena-20261111T065154-edcbe135`）执行正常结束，读取轨迹检查通过。
答案含 `Total ERROR entries: 3` 和正确的三对字段，但现有答案解析器只匹配中文计数表达，
并将解释中排除的 INFO/r03/OK 另取为字段块，因此仍为 verification_failed。
这是本次运行的实际验收结果；未改动题目解析器，也未给 UFO 加入该题的专用回答格式。

曾使用现有 batch.py 与临时 suite 进行分组试跑，现已按用户指示停止。历史目录：
[batch-20261111T065154-bc4ef0b8](../../../../AgentArena/src/linux-arena-container/runs/batch-20261111T065154-bc4ef0b8/summary.json)。
停止时 `config`、`php_line_count` 通过，`logs` 验收失败，`replace_text` 中断，其余 11 题未执行。
完整 linux15 批次未运行。批次原始记录保留中断时的状态与测试素材；随后已确认本次临时服务、评测进程和应用实例不再运行。

## 源码范围

新增 `ufo/automator/linux/observation.py`、`desktop.py`、GUI MCP 服务和针对性集成测试。
修改 LinuxAgent 数据采集策略、消息构造/提示词、MCP 配置、文件命令策略、Linux 会话失败判断及启动说明。
[本次完整修改补丁](../../../logs/linux_tools_validation/implementation.patch) 以任务开始时的工作区为基线，排除了用户已有修改。
没有修改公共 AIP 协议、Galaxy 调度或 AgentArena 源码，也未改动工作区原有的延迟观测代码。
