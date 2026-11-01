# 第三方组件声明 / Third-Party Notices

**显影 SeeName（SeeName.exe）** — 作者：tanhua
本产品自身代码（`main.py` / `engine.py` / `strings.py`）采用 **MIT License**，见 `LICENSE`（全文亦随包附于 `licenses/LICENSE-MIT.txt`）。
本产品随附或依赖的第三方组件如下，各自的许可条款优先适用于对应组件。

## 当前 GGUF 开发分支 / Current GGUF development branch

当前代码直接使用 llama-cpp-python / llama.cpp，不启动 Ollama。绑定固定为
JamePeng/llama-cpp-python 的 `ce261d12b5810fb4c6e695efc85d5e8cf23f0630`，
安装分发中的 MIT 许可证见 `licenses/LICENSE-llama-cpp-python.txt`。
模型使用本地 Qwen3-VL-4B-Instruct GGUF 主模型及 mmproj 视觉投影文件。
v2.0 起提供两个便携包（Vulkan 不含模型、CUDA 含模型），每个包实际包含哪些文件、
来自哪个组件版本，都写在包内的 `BUNDLE-INFO.json` 里。
视频读取使用 PyAV 19.0.1，在进程内调用其 FFmpeg 动态库；PyAV 分发许可见
`licenses/LICENSE-PyAV.txt`。视频元数据写入目前仍使用 FFmpeg 命令行。
PyAV 自身许可不能替代其所链接 FFmpeg 及其他原生库的分发要求，具体构建清单在打包阶段核对。
以下 Ollama 运行时与发布包大小、构建信息描述的是既有旧版发行包。

### v2.0 加速组件 / v2.0 GPU backend components

- **Vulkan 组件**（`ggml-vulkan.dll`）：llama.cpp 的 Vulkan 后端，由本项目按固定的
  上游提交（`27c7a769da1671c97b2e3f6cb88704a6e1e0d0cd`）从源码构建。
  许可 **MIT**，全文随组件附于 `backends/<组件 id>/LICENSE-llama.cpp.txt`。
  MIT 的源码提供义务已通过同版本发布的 `vulkan-*-source.zip` 满足。
- **CUDA 组件**（`ggml-cuda.dll` + `cudart64_12.dll` / `cublas64_12.dll` /
  `cublasLt64_12.dll`）：`ggml-cuda.dll` 同样由本项目从上述提交构建，许可 MIT。
  三个 NVIDIA 运行时库来自 **CUDA Toolkit 12.4**，按 NVIDIA CUDA Toolkit 的
  再分发条款（EULA 附件 A）随产品分发，条款全文随组件附于
  `backends/<组件 id>/LICENSE-NVIDIA-CUDA.pdf`。本产品未修改这些库，
  也不对其提供任何担保；使用它们需要单独接受 NVIDIA 的条款。
- 两个组件都只是 llama.cpp 的动态后端插件，不含独立的可执行程序。

The Vulkan and CUDA `ggml-*` plugins are built from the pinned llama.cpp commit and are
MIT licensed, with the license text shipped alongside each component. The three NVIDIA
runtime DLLs in the CUDA component come from CUDA Toolkit 12.4 and are redistributed under
the CUDA Toolkit EULA (Appendix A), whose text ships with the component as
`LICENSE-NVIDIA-CUDA.pdf`. No NVIDIA library is modified, and no warranty is provided.

The current development branch uses llama-cpp-python / llama.cpp in-process.
The binding revision is pinned in `requirements-inference.txt`; its distributed
MIT license is included in `licenses/LICENSE-llama-cpp-python.txt`. Full-package
native dependency inventory is pending. Ollama release details below describe
the legacy distribution.

Video reading uses PyAV and its native FFmpeg libraries in-process. PyAV's
distributed license is included in `licenses/LICENSE-PyAV.txt`; linked native
libraries require their own packaging inventory. Video metadata writing still
uses the FFmpeg CLI.

---

## 一、模型权重（随产品分发的 `models\` 目录）

### Qwen3-VL-4B-Instruct

- 版权：Copyright the Qwen Team, Alibaba Cloud
- 许可：**Apache License 2.0**（全文见 `licenses/LICENSE-Apache-2.0.txt`）
- 分发形式：经 **Ollama** 量化后分发的 `qwen3-vl:4b-instruct`（约 3.3 GB）
- 状态：**未修改**模型结构与权重内容，仅采用其量化分发版本
- 本产品的使用符合 Apache-2.0：允许商用、修改与再分发；已随附许可证全文并保留版权声明
- ⚠️ Apache-2.0 **不授予商标权**：「Qwen」「通义千问」为阿里巴巴集团的商标，
  本产品仅在界面中以事实性方式标注模型技术名称，不构成商标使用或背书宣称

> Apache-2.0 要求：随附许可证全文、保留版权声明、有重大修改须声明。
> 以上三项均已满足（见本文件与 `licenses/LICENSE-Apache-2.0.txt`）。

---

## 二、推理运行时

### Ollama（随产品分发的 `runtime\ollama`）

- 版权：Copyright Ollama 贡献者
- 许可：**MIT License**
- 用途：本地模型推理服务，监听本机回环地址，不对外提供网络访问

### llama.cpp（运行时在 `models\` 内本地生成的运行包）

- 许可：**MIT License**
- 说明：首次加载模型时由 Ollama 运行时在本机生成（`runner: llamacpp`），非网络下载

### FFmpeg（随产品分发的 `ffmpeg.exe`）

- 版权：Copyright (c) 2000-2026 the FFmpeg Developers
- 随附构建：`N-127367-gb5902e69c7-20261011`（BtbN / FFmpeg-Builds `win64-lgpl` 静态构建）
  - sha256：`8848564c91c04f139e35579c672f45a37010392b0e342f20c6529a7ab7a9f5f1`
  - 编译参数**不含 `--enable-gpl`**；x264 / x265 / xvid / fdk-aac 等仅 GPL 组件均已 `--disable-*`
  - 许可：**GNU Lesser General Public License v3.0（LGPL-3.0）**
    （全文见 `licenses/LICENSE-LGPL-3.0.txt`）
  - 📌 LGPL-3.0 正文第 7-9 行声明其"按引用并入 GPL-3.0 的条款与条件"，
    因此随包**同时附上 GPL-3.0 全文**：`licenses/LICENSE-GPL-3.0.txt`
    （两份文本均取自 <https://www.gnu.org/licenses/>，与官方逐字节一致）
- 提供 LGPL-3.0 所要求的源码途径：
  - 官方源码：<https://ffmpeg.org/download.html>
  - 该构建的完整构建脚本（可复现本二进制）：<https://github.com/BtbN/FFmpeg-Builds>
- 调用方式：本产品以**命令行子进程**调用 FFmpeg，二者为相互独立的程序，
  本产品自身代码（MIT）不因调用而受 LGPL 约束
- 📌 变更记录（v3.12）：原随附 `7.1-essentials_build-www.gyan.dev`（**GPL-3.0**），
  因其中含 x264 / x265 / xvid 等仅 GPL 组件，整体分发需按 GPL-3.0 提供源码；
  本产品仅使用**解码 + 缩放 + MJPEG 输出**，不需要这些组件，故已替换为 LGPL-3.0 构建。

---

## 三、Python 运行时与库（打包进 `SeeName.exe`）

| 组件 | 许可 | 说明 |
|---|---|---|
| CPython | PSF License Agreement | Python 解释器运行时 |
| Pillow | HPND (Historical Permission Notice and Disclaimer) | 图像解码、缩放、EXIF 方向校正 |
| piexif | MIT License | JPEG EXIF 读写（描述落盘：ImageDescription / XP 系列） |
| Tk / Tcl | TCL-style License | 图形界面工具包 |

### PyInstaller（**仅构建工具，不随产品分发**）

- 许可：GPL v2（含例外条款，允许用于打包非 GPL 程序）
- 说明：仅用于将源码打包为可执行文件，其源码不包含在分发产物中

---

## 四、技术渊源（非代码引用，仅为署名）

### Video AI Renamer（video-ai-renamer-win）

- 版权：Copyright (c) 2026 Jiachenghao6
- 许可：**MIT License**
- 说明：本项目的早期原型曾调用该软件的抽帧模块。
  **现版本已完全重写，不含其任何代码、标识符或对其的任何导入依赖**，
  二者实现路径不同（本项目：关键帧抽取 + 多帧宫格一次送模型）。
  此处保留署名，仅为说明技术渊源与致谢。

---

## 五、数据来源（`data\districts.csv` / `data\cities_world.csv`）

### 中国：县级点位（`data\districts.csv`，约 3300 条）

- **地图数据 © OpenStreetMap 贡献者（https://www.openstreetmap.org/copyright），
  遵循 ODbL 许可证**（<https://opendatacommons.org/licenses/odbl/1-0/>）
- 署名文件（随包分发）：`licenses/ATTRIBUTION-OpenStreetMap.txt`
- 来源构成：
  1. **名录**：`modood/Administrative-divisions-of-China`（**WTFPL** 许可，
     系对国家统计局 2023 年行政区划代码的整理）→ 342 个地级单位、3056 个县级单位
  2. **坐标**：OSM 县级行政区（`boundary=administrative` + `admin_level=6`）边界中心点，
     经 Overpass API 按省抓取；OSM 未覆盖的县与港澳台由 GeoNames 补充（见下）
- 允许：复制、分发、商用；要求：① 署名；② 衍生数据库仍以 ODbL 授权
- 因此**本数据文件以 ODbL 1.0 分发**；它与本产品代码的 MIT 许可相互独立，
  二者不构成衍生关系（可执行文件不因读取数据文件而受 ODbL 约束）
- 构建脚本（可复现）：`_build_districts_v2.py` / `_osm_counties.py` /
  `_osm_extra_straight.py` / `_build_world_cities.py`

### 境外与港澳台：全球城市库（`data\cities_world.csv`，10.7 万条）

- 数据源：**GeoNames**（<https://www.geonames.org/>）`cities1000`
- 许可：**Creative Commons Attribution 4.0（CC-BY 4.0）**
  <https://creativecommons.org/licenses/by/4.0/>
- 署名文件（随包分发）：`licenses/ATTRIBUTION-GeoNames.txt`
- 处理方式：仅保留人口 ≥ 2000 的点，精简为
  `名称,纬度,经度,国家码,人口,中文名` 六列，
  属剔除了大量显式属性后的**提取/再组织**结果（CC-BY 允许修改与再分发，仅需署名）
- **境外城市显示中文名**时，中文来自两处：
  ① 随构建脚本固化的手工补表（覆盖国人常去的热门城市与各国首都）；
  ② GeoNames `alternatenames` 中的纯汉字候选（繁转简后取最短者）。
  GeoNames 未收录中文别名的城市仍显示拉丁原名（如 `法国 Paris`）

### ⚠️ 已弃用的旧数据源（均已从分发包中移除）

**① v3.12 及更早**：`districts.json` 由 `Sanotsu/regional-coordinates-of-China`
清洗而来，存在两处授权瑕疵：

1. 该上游项目**未附任何 LICENSE 文件**，默认视为「保留所有权利」；
2. 其坐标据作者说明系批量调用**百度地图 JavaScript API 地理编码**获取，
   百度开放平台服务条款通常不允许将地理编码结果落库或再分发。

**② v3.13**：曾改用 OpenStreetMap 的 `place` 点位（city / town / suburb / quarter，
约 4 万条乡镇街道级）。该源本身为 ODbL-1.0、授权清晰，但**市建成区覆盖稀疏**
（成都主城仅 2 个街道点），且颗粒度不一致，故同样弃用。

> 若你拿到的是 v3.13 或更早的分发包，`data\districts.json` 属于上述旧源，
> 请勿对外再分发该文件。

---

## 六、关于本软件开发方式的说明

本产品的代码由作者借助 AI 编码助手（WorkBuddy / CodeBuddy）辅助生成并人工审校。
相关服务的条款并未主张对所生成内容的所有权；本产品运行时**不调用任何第三方 AI 服务的 API**，
全部推理在本机完成。
本产品未经腾讯或任何第三方背书，也不得被宣称为由任何第三方提供或认可。
