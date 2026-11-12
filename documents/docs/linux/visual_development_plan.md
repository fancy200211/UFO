# LinuxAgent 十五题工具开发与接入

本版实现对应经确认方案的工具开发部分：截图与 AT-SPI 感知、桌面操作、MCP 接入、
LinuxAgent 图像输入和文件操作。Bash 与 GUI 同时可用，不按 MODE 切换。
AgentArena 负责准备现场、打开应用、提交 Galaxy、验收和清理，UFO3 提供通用工具。
按用户最新要求，评测由用户运行；本轮交付工具集合与接入说明，不根据题目成绩调整工具。

## 开发步骤与位置

1. **环境和依赖**：`requirements.txt` 开放 Linux PyAutoGUI，并加入 pyperclip。
   系统 Python 运行 AT-SPI/Pillow 采集；Conda 环境运行 UFO、MCP 和桌面动作。
   详见 [启动说明](../getting_started/quick_start_linux.md)。
2. **采集封装**：`ufo/automator/linux/observation.py` 基于已有 capture_ui 脚本，
   返回原图、编号标注图、活动窗口、控件树与交互控件。默认只遍历前台窗口；
   空树时保留截图及原因。原始坐标以截图左上角为原点。
3. **桌面动作**：`ufo/automator/linux/desktop.py` 提供坐标/编号点击、滚动、
   Unicode 文本粘贴、组合键、拖动及 X11 窗口枚举/激活。编号仅在下一次采集前有效。
4. **GUI MCP**：`ufo/client/mcp/http_servers/linux_gui_mcp_server.py` 默认运行于
   `localhost:8011/mcp`，复用 API key 机制。`config/ufo/mcp.yaml` 将其登记到
   LinuxAgent 的数据采集和动作服务，同时保留 BashExecutor。
5. **Agent 闭环**：`customized_agent_processor.py` 注册 Linux 数据采集策略；
   `linux_agent_strategy.py` 保存观察并送入模型；`customized_agent.py` 与
   `linux_agent_prompter.py` 构造实际 image_url 内容块。更新 Linux 提示词、示例和能力介绍，
   复用既有动作执行及记忆更新。
6. **文件操作**：`linux_mcp_server.py` 增加 cp、mkdir、chmod、mv、zip 的执行与参数策略，
   使用已有 execute_command、cwd 和 shell=False。模型先 find/stat，再传入明确路径。
7. **结果修复**：Linux 动作策略保留失败信息、识别 CLI 非零退出、正确处理空动作；
   `linux_session.py` 识别 FAIL 及未 FINISH 时的步数耗尽。
8. **启动说明**：提供 Bash MCP、GUI MCP、Linux server、Linux client 的手动启动步骤。
   用户运行 Arena 时连接已有设备，省略 --local-services。

## 工具接口

- `get_desktop_observation(app_name=None)`：PNG data URL、树、控件、活动窗口及警告。
- `list_windows()` / `activate_window(window_id)`：应用窗口枚举与切换。
- `click(x,y,button="left",clicks=1)` / `click_control(control_id,button="left",clicks=1)`。
- `scroll(x,y,amount)`：正数向上、负数向下。
- `type_text(text)`：剪贴板与 Ctrl+V，支持 Unicode 和多行，覆盖剪贴板。
- `press_keys(keys)`：单键或同时按下的组合键。
- `drag(start_x,start_y,end_x,end_y,duration=0.5)`：左键拖动。

树节点包含 id、parent_id、name、role、bounds、states；控件列表增加 control_id，
与编号图一致。采集结果和图片通过现有 MCP/AIP 字典传输，不修改公共协议。

## 工具验证与评测交接

1. 针对性测试：MCP 结果可解析、图片进入提示、坐标编号映射、空动作、失败状态与文件操作。
2. 桌面冒烟：真实 HTTP MCP → UFO CommandRouter → 测试窗口，验证粘贴、点击、滚动与拖动；
   逐个查看 VS Code、Calc、Writer、Impress、Chrome 的观察结果。
3. 交付接口、源码位置及服务启动说明，评测交由用户进行。

题库为 8 easy、5 medium、2 hard。工具验证不代表题目验收通过。
原计划中的分组评测及完整 linux15 批次不再由本轮开发执行。
用户评测命令保留每题等待期限 1800 秒，默认 UFO MAX_STEP 保持 50。
当前只适配本机单屏 X11；截图和树为顺序采集，不增加跨平台兼容、审核或运行管理框架。

实际验证结果见 [工具验证记录](tool_validation.md)。
