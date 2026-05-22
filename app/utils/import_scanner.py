"""Source code import scanning utilities.

Analyzes Python source files using AST to discover third-party imports,
compare them against declared requirements, and identify missing declarations.
"""
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.dependency import normalize_package_name

# ── Common import-name → pip-package mappings ──────────────────

IMPORT_TO_PIP: dict[str, str] = {
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "gi": "PyGObject",
    "sklearn": "scikit-learn",
    "attr": "attrs",
    "serial": "pyserial",
    "usb": "pyusb",
    "Crypto": "pycryptodome",
    "jose": "python-jose",
    "dotenv": "python-dotenv",
    "git": "GitPython",
    "bs4": "beautifulsoup4",
    "flask_cors": "Flask-Cors",
    "flask_login": "Flask-Login",
    "flask_sqlalchemy": "Flask-SQLAlchemy",
    "sqlalchemy": "SQLAlchemy",
    "jwt": "PyJWT",
}

# ── Standard library detection ─────────────────────────────────

_STDLIB_NAMES: set[str] | None = None


def _get_stdlib_names() -> set[str]:
    global _STDLIB_NAMES
    if _STDLIB_NAMES is not None:
        return _STDLIB_NAMES
    if sys.version_info >= (3, 10):
        _STDLIB_NAMES = set(sys.stdlib_module_names)
    else:
        # Fallback for Python < 3.10
        _STDLIB_NAMES = {
            "abc", "argparse", "ast", "asyncio", "base64", "bisect",
            "calendar", "collections", "configparser", "contextlib", "copy",
            "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib",
            "email", "enum", "faulthandler", "fileinput", "fnmatch",
            "fractions", "functools", "gc", "getpass", "glob", "gzip",
            "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect",
            "io", "itertools", "json", "keyword", "linecache", "locale",
            "logging", "lzma", "mailbox", "marshal", "math", "mimetypes",
            "multiprocessing", "numbers", "operator", "os", "pathlib",
            "pickle", "platform", "pprint", "profile", "pstats", "queue",
            "re", "readline", "reprlib", "resource", "secrets", "select",
            "shelve", "shlex", "shutil", "signal", "site", "smtplib",
            "socket", "sqlite3", "ssl", "stat", "statistics", "string",
            "struct", "subprocess", "sys", "syslog", "tabnanny",
            "tarfile", "tempfile", "test", "textwrap", "threading", "time",
            "timeit", "token", "trace", "traceback", "tracemalloc",
            "typing", "unicodedata", "unittest", "urllib", "uuid", "venv",
            "warnings", "weakref", "xml", "zipfile", "zlib",
        }
    return _STDLIB_NAMES


def is_stdlib(module_name: str) -> bool:
    """Check if a module name is part of the Python standard library."""
    return module_name in _get_stdlib_names()


def is_local_module(module_name: str, service_dir: Path) -> bool:
    """Check if a module name refers to a local file in the service directory."""
    if module_name == "runner":
        return True
    return (service_dir / f"{module_name}.py").exists() or \
           (service_dir / module_name / "__init__.py").exists()


def import_name_to_pip_name(module_name: str) -> str:
    """Map a Python import name to its pip package name."""
    return IMPORT_TO_PIP.get(module_name, module_name)


# ── Data structures ────────────────────────────────────────────

@dataclass
class ImportInfo:
    """Information about a single import statement."""
    module_name: str        # Top-level module name (e.g. "pymysql")
    full_path: str          # Full import path (e.g. "pymysql.cursors.DictCursor")
    import_type: str        # "stdlib" / "third_party" / "local"
    line_number: int
    pip_name: str | None    # Mapped pip package name (only for third_party)


@dataclass
class SourceScanResult:
    """Result of scanning a single source file."""
    file_path: str
    imports: list[ImportInfo]
    third_party_packages: list[str]   # Deduplicated pip package names
    error: str | None = None


@dataclass
class ScanComparison:
    """Comparison between scanned imports and declared requirements."""
    matched: list[str]       # Both declared and found in code
    scanned_only: list[str]  # Found in code but not declared
    declared_only: list[str] # Declared but not found in code


# ── Core scanning functions ────────────────────────────────────

def extract_imports(source_code: str) -> list[ImportInfo]:
    """Extract all import statements from Python source code using AST."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    imports: list[ImportInfo] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                imports.append(ImportInfo(
                    module_name=top_level,
                    full_path=alias.name,
                    import_type="",  # Will be classified later
                    line_number=node.lineno,
                    pip_name=None,
                ))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                # Relative import (from . import x) — skip
                continue
            top_level = node.module.split(".")[0]
            imports.append(ImportInfo(
                module_name=top_level,
                full_path=node.module,
                import_type="",  # Will be classified later
                line_number=node.lineno,
                pip_name=None,
            ))

    return imports


def scan_source_file(file_path: Path, service_dir: Path | None = None) -> SourceScanResult:
    """Scan a single Python source file for import statements.

    Classifies each import as stdlib, third_party, or local.
    For third_party imports, maps the import name to pip package name.
    """
    if not file_path.exists():
        return SourceScanResult(
            file_path=str(file_path),
            imports=[],
            third_party_packages=[],
            error=f"文件不存在: {file_path}",
        )

    try:
        source_code = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return SourceScanResult(
            file_path=str(file_path),
            imports=[],
            third_party_packages=[],
            error=f"读取文件失败: {e}",
        )

    raw_imports = extract_imports(source_code)
    if not raw_imports:
        # Could be a SyntaxError or empty file
        return SourceScanResult(
            file_path=str(file_path),
            imports=[],
            third_party_packages=[],
            error=None if source_code.strip() else "文件为空",
        )

    # Classify each import
    seen_third_party: set[str] = set()
    third_party_packages: list[str] = []

    for imp in raw_imports:
        if service_dir and is_local_module(imp.module_name, service_dir):
            imp.import_type = "local"
        elif is_stdlib(imp.module_name):
            imp.import_type = "stdlib"
        else:
            imp.import_type = "third_party"
            imp.pip_name = import_name_to_pip_name(imp.module_name)
            normalized = normalize_package_name(imp.pip_name)
            if normalized not in seen_third_party:
                seen_third_party.add(normalized)
                third_party_packages.append(imp.pip_name)

    return SourceScanResult(
        file_path=str(file_path),
        imports=raw_imports,
        third_party_packages=third_party_packages,
    )


def build_scan_comparison(
    declared_requirements: list[str],
    scanned_packages: list[str],
) -> ScanComparison:
    """Compare declared requirements with scanned third-party packages.

    Uses normalize_package_name() for robust name comparison.
    """
    declared_names: set[str] = set()
    for spec in declared_requirements:
        spec = spec.strip()
        if not spec:
            continue
        # Extract package name from PEP 508 specifier (e.g. "pymysql>=1.1" → "pymysql")
        try:
            from packaging.requirements import Requirement
            req = Requirement(spec)
            declared_names.add(normalize_package_name(req.name))
        except Exception:
            declared_names.add(normalize_package_name(spec))

    scanned_names: set[str] = set()
    for pkg in scanned_packages:
        scanned_names.add(normalize_package_name(pkg))

    matched = sorted(declared_names & scanned_names)
    scanned_only = sorted(scanned_names - declared_names)
    declared_only = sorted(declared_names - scanned_names)

    return ScanComparison(
        matched=matched,
        scanned_only=scanned_only,
        declared_only=declared_only,
    )
