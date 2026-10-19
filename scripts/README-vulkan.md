# Optional Vulkan backend (Windows x64)

The base application remains CPU capable. Model manager → GPU acceleration
offers a separately downloaded Vulkan plugin when a compatible catalog is
bundled. No helper EXE, CUDA Toolkit or model duplication is required.

The plugin is experimental. A working vendor Vulkan driver and compatible GPU
are required. Python-visible initialization/inference failures retry on CPU;
native driver crashes cannot be recovered inside the application process.
macOS/Linux artifacts are not provided by this build script.

## Build and release

Use the same Python 3.12 environment and exact CPU binding used for the base
Windows build (`requirements-windows-build.txt`). Install VS 2022 C++ tools,
CMake and a Vulkan SDK including glslc and SPIRV-Headers on the build machine.
These build tools are never distributed with the component.

```powershell
python -B scripts/build_vulkan.py --vulkan-sdk C:\VulkanSDK\<version> --version v1 --download-base-url https://github.com/tanhua-dev/SeeName/releases/download/<release-tag>
```

The script fetches a checksum-pinned llama.cpp archive at the exact submodule
revision of the application binding, builds `ggml-vulkan` as a dynamic backend,
and produces a binary ZIP, source ZIP, their SHA-256 files and a catalog under
`dist/backends`. The binary ZIP contains only `ggml-vulkan.dll` and the upstream
MIT license. The source ZIP is a separate release asset; runtime users do not
need to download it. Every successful build produces both artifacts.
The operating system supplies `vulkan-1.dll` through the graphics driver.

The source ZIP includes the complete checksum-pinned upstream source archive
(all C++ sources, CMake files and Vulkan shaders), the SeeName build scripts and
all their Python helpers, pinned dependency requirements, licenses, the matching
binary catalog snapshot and SOURCE-INFO.json. The build record captures actual
CMake/compiler/glslc versions, compile options and file hashes without host paths.
Upstream sources are retained as their original ZIP so they can be inspected by
extracting it or passed directly to `--source-archive` for an offline source build.
Compilers, SDKs and Python dependencies still need to be installed separately.

For an already-built component, produce its source bundle without recompiling:

```powershell
python -B scripts/package_vulkan_source.py --source-archive <pinned-source.zip> --catalog dist/backends/<component>.catalog.json --build-directory <build-work>/native --vulkan-sdk C:\VulkanSDK\<version>
```

Extract the source bundle and follow README-SOURCE.md to rebuild. A fresh CPU
runtime build may have different DLL fingerprints, so build the matching base
application and catalog together. Byte-identical binaries across toolchains are
not promised. Source and binary filenames/checksums identify the release pair.

Before publication, audit DLL dependencies and test on a clean Windows machine.
Test 4B/2B images, model switching, insufficient memory, invalid/missing drivers,
CPU selection, restart and frozen-package loading. NVIDIA/AMD/Intel devices need
separate coverage; compilation and discovery alone do not establish support.

The frozen application supports an explicit offline acceptance check:

```powershell
SeeName.exe --package-check result.json --check-vulkan-directory <backends-root> --check-model-directory <models-root>
```

This requires a compatible installed component and GPU, uses only the supplied
directories for model/plugin lookup, and records the actual inference device.

Download links for SeeName components use the upstream repository,
https://github.com/tanhua-dev/SeeName/releases, including when building in a fork.
Model weights and third-party build dependencies retain their provider URLs.
Only configure a release tag after its component assets have been published.

Upload both binary and source ZIPs with their checksums to the same upstream Release,
verify the URLs and SHA-256 values, then bundle
the generated catalog when building the application:

```powershell
.\build_windows.ps1 -Version v1.1.0 -VulkanCatalog .\dist\backends\<component>.catalog.json
```

For source development, copy that catalog to `inference/vulkan-catalog.json`.
Without a download URL the catalog is a local development artifact; downloading
stays disabled. The repository default catalog is empty until a real artifact
has been published. No placeholder network endpoint is contacted.

## Install and compatibility

Components live under `%LOCALAPPDATA%\SeeName\backends\<component-id>`.
The download uses HTTPS, supports cancellation/resume, and checks archive and
extracted file sizes/SHA-256. Installation uses a staging directory and is
activated only after validation. Model/config directories are not modified.

The catalog is trusted application data, not downloaded unsigned metadata.
It pins the binding commit, llama.cpp commit and exact CPU-native DLL hashes.
Changing the base runtime requires rebuilding the component/catalog. The loaded
CPU DLLs are not overwritten; the plugin is registered through the existing
binding's `ggml_backend_load` interface. Selecting CPU releases the model and
disables GPU layer, KV and operation offload, and the vision projector uses CPU.

Do not overwrite an installed/loaded component in place. Publish a new immutable
component ID for each update. Failed/cancelled downloads preserve partial data;
hash mismatches remove the partial so a retry starts cleanly.
