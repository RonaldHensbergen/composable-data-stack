# cli/diagnostics.py
from dataclasses import dataclass


@dataclass
class Diagnostic:
    level: str   # "error" | "warning"
    code: str
    message: str
    path: str

    def format(self) -> str:
        return f"[{self.code}] {self.path}\n{self.message}"
