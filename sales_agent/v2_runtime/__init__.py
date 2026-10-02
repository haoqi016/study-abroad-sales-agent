"""Local, deterministic foundation for the frozen Sales V2 runtime."""

from .store import ContractViolation, V2RuntimeStore

__all__ = ["ContractViolation", "V2RuntimeStore"]
