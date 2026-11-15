"""Payment processors and their fee and settlement rules.

A processor takes card or wallet payments on the merchant's behalf and later
pays the money into the merchant's bank account. How much arrives, and when,
depends on the processor's terms, and those terms are the main reason the bank
and the ledger disagree. The generator uses these rules to plant
discrepancies, and the matching engine uses the same rules to explain them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from eod_close.domain.business_days import add_business_days
from eod_close.domain.money import Money


@dataclass(frozen=True, slots=True)
class FeeRule:
    """A processor's per-transaction fee: a percentage plus a fixed amount.

    Attributes:
        percentage: The rate as a fraction, for example ``Decimal("0.014")``
            for 1.4%.
        fixed: A flat amount added to every chargeable transaction.

    Raises:
        ValueError: If ``percentage`` is negative or ``fixed`` is negative.
    """

    percentage: Decimal
    fixed: Money

    def __post_init__(self) -> None:
        if self.percentage < 0 or self.fixed.is_negative():
            raise ValueError("Fees cannot be negative")

    def fee_for(self, gross: Money) -> Money:
        """Calculate the fee on one payment.

        Args:
            gross: The amount the customer paid.

        Returns:
            The fee, rounded half-up to the penny. Zero for refunds and
            other non-positive amounts, which the fictional processors do
            not charge on.

        Raises:
            CurrencyMismatchError: If ``gross`` and ``fixed`` differ in currency.
        """
        if not gross.is_positive():
            return Money.zero(gross.currency)
        return gross.multiply(self.percentage) + self.fixed

    def describe(self) -> str:
        """Describe the rule for people, for example ``"1.4% + £0.20"``."""
        percent = f"{(self.percentage * 100).normalize():f}%"
        return percent if self.fixed.is_zero() else f"{percent} + {self.fixed}"


NO_FEE: Final = FeeRule(Decimal(0), Money.zero())


@dataclass(frozen=True, slots=True)
class PaymentProcessor:
    """A fictional payment processor and its settlement terms.

    Attributes:
        code: Short code used in files, for example ``"BRAMBLE"``.
        name: Display name.
        fee_rule: The fee deducted at settlement when ``settles_net`` is
            True; otherwise the fee schedule invoiced separately.
        settlement_delay_days: Nominal business days from payment to
            settlement (the N in T+N).
        settles_net: True if the processor deducts fees before paying out,
            so the bank receives less than the ledger booked.
        batches_payouts: True if many payments arrive as one bank credit.
        chargeback_fee: The penalty debited alongside each chargeback.
        settlement_currency: The currency paid into the bank.
        sale_currency: The currency customers pay in, when it differs from
            the settlement currency.

    Raises:
        ValueError: If ``settlement_delay_days`` is negative.
    """

    code: str
    name: str
    fee_rule: FeeRule
    settlement_delay_days: int
    settles_net: bool
    batches_payouts: bool = False
    chargeback_fee: Money = Money.zero()
    settlement_currency: str = "GBP"
    sale_currency: str = "GBP"

    def __post_init__(self) -> None:
        if self.settlement_delay_days < 0:
            raise ValueError("Settlement delay cannot be negative")

    def fee_for(self, gross: Money) -> Money:
        """Return the fee this processor charges on ``gross``."""
        return self.fee_rule.fee_for(gross)

    def settlement_amount(self, gross: Money) -> Money:
        """Return what the bank should receive for a payment of ``gross``.

        Args:
            gross: The amount booked in the ledger.

        Returns:
            ``gross`` less the fee for net-settling processors, otherwise
            ``gross`` unchanged.
        """
        return gross - self.fee_for(gross) if self.settles_net else gross

    def expected_settlement_date(self, transaction_date: date) -> date:
        """Return the business day the processor nominally pays out.

        Args:
            transaction_date: The date the payment was taken.

        Returns:
            ``transaction_date`` plus ``settlement_delay_days`` business days.
        """
        return add_business_days(transaction_date, self.settlement_delay_days)


# The processors in every generated case. All names are invented.
DIRECT: Final = PaymentProcessor(
    code="DIRECT",
    name="Direct bank transfer",
    fee_rule=NO_FEE,
    settlement_delay_days=0,
    settles_net=False,
)
OTTERLINE: Final = PaymentProcessor(
    code="OTTERLINE",
    name="Otterline Payments",
    # Otterline pays out gross and invoices its fees monthly, so its fees
    # never appear in the daily reconciliation.
    fee_rule=NO_FEE,
    settlement_delay_days=2,
    settles_net=False,
)
BRAMBLE: Final = PaymentProcessor(
    code="BRAMBLE",
    name="Bramblecard Merchant Services",
    fee_rule=FeeRule(Decimal("0.014"), Money.of("0.20")),
    settlement_delay_days=1,
    settles_net=True,
    chargeback_fee=Money.of("15.00"),
)
QUILLFEATHER: Final = PaymentProcessor(
    code="QUILLFEATHER",
    name="Quillfeather Wallet",
    fee_rule=FeeRule(Decimal("0.019"), Money.zero()),
    settlement_delay_days=1,
    settles_net=True,
    batches_payouts=True,
)
FERNHOLLOW: Final = PaymentProcessor(
    code="FERNHOLLOW",
    name="Fernhollow Cross-Border",
    fee_rule=NO_FEE,
    settlement_delay_days=1,
    settles_net=False,
    sale_currency="EUR",
)

FICTIONAL_PROCESSORS: Final[tuple[PaymentProcessor, ...]] = (
    DIRECT,
    OTTERLINE,
    BRAMBLE,
    QUILLFEATHER,
    FERNHOLLOW,
)
