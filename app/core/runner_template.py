"""Runner template generation."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import SERVICES_DIR, TEMPLATES_DIR


_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)


def generate_runner(service_name: str, service_dir: Path, python_path: str = "python3") -> Path:
    """Generate runner.py for a service."""
    template = _jinja_env.get_template("runner.py.j2")
    content = template.render(
        service_name=service_name,
        service_dir=str(service_dir),
        config_path=str(service_dir / "config.toml"),
        main_path=str(service_dir / "main.py"),
        log_path=str(service_dir / "runner.log"),
    )
    runner_path = service_dir / "runner.py"
    runner_path.write_text(content, encoding="utf-8")
    return runner_path


def generate_unit(
    service_name: str,
    display_name: str,
    service_dir: Path,
    python_path: str = "python3",
    auto_restart: bool = True,
) -> Path | None:
    """Generate systemd unit file for a service."""
    try:
        template = _jinja_env.get_template("service.unit.j2")
    except Exception:
        return None

    content = template.render(
        service_name=service_name,
        display_name=display_name,
        python_path=python_path,
        runner_path=str(service_dir / "runner.py"),
        working_dir=str(service_dir),
        log_path=str(service_dir / "runner.log"),
        auto_restart=auto_restart,
    )
    unit_path = service_dir / f"{service_name}.service"
    unit_path.write_text(content, encoding="utf-8")
    return unit_path
