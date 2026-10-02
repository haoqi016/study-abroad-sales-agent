"""Read-only Sales V2 knowledge boundaries.

The Cases and Programs functions deliberately have no implicit access to the
Application System. A caller must provide an explicitly approved source.
"""

from .advantages import select_approved_advantages
from .cases import search_anonymized_cases
from .methodology import get_sales_methodologies
from .programs import get_program_evidence, program_freshness_actions
from .verify import verify_program_claims

__all__ = [
    "get_sales_methodologies",
    "search_anonymized_cases",
    "get_program_evidence",
    "select_approved_advantages",
    "verify_program_claims",
    "program_freshness_actions",
]
