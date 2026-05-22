"""Input validation utilities."""
import ast
import re

SERVICE_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
RESERVED_PREFIXES = ("systemd-", "dbus-", "sysinit-", "basic")


def validate_service_name(name: str) -> str:
    """Validate a service/module name for safety and systemd compatibility."""
    if not SERVICE_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid name '{name}': must start with a letter, contain only "
            "alphanumeric, underscore, hyphen, and be 1-64 chars"
        )
    for prefix in RESERVED_PREFIXES:
        if name.startswith(prefix):
            raise ValueError(f"Name '{name}' uses reserved prefix '{prefix}'")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Name '{name}' contains path traversal characters")
    return name


def validate_python_code(code: str) -> list[str]:
    """Validate Python code syntax via ast.parse. Returns list of errors."""
    errors = []
    if len(code.encode("utf-8")) > 512 * 1024:
        errors.append("Code size exceeds 512KB limit")
        return errors
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
    return errors


def validate_toml_content(content: str) -> list[str]:
    """Validate TOML content. Returns list of errors."""
    errors = []
    if len(content.encode("utf-8")) > 1024 * 1024:
        errors.append("Config size exceeds 1MB limit")
        return errors
    try:
        import tomllib
        tomllib.loads(content)
    except Exception as e:
        errors.append(f"TOML parse error: {e}")
    return errors


def validate_module_code(code: str) -> tuple[bool, list[str], dict | None]:
    """Validate module code: syntax + Module class presence + extract info.
    Returns (valid, errors, module_info)."""
    errors = validate_python_code(code)
    if errors:
        return False, errors, None

    module_info = {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False, ["Failed to parse module code"], None

    # Find Module class
    module_class = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Module":
            module_class = node
            break

    if module_class is None:
        return False, ["Module class not found in code"], None

    # Extract class attributes
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


def validate_requirements(requirements: list[str]) -> list[str]:
    """Validate a list of requirement specifier strings. Returns list of errors."""
    from app.utils.dependency import validate_requirement_spec

    errors = []
    for i, spec in enumerate(requirements):
        is_valid, err_msg = validate_requirement_spec(spec)
        if not is_valid:
            errors.append(f"依赖 #{i+1} '{spec}': {err_msg}")
    return errors
