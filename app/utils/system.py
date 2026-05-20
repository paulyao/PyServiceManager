"""System command execution utilities."""
import asyncio
import shutil
from dataclasses import dataclass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run_command(cmd: list[str], timeout: int = 30) -> CommandResult:
    """Execute a system command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        proc.kill()
        return CommandResult(returncode=-1, stdout="", stderr=f"Command timed out after {timeout}s")
    except FileNotFoundError:
        return CommandResult(returncode=-1, stdout="", stderr=f"Command not found: {cmd[0]}")


def has_systemctl() -> bool:
    """Check if systemctl is available on this system."""
    return shutil.which("systemctl") is not None


async def systemctl(command: str, service_name: str, timeout: int = 30) -> CommandResult:
    """Run a systemctl command for a service."""
    return await run_command(
        ["systemctl", command, f"{service_name}.service"],
        timeout=timeout,
    )


def get_service_pid_from_file(pid_path: str) -> int | None:
    """Read PID from a .pid file."""
    try:
        with open(pid_path, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None
