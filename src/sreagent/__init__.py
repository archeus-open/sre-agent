"""sreagent: prototype agentic AI system for SRE incident response."""

from .agent import IncidentReport, SREAgent
from .context import ContextManager
from .hosts import CommandPolicy, HostInventory, SimulatedBackend
from .inference import InferenceClient
from .rag import RAGPipeline
from .runbooks import RunbookLoader
from .sandbox import SandboxManager
from .tickets import Ticket, TicketStore

__all__ = [
    "SREAgent", "IncidentReport",
    "ContextManager", "InferenceClient", "RAGPipeline",
    "Ticket", "TicketStore",
    "RunbookLoader",
    "HostInventory", "SimulatedBackend", "CommandPolicy",
    "SandboxManager",
]
__version__ = "0.1.0"
