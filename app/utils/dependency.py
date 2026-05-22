"""Dependency checking and installation utilities.

Provides PEP 508 requirement checking, version constraint validation,
and package installation via uv (preferred) or pip (fallback).
"""
import asyncio
import importlib
import importlib.metadata
import re
import shutil
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as get_version

from packaging.requirements import Requirement

from app.utils.system import run_command

# Lock to prevent concurrent install processes
_install_lock = asyncio.Lock()

# Install timeout in seconds (5 minutes for large packages)
_INSTALL_TIMEOUT = 300


def _get_install_command() -> list[str]:
    """Get the package install command prefix.

    Uses 'uv pip install --python <path>' when uv is available,
    ensuring packages are installed into the current venv.
    Falls back to 'python -m pip install' when uv is not found.
    """
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--python", sys.executable]
    return [sys.executable, "-m", "pip", "install"]


def normalize_package_name(name: str) -> str:
    """Normalize package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _invalidate_metadata_cache() -> None:
    """Invalidate importlib.metadata caches after installing packages.

    importlib.metadata may cache distribution info; calling this ensures
    newly installed packages are discoverable by subsequent checks.
    """
    # Clear the per-distribution cache in importlib.metadata
    # Works on Python 3.11+ where _cached_name is used internally
    try:
        importlib.invalidate_caches()
        # Also clear the fast_path-based cache if present
        if hasattr(importlib.metadata, "_cache"):
            importlib.metadata._cache.clear()
    except Exception:
        pass


@dataclass
class PkgStatus:
    """Status of a single package requirement."""
    name: str
    specifier: str
    installed: bool
    installed_version: str | None
    satisfied: bool
    error: str | None = None


@dataclass
class CheckResult:
    """Result of checking a list of requirements."""
    requirements: list[PkgStatus]
    all_satisfied: bool
    missing: list[str] = field(default_factory=list)
    unsatisfied: list[str] = field(default_factory=list)


@dataclass
class InstallResult:
    """Result of installing requirements."""
    success: bool
    installed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    output: str = ""
    errors: dict[str, str] = field(default_factory=dict)


def validate_requirement_spec(spec: str) -> tuple[bool, str | None]:
    """Validate a single requirement specifier string.

    Returns (is_valid, error_message).
    """
    if not spec or not spec.strip():
        return False, "依赖声明不能为空"
    try:
        req = Requirement(spec.strip())
        if not req.name:
            return False, "包名不能为空"
        return True, None
    except Exception as e:
        return False, str(e)


def check_requirements(requirements: list[str]) -> CheckResult:
    """Check if all requirements are installed and satisfy version constraints."""
    if not requirements:
        return CheckResult(requirements=[], all_satisfied=True)

    statuses: list[PkgStatus] = []
    missing: list[str] = []
    unsatisfied: list[str] = []

    for spec in requirements:
        spec = spec.strip()
        if not spec:
            continue

        try:
            req = Requirement(spec)
        except Exception:
            statuses.append(PkgStatus(
                name=spec, specifier="", installed=False,
                installed_version=None, satisfied=False,
                error=f"无效的依赖声明: {spec}",
            ))
            missing.append(spec)
            continue

        pkg_name = normalize_package_name(req.name)
        specifier = str(req.specifier) if req.specifier else ""

        try:
            installed_ver = get_version(req.name)
            installed = True
        except PackageNotFoundError:
            installed = False
            installed_ver = None

        if not installed:
            satisfied = False
            missing.append(pkg_name)
        elif req.specifier:
            satisfied = req.specifier.contains(installed_ver, prereleases=True)
            if not satisfied:
                unsatisfied.append(pkg_name)
        else:
            satisfied = True

        statuses.append(PkgStatus(
            name=pkg_name, specifier=specifier,
            installed=installed, installed_version=installed_ver,
            satisfied=satisfied,
        ))

    all_satisfied = len(missing) == 0 and len(unsatisfied) == 0
    return CheckResult(
        requirements=statuses, all_satisfied=all_satisfied,
        missing=missing, unsatisfied=unsatisfied,
    )


def _extract_error_message(result) -> str:
    """Extract a human-readable error message from a command result."""
    if result.returncode == -1:
        # Timeout or command not found
        if "timed out" in result.stderr.lower():
            return f"安装超时({_INSTALL_TIMEOUT}s)"
        if "not found" in result.stderr.lower():
            return "安装命令未找到"
        return result.stderr

    # Parse pip/uv error output for common failure patterns
    stderr = result.stderr
    for pattern in [
        r"Because (\S+) was not found in the package registry",
        r"No matching distribution found for (\S+)",
        r"ERROR: Could not find a version that satisfies the requirement (\S+)",
        r"error: No solution found for (\S+)",
    ]:
        import re as _re
        m = _re.search(pattern, stderr)
        if m:
            return f"找不到匹配的版本: {m.group(1)}"

    # Compilation error
    if "failed with exit status" in stderr.lower():
        # Try to extract the package name from the build output
        for line in stderr.split("\n"):
            if "building wheel for" in line.lower():
                return f"编译失败: {line.strip()}"

    # Generic error — return last few lines of stderr
    err_lines = [l for l in stderr.split("\n") if l.strip()]
    if err_lines:
        return err_lines[-1][:200]

    return "安装失败"


async def install_requirements(requirements: list[str]) -> InstallResult:
    """Install unsatisfied requirements.

    Uses uv pip install (preferred) or pip install (fallback).
    Installs packages one by one so that a single failure does not
    prevent other packages from being installed.
    Uses asyncio Lock to prevent concurrent install processes.
    """
    if not requirements:
        return InstallResult(success=True)

    # Find unsatisfied requirements
    check = check_requirements(requirements)
    if check.all_satisfied:
        return InstallResult(success=True, output="所有依赖已满足，无需安装")

    # Collect original spec strings for unsatisfied packages
    unsatisfied_names = set(check.missing + check.unsatisfied)
    to_install: list[str] = []
    for spec in requirements:
        spec = spec.strip()
        if not spec:
            continue
        try:
            req = Requirement(spec)
            if normalize_package_name(req.name) in unsatisfied_names:
                to_install.append(spec)
        except Exception:
            to_install.append(spec)

    if not to_install:
        return InstallResult(success=True, output="所有依赖已满足，无需安装")

    installed_pkgs: list[str] = []
    failed_pkgs: list[str] = []
    errors: dict[str, str] = {}
    all_output: list[str] = []

    async with _install_lock:
        install_cmd = _get_install_command()
        for spec in to_install:
            cmd = [*install_cmd, spec]
            result = await run_command(cmd, timeout=_INSTALL_TIMEOUT)
            all_output.append(f"--- {' '.join(install_cmd)} {spec} ---")
            all_output.append(result.stdout)
            if result.stderr:
                all_output.append(result.stderr)

            # Invalidate metadata cache so newly installed packages are visible
            _invalidate_metadata_cache()

            # Determine install result:
            # 1. If command succeeded, verify with check_requirements()
            # 2. If command failed, extract error message
            if result.ok:
                post_check = check_requirements([spec])
                for s in post_check.requirements:
                    if s.satisfied:
                        installed_pkgs.append(s.name)
                    else:
                        # Command returned 0 but package still not satisfied
                        failed_pkgs.append(s.name)
                        errors[s.name] = "安装命令成功但包仍不可用"
            else:
                # Extract package name for error reporting
                try:
                    pkg_name = normalize_package_name(Requirement(spec).name)
                except Exception:
                    pkg_name = spec
                failed_pkgs.append(pkg_name)
                errors[pkg_name] = _extract_error_message(result)

    return InstallResult(
        success=len(failed_pkgs) == 0,
        installed=installed_pkgs,
        failed=failed_pkgs,
        output="\n".join(all_output),
        errors=errors,
    )
