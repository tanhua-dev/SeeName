"""Select exactly one optional GPU backend before creating the model context."""


class AcceleratorSelector:
    def __init__(self, config, **components):
        self.config = config
        self.components = components
        self.active_name = ''

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

    def enable(self, name, enabled):
        if enabled:
            for other, component in self.components.items():
                component.enable(other == name)
        else:
            self.components[name].enable(False)

    def enable_cpu(self):
        for component in self.components.values():
            component.enable(False)

    def install(self, name, progress=None, should_stop=None):
        return self.components[name].install(progress, should_stop)
