"""Shared S1 contracts for the content factory."""

from .gold_set import GoldSetReadiness, read_gold_set_readiness
from .validation import (
    ContractValidationError,
    compute_product_completeness,
    product_script_eligibility,
    validate_document,
    validate_or_raise,
)

__all__ = [
    "ContractValidationError",
    "compute_product_completeness",
    "GoldSetReadiness",
    "product_script_eligibility",
    "read_gold_set_readiness",
    "validate_document",
    "validate_or_raise",
]

__version__ = "0.1.0"
