# 显影 SeeName


**看见，即命名。/ See it. Name it.**


AI 真正"看懂"你的照片和视频，按画面内容自动改出有意义的文件名，告别 IMG_2026 这类乱码。


AI that truly *sees* your photos and videos and renames them by what's actually in the frame — goodbye, IMG_2026.


- 内置开源 AI 模型，识别全程在本地运行，文件不上传任何服务器 / Bundled open-source AI model — everything runs locally, your files never leave your computer
- 中英双语界面 / Bilingual Chinese–English UI
- 按时间 + 地点 + 画面内容智能命名，可选把描述写入文件元数据 / Smart naming by time, place and image content, optional metadata writing
- 免费使用 / Free to use


作者 / By tanhua

## v2.0 便携发行版 / Portable builds

v2.0 起不再依赖 Ollama：推理由 Python 绑定在进程内调用 llama.cpp，模型是本地 GGUF，
加速组件是单独的 llama.cpp 后端插件（`ggml-vulkan.dll` / `ggml-cuda.dll`），
放在软件目录的 `backends/` 下，跟着文件夹一起搬走仍然有效。模型放在同级的 `models/`。
A single portable folder: the app, the optional GPU backend plugin and the GGUF models
all live side by side, so moving the folder keeps everything working.

发行两个包 / two editions:

| 版本 Edition | 文件名 File | 内含 Contents | 大小 Size | 适用 Who |
|---|---|---|---|---|
| Vulkan 版（不含模型） | `SeeName-v2.0.0-vulkan.zip` | 程序 + Vulkan 加速组件 | 约 260 MB | 显卡型号不确定、想自己选模型 |
| CUDA 版（含模型） | `SeeName-v2.0.0-cuda-full.zip` | 程序 + CUDA 加速组件 + 4B Q4 模型 | 约 4.1 GB | NVIDIA 显卡，解压即用 |

两个包都是解压即用，都不需要另外安装 Python、Ollama 或 FFmpeg。
用 zip 而不是 7z：Windows 双击就能解，不用先装解压工具；包内是量化 GGUF 和 DLL，
换 7z 也只能省 1～3%，不值得让用户多一道门槛。单文件不分卷，附同名 `.sha256` 校验。

本地怎么组装 / how the bundles are assembled:

```sh
# 先由 CI 或本机 build_windows.py 产出通过验收的应用目录，再拼装
python -B scripts/assemble_portable.py --app-dir build/windows/<id>/dist/SeeName \
    --version v2.0.0 --flavor vulkan --backend vulkan \
    --component-dir backends/vulkan-<id> --output dist/bundles
python -B scripts/assemble_portable.py --app-dir build/windows/<id>/dist/SeeName \
    --version v2.0.0 --flavor cuda-full --backend cuda \
    --component-dir backends/cuda-<id> --models-dir models --output dist/bundles
```

产出 `SeeName-v2.0.0-vulkan.zip`、`SeeName-v2.0.0-cuda-full.zip` 和各自的 `.sha256`。

组装时会按应用内置的可信清单逐个核对组件的文件名、大小和 SHA-256，对不上直接拒绝，
不会等到用户解压之后才在界面上说"加速用不了"。

## 加速组件与硬件判断 / Backend and hardware detection

加速组件不随 CPU 包分发，也不从网络静默启用。首次启动按以下顺序判断一次：
A GPU backend is never enabled silently. On first start:

1. `config.json` 里的 `accel_pick` 有记录（你在面板上选过）→ 照旧。
2. 没有记录 → 在 `backends/` 里按 CUDA → Vulkan 找已安装的组件，找到就启用。
3. 一个都没有 → 用 CPU，且**不写** `accel_pick`，这样以后装了组件还能自动启用。

自动启用的组件如果实际加载不起来（没有可用显卡、驱动太旧、DLL 被拦），会立刻退回 CPU、
把原因写进「设置 → GPU 加速」，并把这次选择改记为 CPU —— 下次启动不会再把同一个失败重放一遍。
用户自己在面板上选的后端失败时不做这个改写，原因照旧留在面板里。

If an automatically picked backend fails to load, SeeName falls back to CPU, keeps the
reason in the acceleration panel and records `accel_pick=cpu`, so the same failure is not
retried on every start. A backend the user picked explicitly is never rewritten.

组件怎么构建、版本怎么匹配、下载清单怎么发布，见 [Vulkan 组件说明](scripts/README-vulkan.md)
和 [CUDA 组件说明](scripts/README-cuda.md)。

## 已发布的 v1.0 旧版下载 / Legacy v1.0 downloads

> v1.0 使用 Ollama 运行时，与当前代码架构不同，仅作历史版本保留。

1. 使用 Python 3.12 环境，安装 Pillow、piexif，以及推理和媒体依赖：
   ```sh
   python -m pip install pillow piexif
   python -m pip install -r requirements-inference.txt
   python -m pip install -r requirements-media.txt
   python main.py
   ```
   绑定从固定的 Git 提交安装，需要 Git、CMake 和 C++ 构建工具；Python 还须提供 Tkinter。
   若已有 Conda `seename` 环境，也可用 `conda run -n seename python main.py` 启动。
2. 将 `Qwen3VL-4B-Instruct-Q4_K_M.gguf` 与 `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` 放在同一目录。
   也可在「模型管理…」选择现有目录，检查该目录及下一层子目录。未指定目录时，也兼容程序旁原有的 `models/`。Q8/F16 主模型及 F16 视觉投影也可发现；此前实际推理已验证 4B Q4+Q8 投影。
3. 开始识别时自动加载模型，或点击底部状态提前加载。没有模型时会提示打开模型管理，可下载 4B / 2B Q4 / Q8 模型及配套投影，或选择本地目录；旧 Ollama blobs 不能直接使用。

在线下载支持 Hugging Face（国外）和 ModelScope（国内），使用 Python HTTPS，不调用外部程序。
下载到 `.part` 文件，支持取消、重新点击继续；校验固定文件大小和 SHA-256 后才启用。
默认保存地址：Windows `%LOCALAPPDATA%\SeeName\models`；macOS `~/Library/Application Support/SeeName/models`；Linux `${XDG_DATA_HOME:-~/.local/share}/SeeName/models`。
其下分别使用 `Qwen3-VL-4B-Instruct-GGUF` 和 `Qwen3-VL-2B-Instruct-GGUF` 子目录。
自定义模型目录同时作为下载目标；未配置自定义目录时也会检查程序旁原有的 `models`，无需移动已有文件。
含配套 Q8 投影：4B Q4 约 2.75 GiB、4B Q8 约 4.41 GiB，2B Q4 约 1.45 GiB、2B Q8 约 2.12 GiB。同规格的 Q4 / Q8 共用投影，已校验的文件不会重复下载；2B 已接入模型发现和选择，实际推理效果仍需实机验证。

Use Python 3.12 with Tkinter and install Pillow, piexif and the dependencies
pinned in `requirements-inference.txt` and `requirements-media.txt` using the
commands above. The pinned Git
source install needs Git, CMake and a C++ toolchain. An existing Conda `seename`
environment can also run `conda run -n seename python main.py`. Keep the main
GGUF and matching mmproj in one directory; choose it in Model manager. Models
load on demand.

视频信息、拍摄时间/GPS 和抽帧通过 PyAV 在进程内读取，不需要独立 FFmpeg 程序。
抽帧保留关键帧优先和定位回退，并处理显示旋转、镜像及像素宽高比。
视频元数据写入仍依赖 FFmpeg 命令行，部分 HEIC 元数据写入仍可能依赖 ExifTool。
「最终安装包只有主程序一个 EXE」尚未完成；元数据写入迁移和完整打包验证另行处理。
本轮只验证 Windows CPU，macOS/Linux 及 GPU 尚未实机验证。`build_mac.sh` 仍属于下面的旧版打包流程，尚未适配当前分支。

Video metadata reading and frame extraction now use PyAV in-process, with
keyframe-first sampling, seek fallback and display orientation/aspect-ratio
handling. Video metadata writes and some HEIC operations still need external
tools. The final single-EXE distribution is pending metadata migration and full
packaging checks. HDR tone mapping and real phone-video compatibility have not
been validated.
Only Windows CPU has been tested; the existing macOS build script is still for
the legacy release and has not been adapted to this branch.


## 已发布的 v1.0 旧版下载 / Legacy v1.0 downloads


| 版本 Edition | 大小 Size | 说明 Notes | 获取 Get it |
|---|---|---|---|
| Slim（不含模型） | 1.36 GB | 首次使用需自行拉取模型，见下 | [上游 GitHub Releases](https://github.com/tanhua-dev/SeeName/releases) |
| Full（含模型） | 4.22 GB | 解压即用 | 百度网盘（链接见[上游 Release 说明](https://github.com/tanhua-dev/SeeName/releases)） |


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


- 默认先用 CPU，建议 16GB 以上内存；装了加速组件的机器首次启动会自动启用显卡。
  耗时取决于 CPU/GPU、图片和模型。CUDA 版需要 NVIDIA 显卡和 527.41 以上驱动。
  CPU by default; a machine that already has a backend component gets it enabled on
  first start. The CUDA edition needs an NVIDIA GPU with driver 527.41 or newer.
- 可以在设置里关掉“AI描述”总开关，只按时间 + 地点命名；照片未开启“照片也写描述”时也不加载模型。
  Turn off the "AI description" master switch to name by time and place only.
  Photo-only jobs also skip model loading when photo descriptions are disabled.


## 开源协议 License


本项目 MIT 开源。第三方组件及署名见 `licenses/` 文件夹：地名数据 © GeoNames（CC-BY 4.0）、地图数据 © OpenStreetMap 贡献者（ODbL）、FFmpeg（LGPL）、Qwen3-VL（Apache-2.0）。


This project is MIT licensed. Third-party components and attributions are in the `licenses/` folder.
