"""Policy / Guardrail Engine — the pre-action gate. In-process, synchronous,
fail-closed. No action (discovery or replay) reaches a Surface Adapter without a
prior Allow from here."""

from .allowlist import Allowlist, TenantPolicy
from .engine import PolicyDecision, PolicyEngine, PolicyVerdict

__all__ = ["PolicyEngine", "PolicyDecision", "PolicyVerdict", "Allowlist", "TenantPolicy"]
