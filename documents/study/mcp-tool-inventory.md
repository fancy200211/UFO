# 项目 MCP 工具源码索引

根据当前工作区的配置和工具注册源码整理。按目标设备操作系统归类，Android 服务可以在连接手机的主机上运行。

配置入口：[mcp.yaml](/home/zhou/GUI-Agent/UFO3/config/ufo/mcp.yaml:16)。共 100 个工具注册定义，按服务分别计数；不包含 Client 自身的元工具（如 list_tools）。这是静态源码清单，未启动设备服务验证。

## Agent 与集合的对应关系

| Agent / 应用 | data_collection | action |
|---|---|---|
| HostAgent | UICollector | HostUIExecutor、CommandLineExecutor |
| AppAgent / default | UICollector | AppUIExecutor、CommandLineExecutor |
| AppAgent / WINWORD.EXE | UICollector | AppUIExecutor、WordCOMExecutor |
| AppAgent / EXCEL.EXE | UICollector | AppUIExecutor、ExcelCOMExecutor |
| AppAgent / POWERPNT.EXE | UICollector | AppUIExecutor、PowerPointCOMExecutor |
| AppAgent / explorer.exe | UICollector | AppUIExecutor、PDFReaderExecutor |
| LinuxAgent | 未配置 | BashExecutor |
| MobileAgent | MobileDataCollector | MobileActionExecutor |
| ConstellationAgent | 未配置 | ConstellationEditor |
| HardwareAgent | HardwareCollector | HardwareExecutor |

应用专属配置使用各自的配置块，不会自动合并 default 的工具。因此 Word 等配置中没有 CommandLineExecutor。当前配置未定义 macOS、iOS 专属工具集合。

data_collection / action 是配置中的用途分类，不是严格的只读/写入分类。例如 get_system_info 和 texts 属于 action，add_control_list 属于 data_collection。

## Windows：UICollector（9 个）

配置分类：`data_collection`。用途：桌面应用、窗口、控件、截图和 UI 树。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [get_desktop_app_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:645) | Get information about all application windows currently open on the desktop. |
| [get_desktop_app_target_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:688) | Get information about all application windows currently open on the desktop. |
| [get_app_window_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:731) | Get information about the currently selected application window. |
| [get_app_window_controls_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:747) | Get information about controls in the currently selected application window. |
| [get_app_window_controls_target_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:773) | Get information about controls in the currently selected application window. |
| [capture_window_screenshot](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:811) | Capture a screenshot of the currently selected application window. |
| [capture_desktop_screenshot](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:857) | Capture a screenshot of the desktop (all screens or primary screen). |
| [get_ui_tree](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:887) | Get the UI tree for currently selected application window. |
| [add_control_list](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:901) | Add a list of control elements from grounding results to the control dictionary. |

## Windows：HostUIExecutor（1 个）

配置分类：`action`。用途：选择应用窗口。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [select_application_window](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:201) | Select an application window for UI automation. |

## Windows：AppUIExecutor（9 个）

配置分类：`action`。用途：应用内鼠标、键盘、文本读取、等待等。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [click_input](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:285) | Click on a UI control element using the mouse. All type of controls elements are supported. |
| [click_on_coordinates](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:329) | Click on specific coordinates within the application window, instead of clicking on a specific control item. |
| [drag_on_coordinates](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:364) | Drag from one point to another point in the application window, instead of dragging a specific control item. |
| [set_edit_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:425) | Type text in a control element. |
| [keyboard_input](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:472) | Simulate keyboard input to a control or the focused application, such as sending key presses or shortcuts. |
| [wheel_mouse_input](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:515) | Send mouse wheel input to scroll a control. |
| [texts](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:557) | Retrieve all text content from a control element. |
| [wait](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:589) | Wait for a specified number of seconds before continuing execution. |
| [summary](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ui_mcp_server.py:615) | 返回传入的摘要文本；函数本身不调用模型生成摘要。 |

## Windows（按当前配置）：CommandLineExecutor（1 个）

配置分类：`action`。用途：当前仅按名称启动白名单应用，不接受参数，也不是任意 Shell 执行。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [run_shell](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/cli_mcp_server.py:138) | Launch an allow-listed application by name only, without arguments. |

## Windows / Word：WordCOMExecutor（6 个）

配置分类：`action`。用途：Word 表格、选择、字体和保存。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [insert_table](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:92) | Insert a table to a Word document. |
| [select_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:111) | Select the text in a Word document for further operations, such as changing the font size or color. |
| [select_table](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:130) | Select a table in a Word document for further operations, such as deleting the table or changing the border color. |
| [select_paragraph](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:151) | Select a paragraph in a Word document for further operations, such as changing the alignment or indentation. |
| [save_as](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:182) | The fastest way to save or export the Word document to a specified file format with one command. |
| [set_font](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/word_wincom_mcp_server.py:223) | Set the font of the selected text in a Word document. The text must be selected before calling this command. |

## Windows / Excel：ExcelCOMExecutor（6 个）

配置分类：`action`。用途：Excel 表格读写、选择、列重排和保存。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [table2markdown](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:89) | Get the table content in a sheet of the Excel app and convert it to markdown format. |
| [insert_excel_table](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:113) | Insert a table to the Excel sheet. The table is a list of list of strings or numbers. |
| [select_table_range](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:150) | A quick way to select a range of cells in the sheet of the Excel app instead of dragging the mouse. |
| [save_as](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:194) | A shortcut and quickest way to save or export the Excel document to a specified file format with one command. |
| [reorder_columns](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:234) | Reorder the columns in the sheet of the Excel app based on the desired order. |
| [get_range_values](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/excel_wincom_mcp_server.py:256) | Get the values from a specified range in the sheet. |

## Windows / PowerPoint：PowerPointCOMExecutor（2 个）

配置分类：`action`。用途：设置幻灯片背景和保存。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [set_background_color](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ppt_wincom_mcp_server.py:89) | A fast way to set the background color of one or more slides in a PowerPoint presentation. |
| [save_as](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/ppt_wincom_mcp_server.py:120) | The fastest way to save or export the PowerPoint presentation to a specified file format with one command. |

## Windows / explorer.exe：PDFReaderExecutor（3 个）

配置分类：`action`。用途：列出 PDF、提取单个或多个 PDF 的文本。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [extract_pdf_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/pdf_reader_mcp_server.py:177) | Extract text content from a single PDF file with optional human simulation. |
| [list_pdfs_in_directory](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/pdf_reader_mcp_server.py:210) | List all PDF files in the specified directory. |
| [extract_all_pdfs_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/pdf_reader_mcp_server.py:232) | Extract text content from all PDF files in the specified directory with human simulation. |

## Linux：BashExecutor（2 个）

配置分类：`action`。用途：白名单命令执行和系统信息查询。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [execute_command](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/linux_mcp_server.py:757) | Execute an allow-listed command on Linux and return stdout/stderr. |
| [get_system_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/linux_mcp_server.py:829) | Get basic system info (uname, uptime, memory, disk). |

## Android：MobileDataCollector（5 个）

配置分类：`data_collection`。用途：截图、UI 树、设备、应用和控件信息。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [capture_screenshot](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:286) | Capture screenshot from Android device. |
| [get_ui_tree](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:357) | Get the UI hierarchy tree in XML format. |
| [get_device_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:414) | Get comprehensive Android device information. |
| [get_mobile_app_target_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:506) | Get information about installed application packages as TargetInfo list. |
| [get_app_window_controls_target_info](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:596) | Get UI controls information as TargetInfo list. |

## Android：MobileActionExecutor（8 个）

配置分类：`action`。用途：点击、滑动、输入、启动应用、按键、等待和缓存管理。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [tap](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:764) | Tap/click at specified coordinates on the screen. |
| [swipe](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:808) | Perform swipe gesture from start to end coordinates. |
| [type_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:860) | Type text into a specific input field control. |
| [launch_app](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:1031) | Launch an application by package name or app ID. |
| [press_key](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:1167) | Press a hardware or software key. |
| [click_control](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:1215) | Click a UI control by its id and name. |
| [wait](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:1309) | Wait for a specified number of seconds. |
| [invalidate_cache](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/mobile_mcp_server.py:1358) | Manually invalidate cached data to force refresh on next query. |

## 任务编排，不限定操作系统：ConstellationEditor（7 个）

配置分类：`action`。用途：任务及依赖关系的增删改、批量构建任务图。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [add_task](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:43) | Add a new task to the constellation. The task will be validated and automatically assigned timestamps and default values. |
| [remove_task](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:106) | Remove a task from the constellation. This operation will automatically remove all dependencies that reference this task (both incoming and outgoing) to maintain constellation integrity. |
| [update_task](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:131) | Update specific fields of an existing task in the constellation. Only the provided fields will be modified, other fields remain unchanged. |
| [add_dependency](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:196) | Add a dependency relationship between two tasks in the constellation. This creates a directed edge from source to target task, establishing execution order constraints where the target task waits for the source task to complete. |
| [remove_dependency](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:249) | Remove a specific dependency relationship from the constellation. This breaks the connection between two tasks without affecting the tasks themselves. |
| [update_dependency](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:274) | Update the condition description of an existing dependency relationship. This allows you to modify the explanation of why and how tasks depend on each other. |
| [build_constellation](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/local_servers/constellation_mcp_server.py:308) | Build a complete constellation from configuration data. This allows batch creation of multiple tasks and dependencies in a single operation with automatic validation. |

## 硬件模拟，不限定操作系统：HardwareCollector / HardwareExecutor（41 个）

配置分类：`data_collection / action`。用途：Arduino HID、BB-8 测试夹具、机械臂等模拟工具；当前不执行真实硬件操作。

| 工具（点击定位函数） | 源码说明 |
|---|---|
| [arduino_hid_status](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:50) | Get the current status of the Arduino HID device (dummy). |
| [arduino_hid_connect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:57) | Connect to the Arduino HID device (dummy). |
| [arduino_hid_disconnect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:65) | Disconnect from the Arduino HID device (dummy). |
| [type_text](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:75) | Type a string of text (dummy). |
| [press_key_sequence](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:87) | Press a sequence of keys (dummy). |
| [press_hotkey](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:101) | Press multiple keys simultaneously (dummy). |
| [move_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:111) | Move the mouse pointer (dummy). |
| [click_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:122) | Click the specified mouse button (dummy). |
| [press_mouse_button](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:134) | Press and hold the specified mouse button (dummy). |
| [release_mouse_button](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:141) | Release the specified mouse button (dummy). |
| [scroll_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:148) | Scroll the mouse wheel (dummy). |
| [drag_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:168) | Drag the mouse from start to end position (dummy). |
| [double_click_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:183) | Perform a double-click (dummy). |
| [right_click_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:193) | Shortcut for right mouse button click (dummy). |
| [middle_click_mouse](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:200) | Shortcut for middle mouse button click (dummy). |
| [bb8_status](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:208) | Get the current status of the BB-8 test fixture (mock). |
| [bb8_connect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:213) | Connect to the BB-8 test fixture (mock). |
| [bb8_disconnect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:218) | Disconnect from the BB-8 test fixture (mock). |
| [bb8_usb_port_plug](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:226) | Plug in a USB device to the specified port (mock). |
| [bb8_usb_port_unplug](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:233) | Unplug a USB device from the specified port (mock). |
| [bb8_psu_charger_plug](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:240) | Plug in the PSU charger (mock). |
| [bb8_psu_charger_unplug](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:245) | Unplug the PSU charger (mock). |
| [bb8_blade_attach](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:250) | Attach the blade (mock). |
| [bb8_blade_detach](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:255) | Detach the blade (mock). |
| [bb8_lid_open](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:260) | Open the lid (mock). |
| [bb8_lid_close](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:265) | Close the lid (mock). |
| [bb8_button_press](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:270) | Press a physical button (mock). |
| [bb8_button_long_press](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:277) | Long press a physical button (mock). |
| [robot_arm_status](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:285) | Get the current status of the Robot Arm (mock). |
| [robot_arm_connect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:295) | Connect to the Robot Arm (mock). |
| [robot_arm_disconnect](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:304) | Disconnect from the Robot Arm (mock). |
| [touch_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:309) | Simulate a touch at the specified location on the screen (mock). |
| [draw_on_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:316) | Simulate drawing on the screen by following a path of coordinates (mock). |
| [tap_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:328) | Simulate tap(s) at the specified location on the screen (mock). |
| [swipe_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:341) | Simulate a swipe gesture from start to end location (mock). |
| [long_press_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:354) | Simulate a long press at the specified location (mock). |
| [double_tap_screen](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:366) | Simulate a double tap at the specified location (mock). |
| [press_key](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:376) | Simulate pressing a keyboard key, optionally with modifier keys (mock). |
| [tap_trackpad](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:388) | Simulate tap(s) at the specified location on the trackpad (mock). |
| [swipe_trackpad](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:401) | Simulate a swipe gesture on the trackpad from start to end location (mock). |
| [take_screenshot](/home/zhou/GUI-Agent/UFO3/ufo/client/mcp/http_servers/hardware_mcp_server.py:414) | Simulate taking a screenshot (mock). |

HardwareCollector 和 HardwareExecutor 在当前配置中连接同一个 HTTP 地址（8006/mcp）；服务源码只有一个完整工具集合，并未按这两个名称拆成不同子集。take_screenshot 当前读取静态测试图片。
