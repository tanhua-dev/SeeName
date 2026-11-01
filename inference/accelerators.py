"""Select exactly one optional GPU backend before creating the model context."""


class AcceleratorSelector:
    def __init__(self, config, save_config=None, **components):
        self.config = config
        self.save_config = save_config
        self.components = components
        self.active_name = ''
        # 这轮启用是「启动时我替你挑的」还是「你自己点的」。
        # 只有前者在加载失败时才允许自动退回 CPU：用户明确选过的，不能背着他改。
        self._auto = False

    @property
    def active(self):
        enabled = [name for name in self.components
                   if self.config().get(name.lower()+'_enabled', False)]
        return enabled[0] if len(enabled) == 1 else ''

    @property
    def error(self):
        component = self.components.get(self.active_name or self.active)
        return component.error if component else ''

    @error.setter
    def error(self, value):
        component = self.components.get(self.active_name or self.active)
        if component:
            component.error = value

    def load(self):
        name = self.active
        if not name:
            self.active_name = ''
            return False
        self.active_name = name
        return self.components[name].load()

    def status(self, name):
        return self.components[name].status()

    def enable(self, name, enabled, component_id=None):
        self._auto = False      # 用户自己在面板上点的选择
        if enabled:
            for other, component in self.components.items():
                component.enable(other == name, component_id if other == name else None)
        else:
            self.components[name].enable(False)
        self._remember(name if enabled else 'cpu')

    def enable_cpu(self):
        self._auto = False
        for component in self.components.values():
            component.enable(False)
        self._remember('cpu')

    def _remember(self, pick: str):
        """记下用户这次的选择，之后就不再用「首次自动选」去覆盖它。"""
        if self.save_config is None:
            return
        try:
            self.save_config(accel_pick=pick)
        except Exception:
            pass

    def autopick(self) -> str:
        """首次运行时按硬件挑一次加速后端；用户选过就不再插手。

        发行包不带 GPU 运行库，所以默认是 CPU——但这不等于「假设你没有独显」：
        只要加速组件已经下载到本地（backends/ 里有对应后端，目录跟随软件目录走），
        第一次启动就自动启用它，用户不用先去设置里点一下。挑的顺序是 CUDA →
        Vulkan，两个都没装就继续用 CPU。

        注意：什么都没装时**不写** accel_pick。否则用户以后下载了组件，也会被
        这条「已经决定过用 CPU」的记录挡住，永远开不起来。只有用户显式选过
        （enable / enable_cpu）才落盘，那是真的选择。
        模型加载失败时 llama_backend 会自动退回 CPU 并在加速面板写明原因。
        """
        try:
            decided = str((self.config() or {}).get('accel_pick') or '')
        except Exception:
            decided = ''
        if decided:
            return '' if decided == 'cpu' else decided
        for name in ('CUDA', 'Vulkan'):
            component = self.components.get(name)
            if component is None:
                continue
            try:
                if not component.status().get('installed'):
                    continue
            except Exception:
                continue
            self.enable(name, True)
            self._auto = True       # enable() 把它清成了「用户选择」，这里改回「我挑的」
            self.active_name = name
            return name
        return ''

    def demote_to_cpu(self) -> bool:
        """启动时自动挑中的后端实际跑不起来：退回 CPU 并记住，下次别再白试一遍。

        只对 autopick 生效。用户自己在加速面板点过「Vulkan / CUDA」，那是明确
        的选择，失败原因照旧留在面板里让他自己判断，不能背着他改。
        """
        if not self._auto:
            return False
        name = self.active_name or self.active
        component = self.components.get(name)
        # enable_cpu() 会把各组件的 error 清空，失败原因得先留一份再写回去，
        # 否则用户下次打开加速面板只看到「未启用」，不知道为什么被退回来。
        reason = component.error if component else ''
        self.enable_cpu()
        if component is not None and reason:
            component.error = reason
        return True

    def install(self, name, progress=None, should_stop=None, component_id=None):
        return self.components[name].install(progress, should_stop, component_id)
