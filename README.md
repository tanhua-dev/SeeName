# 显影 SeeName


**看见，即命名。/ See it. Name it.**


AI 真正"看懂"你的照片和视频，按画面内容自动改出有意义的文件名，告别 IMG_2026 这类乱码。


AI that truly *sees* your photos and videos and renames them by what's actually in the frame — goodbye, IMG_2026.


- 内置开源 AI 模型，识别全程在本地运行，文件不上传任何服务器 / Bundled open-source AI model — everything runs locally, your files never leave your computer
- 中英双语界面 / Bilingual Chinese–English UI
- 按时间 + 地点 + 画面内容智能命名，可选把描述写入文件元数据 / Smart naming by time, place and image content, optional metadata writing
- 免费使用 / Free to use


作者 / By tanhua


## 下载 Download


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


- 推荐 Recommended：NVIDIA 独立显卡（6GB 以上显存）、16GB 以上内存，AI 识别速度快。
  A dedicated NVIDIA GPU (6GB+ VRAM) and 16GB+ RAM for fast AI recognition.
- 配置较低或无独显 Low-end / no dedicated GPU：机器能运行，但 AI 识别会变慢；可以在设置里关掉“AI描述”总开关，软件会只按时间 + 地点命名，一样能用。
  The app still works; turn off the "AI描述" (AI description) master switch in settings and files will be named by time + place only, without calling the AI model.


## 开源协议 License


本项目 MIT 开源。第三方组件及署名见 `licenses/` 文件夹：地名数据 © GeoNames（CC-BY 4.0）、地图数据 © OpenStreetMap 贡献者（ODbL）、FFmpeg（LGPL）、Qwen3-VL（Apache-2.0）。


This project is MIT licensed. Third-party components and attributions are in the `licenses/` folder.
