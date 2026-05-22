"""Dependency checking and installation utilities."""
import asyncio
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as get_version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from app.utils.system import run_command

# Lock to prevent concurrent pip install processes
_install_lock = asyncio.Lock()


def _get_install_command() -> list[str]:
    """Get the package install command prefix.

    Uses 'uv pip install' when uv is available, falls back to 'pip install'.
    """
    if shutil.which("uv"):
        return ["uv", "pip", "install"]
    return [sys.executable, "-m", "pip", "install"]


def normalize_package_name(name: str) -> str:
    """Normalize package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class PkgStatus:
    """Status of a single package requirement."""
    name: str
    specifier: str
    installed: bool
    installed_version: str | None
    satisfied: bool


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


async def install_requirements(requirements: list[str]) -> InstallResult:
    """Install unsatisfied requirements via pip.

    Installs packages one by one so that a single failure does not
    prevent other packages from being installed.
    Uses asyncio Lock to prevent concurrent pip processes.
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
    all_output: list[str] = []

    async with _install_lock:
        install_cmd = _get_install_command()
        for spec in to_install:
            cmd = [*install_cmd, spec]
            result = await run_command(cmd, timeout=120)
            all_output.append(f"--- {' '.join(install_cmd)} {spec} ---")
            all_output.append(result.stdout)
            all_output.append(result.stderr)

            # Check if this specific package was installed
            post_check = check_requirements([spec])
            for s in post_check.requirements:
                if s.satisfied:
                    installed_pkgs.append(s.name)
                else:
                    failed_pkgs.append(s.name)

    return InstallResult(
        success=len(failed_pkgs) == 0,
        installed=installed_pkgs,
        failed=failed_pkgs,
        output="\n".join(all_output),
    )
