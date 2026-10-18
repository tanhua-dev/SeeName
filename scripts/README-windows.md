# SeeName · Windows x64 CPU portable edition

解压整个 SeeName 文件夹，双击 SeeName.exe。请保留 _internal 文件夹；它包含
Python、Tk、llama.cpp、PyAV 和其他必要动态库，不需要另外安装 Python 或 Ollama。

此包不含模型。首次启动未发现模型时会提示打开模型管理，支持在线下载 4B / 2B Q4 / Q8
主模型和投影文件。可选择 Hugging Face 或 ModelScope，支持取消和续传，并校验 SHA-256。
默认下载到 %LOCALAPPDATA%\SeeName\models 下的 Qwen3-VL-4B-Instruct-GGUF 或
Qwen3-VL-2B-Instruct-GGUF。可选择已有模型目录；自定义目录也作为下载目标。
仍会发现程序旁原有 models 目录中的模型，已有 F16 主模型可继续手动导入。

此版本禁用文件元数据写回，旧配置也不能开启。识别、核对改名、.ai.txt 和
目录说明功能保留。视频读取通过 PyAV 完成，不需要 FFmpeg 命令行。
模型使用 CPU，性能取决于硬件和图片。HEIC 解码未作为本包的已验证功能。
如果此版本附带已发布的 Vulkan 组件清单，可在「模型管理 → GPU 加速」按需下载。
组件保存到 %LOCALAPPDATA%\SeeName\backends，不含额外 EXE；需要兼容的显卡驱动。

Extract the entire SeeName folder and run SeeName.exe. Keep _internal intact.
No separate Python, Ollama, FFmpeg CLI or ExifTool installation is required.
Models are external. Model manager can download 4B / 2B Q4 / Q8 pairs from Hugging Face
or ModelScope with cancellation, resume and SHA-256 verification. The default root
is %LOCALAPPDATA%\SeeName\models; a custom model directory also becomes the download target.
This build disables file metadata writes, including when old settings enable
them. Renaming and optional text/Markdown descriptions remain available.

The package is unsigned. See BUILD-INFO.json for the version, source revision,
dependency versions and checks performed for this build. Some builds check only
library initialization; model_inference records whether real weights were tested.
