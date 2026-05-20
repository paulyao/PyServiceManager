"""Module dynamic loading, validation, and utility extraction."""
import ast
from pathlib import Path


def validate_module_code(code: str) -> tuple[bool, list[str], dict | None]:
    """Validate module code: syntax + Module class presence + extract info.
    Returns (valid, errors, module_info)."""
    errors = []

    if len(code.encode("utf-8")) > 512 * 1024:
        return False, ["Code size exceeds 512KB limit"], None

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"Syntax error at line {e.lineno}: {e.msg}"], None

    # Find Module class
    module_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Module":
            module_class = node
            break

    if module_class is None:
        return False, ["Module class not found in code"], None

    # Extract class attributes
    module_info = {}
    for item in module_class.body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            attr_name = item.targets[0].id if isinstance(item.targets[0], ast.Name) else None
            if attr_name and isinstance(item.value, ast.Constant):
                module_info[attr_name] = item.value.value

    # Extract methods
    hooks = []
    public_methods = []
    for item in module_class.body:
        if isinstance(item, ast.FunctionDef):
            if item.name.startswith("on_"):
                hooks.append(item.name)
            elif not item.name.startswith("_"):
                public_methods.append(item.name)

    module_info["has_on_start"] = "on_start" in hooks
    module_info["has_on_stop"] = "on_stop" in hooks
    module_info["has_on_config_reload"] = "on_config_reload" in hooks
    module_info["has_on_error"] = "on_error" in hooks
    module_info["public_methods"] = public_methods

    return True, [], module_info


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
