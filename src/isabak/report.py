from dataclasses import dataclass, field
from subprocess import CalledProcessError
from time import monotonic
from src.isabak.logs import get_logger

logger = get_logger(__name__)


@dataclass
class Step:
    stage: str
    name: str
    ok: bool
    seconds: float
    note: str = ""


@dataclass
class RunReport:
    started: float = field(default_factory=monotonic)
    steps: list[Step] = field(default_factory=list)

    def add(self, stage: str, name: str, ok: bool, seconds: float, note: str = ""):
        self.steps.append(Step(stage, name, ok, seconds, note))

    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    def log_summary(self, app_name: str):
        # error level on failure so cron emails it, info on success so runs stay quiet
        log = logger.info if self.ok() else logger.error
        status = "succeeded" if self.ok() else "FAILED"
        log(f"{app_name} {status} in {format_seconds(monotonic() - self.started)}")
        for step in self.steps:
            result = "ok  " if step.ok else "FAIL"
            line = (
                f"  {result} {step.stage} {step.name} ({format_seconds(step.seconds)})"
            )
            if step.note:
                line += f" {step.note}"
            log(line)


def describe_error(e: Exception) -> str:
    # never the full message, CalledProcessError argv can hold credentials
    if isinstance(e, CalledProcessError):
        return f"{type(e).__name__}: exit status {e.returncode}"
    return type(e).__name__


def format_seconds(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"
