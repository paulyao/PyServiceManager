"""Module dynamic loading, validation, and utility extraction."""
from pathlib import Path


def get_module_template() -> str:
    """Return default module code template."""
    return '''"""Module: my-module"""
from pathlib import Path


class Module:
    """Module metadata"""
    name = "my-module"
    version = "1.0.0"
    description = "Module description"

    # Optional: module config defaults
    # config_schema = {"key": "default_value"}

    # Lifecycle hooks (optional, called by runner automatically)
    def on_start(self, ctx):
        """Called when service starts."""
        ctx.logger.info(f"Module {self.name} starting")

    def on_stop(self, ctx):
        """Called when service stops."""
        ctx.logger.info(f"Module {self.name} stopping")

    def on_config_reload(self, ctx):
        """Called when config changes."""
        pass

    # Utility methods (accessible via modules["my-module"] in user code)
    def my_utility(self, *args, **kwargs):
        """A utility method for user code."""
        pass
'''
