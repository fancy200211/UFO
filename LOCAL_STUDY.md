# UFO3 本地学习环境

本地分支：`study-ufo3`，基于官方 `main` 的 `eae3c0a`。
Conda 环境：`ufo3`，Python 3.10，面向当前 Linux 主机的源码学习与调试。

## 日常进入

```bash
cd /home/zhou/GUI-Agent/UFO3
source /home/zhou/miniconda3/etc/profile.d/conda.sh
conda activate ufo3
git switch study-ufo3
python --version
```

VS Code 的 Python 解释器选择：
`/home/zhou/miniconda3/envs/ufo3/bin/python`。

## 在另一台机器重建

以下创建命令只适用于分支、环境尚不存在时：

```bash
git switch -c study-ufo3
conda create -n ufo3 python=3.10 pip -y --override-channels -c conda-forge
conda activate ufo3
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m pip check
```

Python 3.10 与仓库固定的 pandas 1.4.3 更匹配。这里选用 CPU 版 PyTorch，
不部署本地大模型，不需要下载 CUDA 运行库。依赖安装不等于下载模型权重。
Windows 专用依赖由 requirements.txt 的平台标记跳过。

## 模型配置

已从模板生成以下本地配置文件，密钥仍为占位符：

- `config/ufo/agents.yaml`：Host、App 等模型配置。
- `config/galaxy/agent.yaml`：Galaxy 编排模型配置。

这两个文件已被上游 .gitignore 忽略。运行真实任务前需要填写自己的
服务地址、可用模型名和密钥；仅阅读源码不需要填写。此环境配置过程
不启动服务、不调用模型 API、不控制设备。

## 学习分支的使用

```bash
git status
git diff
# 准备保留某项学习修改时，按具体路径添加并提交：
# git add <文件路径>
# git commit -m "Add study notes"
```

需要同步上游时，先提交或暂存自己的修改，再执行：

```bash
git fetch origin
git merge origin/main
```

当前 Linux 环境可用于学习 Galaxy、LinuxAgent 和 Android 接入代码；
Windows GUI 执行依赖 Windows 系统。Android 实际操作还需要 ADB 和设备。
