"""Optional Vulkan backend compatibility wrapper."""
from .backend_component import (
    BINDING_COMMIT,
    LLAMA_COMMIT,
    NATIVE_FILES,
    NativeBackendComponent,
    default_root,
    digest,
    runtime_directory,
)


class VulkanComponent(NativeBackendComponent):
    def __init__(self, config, save_config, *, catalog=None, root=None):
        super().__init__(config, save_config, backend='Vulkan', plugin='ggml-vulkan.dll',
                         config_key='vulkan_enabled', catalog=catalog, root=root)
