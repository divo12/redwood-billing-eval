# Source: R1/sources/google_drive/shared_drives/finance/billing-adjustments-approval-matrix.json (duplicate-charge credit row, revised 2027-02-26).
DUPLICATE_CREDIT_LIMIT_CENTS = 25_000  # invoices for March 2027 usage onward
EARLIER_DUPLICATE_CREDIT_LIMIT_CENTS = 50_000  # invoices for February 2027 usage or earlier
LIMIT_CHANGED_ON = "2027-03-01"


def duplicate_credit_limit_cents(period_start: str) -> int:
    """The per-line limit for an invoice, decided by the usage period it bills."""
    return DUPLICATE_CREDIT_LIMIT_CENTS if period_start >= LIMIT_CHANGED_ON else EARLIER_DUPLICATE_CREDIT_LIMIT_CENTS
