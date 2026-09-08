"""Human-Loop domain: stuck detection, intervention requests, and the
control-transfer / session-handoff mechanism. The only domain permitted to change
*who* holds the input lock on a live session."""

from .operator_console import OperatorConsole
from .service import EscalationService, ResumeOutcome
from .session_broker import ControlLock, Lease, SessionBroker

__all__ = [
    "SessionBroker", "Lease", "ControlLock",
    "EscalationService", "ResumeOutcome", "OperatorConsole",
]
