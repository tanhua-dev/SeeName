# Optional CUDA backend (Windows x64)

The application keeps its CPU inference runtime. This optional backend is
downloaded separately and contains the matching `ggml-cuda.dll`, CUDA 12.4
runtime libraries, and both llama.cpp and NVIDIA license documents. The NVIDIA
display driver supplies `nvcuda.dll`; users do not need to install the CUDA
Toolkit. A compatible NVIDIA GPU and a sufficiently recent NVIDIA driver are
required. Initialization errors fall back to CPU when the native library
returns safely.

The CUDA runtime libraries are redistributed under the CUDA Toolkit EULA. The
complete EULA is included in the component ZIP and should be reviewed before
redistributing the component. NVIDIA lists cudart and cuBLAS libraries among
the redistributable Toolkit components: <https://docs.nvidia.com/cuda/eula/>.

## Build

Use Windows x64, Python 3.12, Visual Studio 2022 C++ tools, CMake, Ninja, the pinned
CPU binding requirements, and CUDA Toolkit 12.4. The builder fetches the exact
llama.cpp source archive pinned to the installed binding, verifies its SHA-256,
builds the dynamic backend, and packages only the backend, required runtime
DLLs, and licenses. The source ZIP contains the complete upstream source
archive and build recipe. It does not contain the toolkit or compiler.
When available, `ccache` is used through CMake's compiler launcher. Compiler
output is streamed to the terminal and retained in `build.log`.

```powershell
$env:CMAKE_ARGS = '-DGGML_NATIVE=OFF -DGGML_CUDA=OFF -DGGML_VULKAN=OFF'
python -m pip install -r requirements-windows-build.txt
python -B scripts/build_cuda.py --version local1
```

Local builds set no online download URL and publish nothing. GitHub Actions
builds both optional backends and embeds their trusted catalogs in the
application. Hosted runners compile/package CUDA but do not provide an NVIDIA
GPU; inference must be checked on a compatible local machine.

For a hardware check, place the extracted CUDA component under
`%LOCALAPPDATA%\SeeName\backends\<component-id>` and run the packaged app:

```powershell
.\SeeName.exe --package-check .\cuda-check.json `
  --check-cuda-directory "$env:LOCALAPPDATA\SeeName\backends" `
  --check-model-directory <model-directory>
```

The report records discovered CUDA
devices and an image inference result. A preview Actions artifact has no
download URL in its catalog; extract the component manually for this check. A
prerelease tag exercises the full in-app HTTPS download path.

CUDA 12.x minor-version compatibility requires an NVIDIA driver at least
528.33 on Windows x64; newer toolkit/runtime versions can require a newer
driver. See NVIDIA's current compatibility guide and toolkit release notes
before publishing: <https://docs.nvidia.com/deploy/cuda-compatibility/>.
