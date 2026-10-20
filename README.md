# 显影 SeeName


**看见，即命名。/ See it. Name it.**


AI 真正"看懂"你的照片和视频，按画面内容自动改出有意义的文件名，告别 IMG_2026 这类乱码。


AI that truly *sees* your photos and videos and renames them by what's actually in the frame — goodbye, IMG_2026.


- 内置开源 AI 模型，识别全程在本地运行，文件不上传任何服务器 / Bundled open-source AI model — everything runs locally, your files never leave your computer
- 中英双语界面 / Bilingual Chinese–English UI
- 按时间 + 地点 + 画面内容智能命名，可选把描述写入文件元数据 / Smart naming by time, place and image content, optional metadata writing
- 免费使用 / Free to use


作者 / By tanhua

## 当前分支：本地 GGUF 开发版 / Local GGUF development version

当前代码通过 Python 绑定在进程内调用 llama.cpp，默认使用 CPU，无需 Ollama。
This branch runs llama.cpp in-process through Python on CPU, without Ollama.

1. 使用 Python 3.12 环境，安装 Pillow、piexif，以及 `requirements-inference.txt` 固定的绑定版本：
   ```sh
   python -m pip install pillow piexif
   python -m pip install -r requirements-inference.txt
   python main.py
   ```
   绑定从固定的 Git 提交安装，需要 Git、CMake 和 C++ 构建工具；Python 还须提供 Tkinter。
   若已有 Conda `seename` 环境，也可用 `conda run -n seename python main.py` 启动。
2. 将 `Qwen3VL-4B-Instruct-Q4_K_M.gguf` 与 `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` 放在同一目录。
   默认检查 `models/` 及下一层子目录；也可在「模型管理…」选择现有目录。Q8/F16 主模型及 F16 视觉投影也可发现，实际推理本轮只验证 Q4+Q8 投影。
3. 开始识别时自动加载模型，或点击底部状态提前加载。当前不提供软件内在线下载；旧 Ollama blobs 不能直接使用。

Use Python 3.12 with Tkinter and install Pillow, piexif and the binding revision
pinned in `requirements-inference.txt` using the commands above. The pinned Git
source install needs Git, CMake and a C++ toolchain. An existing Conda `seename`
environment can also run `conda run -n seename python main.py`. Keep the main
GGUF and matching mmproj in one directory; choose it in Model manager. Models
load on demand.

视频抽帧及元数据写入仍依赖 FFmpeg，部分 HEIC 元数据写入仍可能依赖 ExifTool。
「最终安装包只有主程序一个 EXE」尚未完成；下一步是媒体依赖迁移和完整打包验证。
本轮只验证 Windows CPU，macOS/Linux 及 GPU 尚未实机验证。`build_mac.sh` 仍属于下面的旧版打包流程，尚未适配当前分支。

FFmpeg and some HEIC metadata operations still need external tools. The final
single-EXE distribution is pending media migration and full packaging checks.
Only Windows CPU has been tested; the existing macOS build script is still for
the legacy release and has not been adapted to this branch.


## 已发布的 v1.0 旧版下载 / Legacy v1.0 downloads


| 版本 Edition | 大小 Size | 说明 Notes | 获取 Get it |
|---|---|---|---|
| Slim（不含模型） | 1.36 GB | 首次使用需自行拉取模型，见下 | GitHub Releases（本页） |
| Full（含模型） | 4.22 GB | 解压即用 | 百度网盘（链接见 Release 说明） |


Slim 版首次使用前拉取模型 / For the Slim edition, pull the model first:


```
ollama pull qwen3-vl:4b-instruct
```


SHA256 校验 / checksums:


```
7740f26b7e9d13a626e416b616bb9dd65fabd83a64ef8a9efa0c29df5d27fdf5  SeeName_v1.0_slim_no_model.7z
0265d0f640681e378bc855306c6ff3d0041f30db65aac0e2450f90b9b7539f83  SeeName_v1.0_full_with_model.7z
```


## 运行配置 System requirements


- 当前源码版使用 CPU，建议 16GB 以上内存。显卡不会自动启用；耗时取决于 CPU、图片和模型。
  The current source version uses CPU inference; 16GB+ RAM is recommended. A GPU
  is not enabled automatically; speed depends on the CPU, images and model.
- 可以在设置里关掉“AI描述”总开关，只按时间 + 地点命名；照片未开启“照片也写描述”时也不加载模型。
  Turn off the "AI description" master switch to name by time and place only.
  Photo-only jobs also skip model loading when photo descriptions are disabled.


## 开源协议 License


本项目 MIT 开源。第三方组件及署名见 `licenses/` 文件夹：地名数据 © GeoNames（CC-BY 4.0）、地图数据 © OpenStreetMap 贡献者（ODbL）、FFmpeg（LGPL）、Qwen3-VL（Apache-2.0）。


This project is MIT licensed. Third-party components and attributions are in the `licenses/` folder.
