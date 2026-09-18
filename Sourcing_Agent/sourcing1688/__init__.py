"""A personalised 1688 apparel sourcing agent, exposed to any AI over MCP.

The package is deliberately split so that the *judgement* (what to search for,
what disqualifies a supplier, what "best" means for this buyer, what an order
actually costs delivered) lives in deterministic, testable Python, while the
*language* (understanding a vague brief, reading the results back, deciding what
to ask next) stays with whichever model connects to it.

That division is the point. An LLM asked to eyeball twenty listings will quietly
forget the MOQ ceiling on the nineteenth; a filter will not. Equally, no rule
engine will ever parse "something like what I bought last spring but warmer".
"""

from .models import Offer, ScoredOffer, SearchPlan, Sku, Supplier
from .preferences import default_profile, load_profile, save_profile, update_profile

__version__ = "1.0.0"

__all__ = [
    "Offer",
    "ScoredOffer",
    "SearchPlan",
    "Sku",
    "Supplier",
    "default_profile",
    "load_profile",
    "save_profile",
    "update_profile",
    "__version__",
]
