"""Optional, checksum-verified CUDA backend and runtime component."""
from .backend_component import NativeBackendComponent


CUDA_RUNTIME_FILES = ('cudart64_12.dll', 'cublas64_12.dll', 'cublasLt64_12.dll')


class CudaComponent(NativeBackendComponent):
    def __init__(self, config, save_config, *, catalog=None, root=None):
        super().__init__(config, save_config, backend='CUDA', plugin='ggml-cuda.dll',
                         config_key='cuda_enabled', catalog=catalog, root=root,
                         extra_files=(*CUDA_RUNTIME_FILES, 'LICENSE-NVIDIA-CUDA.pdf'))
