"""Seeded generator for fictional end-of-day reconciliation cases.

Each case is one business day for Copperkettle Homewares Ltd, a fictional
online retailer that banks with the fictional Thistledown Bank. The generator
builds a bank statement and an internal ledger that ought to agree, plants a
known mix of discrepancies and records each one in an answer key. The matching
engine is scored against that key, and the game shows its explanations when
the player closes the day.

Generation is deterministic: the same seed and difficulty always produce the
same case, so any day can be replayed exactly when reporting a bug or
comparing scores.

Dating convention: the bank statement covers the business day being closed,
so every bank entry is dated that day. Ledger entries are dated when they were
booked, which is earlier whenever the money took time to arrive.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final

from eod_close.domain.business_days import add_business_days
from eod_close.domain.entries import BankEntry, EntryKind, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.processor import FICTIONAL_PROCESSORS, PaymentProcessor
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.generator.answer_key import AnswerKey, DiscrepancyKind, PlantedItem
from eod_close.generator.difficulty import Difficulty, DifficultyProfile, profile_for

COMPANY_NAME: Final = "Copperkettle Homewares Ltd"
BANK_NAME: Final = "Thistledown Bank plc"
ACCOUNT_NAME: Final = "Copperkettle Homewares Ltd - Collections"

# Cases fall on a business day in a fixed range, so dates look plausible and
# include Mondays, where T+1 settlements reach back over a weekend.
_FIRST_BUSINESS_DATE: Final = date(2026, 1, 5)
_BUSINESS_DAYS_IN_RANGE: Final = 250

# The largest FX rounding difference the generator plants, in pence. The
# engine's currency tolerance (``EngineConfig.fx_tolerance``) must be at least this, and boundary
# tests check both sides of it.
MAX_FX_ROUNDING_PENCE: Final = 4

# Common retail prices. Real shops price at these points, so unrelated
# payments often share an amount and the amount alone proves little.
_PRICE_POINTS: Final = ("9.99", "14.99", "19.99", "24.99", "29.99", "39.99", "49.99")

_REQUIRED_PROCESSORS: Final = frozenset(p.code for p in FICTIONAL_PROCESSORS)

# A fixed mix is meant to be read at a glance, so it adds none of the
# ambiguity the harder levels do, and a batch payout covers just two sales.
_FIXED_MIX_PROFILE: Final = DifficultyProfile(
    None,
    item_count=1,
    weights=((DiscrepancyKind.CLEAN, 1),),
    max_batch_size=2,
    max_batches=1,
)


@dataclass(frozen=True)
class Case:
    """One generated business day.

    Attributes:
        case_id: Identifier encoding the difficulty and seed.
        seed: The seed used.
        difficulty: The difficulty level used.
        business_date: The day being closed.
        bank_statement: The bank side.
        ledger: The ledger side.
        answer_key: What was planted, for scoring.
    """

    case_id: str
    seed: int
    difficulty: Difficulty
    business_date: date
    bank_statement: BankStatement
    ledger: Ledger
    answer_key: AnswerKey


class CaseGenerator:
    """Builds reproducible cases from a seed and a difficulty level.

    Args:
        processors: The processors available to cases. Must include every
            processor in ``FICTIONAL_PROCESSORS``, because each kind of
            discrepancy is planted through a specific processor.

    Raises:
        ValueError: If a required processor is missing.
    """

    def __init__(self, processors: Sequence[PaymentProcessor] = FICTIONAL_PROCESSORS) -> None:
        self._processors = {p.code: p for p in processors}
        missing = _REQUIRED_PROCESSORS - self._processors.keys()
        if missing:
            raise ValueError(f"Missing processors: {sorted(missing)}")

    def generate(self, seed: int, difficulty: Difficulty) -> Case:
        """Generate one case.

        Args:
            seed: Any integer. The same seed and difficulty give the same case.
            difficulty: Controls the volume and mix of discrepancies.

        Returns:
            The generated case, with its answer key.
        """
        # A string seed keeps the two inputs independent, so seed 7 at Intern
        # and seed 7 at Head of Ops are unrelated days. Python hashes string
        # seeds with SHA-512, which is stable across runs and platforms.
        rng = random.Random(f"{seed}:{difficulty.value}")
        business_date = add_business_days(
            _FIRST_BUSINESS_DATE, rng.randrange(_BUSINESS_DAYS_IN_RANGE)
        )
        case_id = f"EOD-{difficulty.value}-{seed}"
        builder = _CaseBuilder(rng, business_date, self._processors, profile_for(difficulty))
        for kind in builder.choose_kinds():
            builder.plant(kind)
        bank_statement, ledger, answer_key = builder.build(case_id)
        return Case(
            case_id=case_id,
            seed=seed,
            difficulty=difficulty,
            business_date=business_date,
            bank_statement=bank_statement,
            ledger=ledger,
            answer_key=answer_key,
        )

    def generate_mix(
        self, seed: int, kinds: Sequence[DiscrepancyKind], name: str
    ) -> tuple[BankStatement, Ledger, AnswerKey]:
        """Generate a small day containing exactly the given items.

        The levels choose their items at random, so a small day at any of
        them might contain no problems at all. A fixed mix guarantees the
        shape, for example the home page's "another day", which always has
        one of each kind of problem.

        Args:
            seed: Any integer. The same seed, kinds and name give the same day.
            kinds: One entry per planted item; repeat a kind to plant it again.
            name: Names the mix in the case ID, and keeps its days independent
                of other mixes' days with the same seed.

        Returns:
            The bank statement, the ledger and the answer key.
        """
        rng = random.Random(f"{seed}:{name}")
        business_date = add_business_days(
            _FIRST_BUSINESS_DATE, rng.randrange(_BUSINESS_DAYS_IN_RANGE)
        )
        builder = _CaseBuilder(rng, business_date, self._processors, _FIXED_MIX_PROFILE)
        for kind in kinds:
            builder.plant(kind)
        return builder.build(f"EOD-{name}-{seed}")


def transpose_adjacent_digits(units: int, position: int) -> int:
    """Swap two adjacent digits of a positive whole number.

    Args:
        units: The number, for example an amount in pence.
        position: Index of the left-hand digit of the pair, counting from the
            most significant digit at 0.

    Returns:
        The number with the two digits swapped.

    Raises:
        ValueError: If ``units`` is not positive or ``position`` is out of range.
    """
    digits = list(str(units))
    if units <= 0 or not 0 <= position < len(digits) - 1:
        raise ValueError(f"Cannot transpose position {position} of {units}")
    digits[position], digits[position + 1] = digits[position + 1], digits[position]
    return int("".join(digits))


class _CaseBuilder:
    """Accumulates the entries and answer key for one case.

    Entries are created with temporary IDs and shuffled before final IDs are
    assigned, so neither the order of the files nor the IDs give away which
    entries belong together.
    """

    def __init__(
        self,
        rng: random.Random,
        business_date: date,
        processors: dict[str, PaymentProcessor],
        profile: DifficultyProfile,
    ) -> None:
        self._rng = rng
        self._date = business_date
        self._processors = processors
        self._profile = profile
        self._bank: list[BankEntry] = []
        self._ledger: list[LedgerEntry] = []
        self._items: list[PlantedItem] = []
        self._batch_count = 0
        # Sequences start at a random point and step unevenly so references
        # look like a real order system rather than 1, 2, 3.
        self._sequences = {
            "ORD": rng.randint(100_000, 400_000),
            "INV": rng.randint(20_000, 40_000),
            "RFD": rng.randint(5_000, 9_000),
            "CHB": rng.randint(500, 900),
        }
        self._planters: dict[DiscrepancyKind, Callable[[], None]] = {
            DiscrepancyKind.CLEAN: self._plant_clean,
            DiscrepancyKind.SETTLEMENT_DELAY: self._plant_settlement_delay,
            DiscrepancyKind.PROCESSOR_FEE: self._plant_processor_fee,
            DiscrepancyKind.FX_ROUNDING: self._plant_fx_rounding,
            DiscrepancyKind.BATCH_PAYOUT: self._plant_batch_payout,
            DiscrepancyKind.REFUND: self._plant_refund,
            DiscrepancyKind.CHARGEBACK: self._plant_chargeback,
            DiscrepancyKind.DUPLICATE: self._plant_duplicate,
            DiscrepancyKind.MISSING_FROM_BANK: self._plant_missing_from_bank,
            DiscrepancyKind.MISSING_FROM_LEDGER: self._plant_missing_from_ledger,
            DiscrepancyKind.TRANSPOSITION: self._plant_transposition,
        }

    def choose_kinds(self) -> list[DiscrepancyKind]:
        """Pick the kind of every item, guaranteeing each listed kind once."""
        profile = self._profile
        kinds = list(profile.kinds)
        weights = [weight for _, weight in profile.weights]
        batches = kinds.count(DiscrepancyKind.BATCH_PAYOUT)
        while len(kinds) < profile.item_count:
            kind = self._rng.choices(profile.kinds, weights)[0]
            if kind is DiscrepancyKind.BATCH_PAYOUT:
                # Batches are capped because each one widens the subset-sum
                # search the engine has to run; see the batch strategy.
                if batches >= profile.max_batches:
                    continue
                batches += 1
            kinds.append(kind)
        return kinds

    def plant(self, kind: DiscrepancyKind) -> None:
        """Add one item of the given kind to the case."""
        self._planters[kind]()

    def build(self, case_id: str) -> tuple[BankStatement, Ledger, AnswerKey]:
        """Shuffle, assign final IDs and return the finished files and key."""
        self._rng.shuffle(self._bank)
        self._rng.shuffle(self._ledger)
        id_map: dict[str, str] = {}
        bank = []
        for n, entry in enumerate(self._bank, start=1):
            id_map[entry.entry_id] = f"B{n:04d}"
            bank.append(replace(entry, entry_id=id_map[entry.entry_id]))
        ledger = []
        for n, ledger_entry in enumerate(self._ledger, start=1):
            id_map[ledger_entry.entry_id] = f"L{n:04d}"
            ledger.append(replace(ledger_entry, entry_id=id_map[ledger_entry.entry_id]))

        def remap(ids: tuple[str, ...]) -> tuple[str, ...]:
            """Translate working IDs to published IDs, sorted for a stable key."""
            return tuple(sorted(id_map[i] for i in ids))

        items = sorted(
            (
                replace(
                    item,
                    bank_ids=remap(item.bank_ids),
                    ledger_ids=remap(item.ledger_ids),
                    related_ids=remap(item.related_ids),
                )
                for item in self._items
            ),
            key=lambda item: item.bank_ids or item.ledger_ids,
        )
        statement = BankStatement(
            bank,
            bank_name=BANK_NAME,
            account_name=ACCOUNT_NAME,
            statement_date=self._date,
        )
        return statement, Ledger(ledger, entity_name=COMPANY_NAME), AnswerKey(case_id, tuple(items))

    # Building blocks

    def _reference(self, prefix: str) -> str:
        self._sequences[prefix] += self._rng.randint(1, 9)
        return f"{prefix}-{self._sequences[prefix]}"

    def _chance(self, rate: float) -> bool:
        # No random draw at a zero rate, so adding a noise setting leaves the
        # easier levels' cases exactly as they were.
        return rate > 0 and self._rng.random() < rate

    def _amount(self, *, small_only: bool = False) -> Money:
        if self._chance(self._profile.price_point_rate):
            return Money.of(self._rng.choice(_PRICE_POINTS))
        # Order values are skewed, like a real retailer's: many small baskets
        # and a few large trade orders.
        tiers = ((500, 15_000), (15_000, 100_000), (100_000, 999_999))
        low, high = tiers[0] if small_only else self._rng.choices(tiers, weights=(70, 25, 5))[0]
        return Money.from_minor_units(self._rng.randint(low, high))

    def _days_before(self, days: int) -> date:
        return add_business_days(self._date, -days)

    def _bank_entry(
        self, reference: str, amount: Money, processor: PaymentProcessor, description: str = ""
    ) -> BankEntry:
        entry = BankEntry(
            entry_id=f"~B{len(self._bank)}",
            reference=reference,
            amount=amount,
            value_date=self._date,
            counterparty=processor.code,
            description=description or processor.name.upper(),
        )
        self._bank.append(entry)
        return entry

    def _ledger_entry(
        self,
        reference: str,
        amount: Money,
        processor: PaymentProcessor,
        booked: date,
        *,
        kind: EntryKind = EntryKind.PAYMENT,
        original_amount: Money | None = None,
        original_reference: str | None = None,
        description: str = "",
    ) -> LedgerEntry:
        entry = LedgerEntry(
            entry_id=f"~L{len(self._ledger)}",
            reference=reference,
            amount=amount,
            value_date=booked,
            processor_code=processor.code,
            kind=kind,
            original_amount=original_amount,
            original_reference=original_reference,
            description=description or f"{kind.value.capitalize()} via {processor.name}",
        )
        self._ledger.append(entry)
        return entry

    def _record(
        self,
        kind: DiscrepancyKind,
        explanation: str,
        bank: Sequence[BankEntry] = (),
        ledger: Sequence[LedgerEntry] = (),
        related: Sequence[str] = (),
    ) -> None:
        self._items.append(
            PlantedItem(
                kind=kind,
                bank_ids=tuple(e.entry_id for e in bank),
                ledger_ids=tuple(e.entry_id for e in ledger),
                explanation=explanation,
                related_ids=tuple(related),
            )
        )

    def _clean_pair(self) -> tuple[BankEntry, LedgerEntry]:
        direct = self._processors["DIRECT"]
        reference = self._reference("INV")
        amount = self._amount()
        bank = self._bank_entry(reference, amount, direct, f"CUSTOMER TRANSFER {reference}")
        ledger = self._ledger_entry(reference, amount, direct, self._date)
        return bank, ledger

    # One planter per discrepancy kind

    def _plant_clean(self) -> None:
        """A trade customer pays an invoice by bank transfer the same day.

        Same reference, amount and date on both sides: the control group the
        engine must never get wrong.
        """
        bank, ledger = self._clean_pair()
        self._record(
            DiscrepancyKind.CLEAN,
            "Same reference, amount and date on both sides.",
            [bank],
            [ledger],
        )

    def _plant_settlement_delay(self) -> None:
        """A processor pays out days after the sale was booked.

        Processors hold funds while they settle with the card schemes, and
        payments taken after a daily cut-off, or on a Friday, slip further.
        The ledger books the sale on the day it happened; the bank sees it
        one to three business days later.

        At harder levels the bank line sometimes loses its reference. Bank
        narrative fields are short (Bacs allows 18 characters), and
        intermediaries truncate or drop references, so the amount, date and
        remitter are all that is left to match on.
        """
        otterline = self._processors["OTTERLINE"]
        delay = self._rng.randint(1, 3)
        booked = self._days_before(delay)
        reference = self._reference("ORD")
        amount = self._amount()
        blank = self._chance(self._profile.blank_reference_rate)
        bank = self._bank_entry("" if blank else reference, amount, otterline)
        ledger = self._ledger_entry(reference, amount, otterline, booked)
        self._record(
            DiscrepancyKind.SETTLEMENT_DELAY,
            f"{otterline.name} paid {delay} working day{'' if delay == 1 else 's'} late: "
            f"recorded on {booked:%d %b}, arrived on {self._date:%d %b}."
            + (" The bank line had no reference." if blank else ""),
            [bank],
            [ledger],
        )

    def _plant_processor_fee(self) -> None:
        """A processor deducts its fee before paying out.

        The ledger records what the customer paid (gross), but a net-settling
        processor keeps its fee, so the bank receives less. The difference is
        exactly the processor's published fee for that amount.
        """
        bramble = self._processors["BRAMBLE"]
        booked = self._days_before(bramble.settlement_delay_days)
        reference = self._reference("ORD")
        gross = self._amount()
        fee = bramble.fee_for(gross)
        net = bramble.settlement_amount(gross)
        bank = self._bank_entry(reference, net, bramble)
        ledger = self._ledger_entry(reference, gross, bramble, booked)
        self._record(
            DiscrepancyKind.PROCESSOR_FEE,
            f"{bramble.name} kept its {bramble.fee_rule.describe()} fee: "
            f"{gross} sale, {fee} fee, {net} arrived.",
            [bank],
            [ledger],
        )

    def _plant_fx_rounding(self) -> None:
        """A foreign-currency sale converts to a slightly different sterling amount.

        The ledger converts the customer's euros at the company's daily
        reference rate. The processor converts at its own rate and rounds
        at its own step, so the sterling it pays out differs by a few pence.
        """
        fernhollow = self._processors["FERNHOLLOW"]
        booked = self._days_before(fernhollow.settlement_delay_days)
        reference = self._reference("ORD")
        euros = self._amount().minor_units
        original = Money.from_minor_units(euros, fernhollow.sale_currency)
        rate = Decimal(self._rng.randint(8_300, 8_800)).scaleb(-4)
        booked_gbp = original.convert(rate, "GBP")
        pence = self._rng.choice([-1, 1]) * self._rng.randint(1, MAX_FX_ROUNDING_PENCE)
        received = booked_gbp + Money.from_minor_units(pence)
        bank = self._bank_entry(reference, received, fernhollow)
        ledger = self._ledger_entry(
            reference, booked_gbp, fernhollow, booked, original_amount=original
        )
        self._record(
            DiscrepancyKind.FX_ROUNDING,
            f"A {original} sale, recorded as {booked_gbp} at a rate of {rate}; "
            f"{fernhollow.name} used its own rate and paid {received}, "
            f"{received - booked_gbp} different.",
            [bank],
            [ledger],
        )

    def _plant_batch_payout(self) -> None:
        """A wallet provider pays many sales as one bank credit.

        Quillfeather pays each day's sales as one payout, net of fees, once
        its risk hold clears, which takes one to three business days. The
        bank shows a single payout while the ledger holds each sale
        separately, and nothing on the bank line says which sales it covers.
        """
        quill = self._processors["QUILLFEATHER"]
        self._batch_count += 1
        # One payout per booking day: the first batch covers yesterday's
        # sales, the second the day before, and so on. The profile caps
        # batches at three, so every batch falls inside the engine's window.
        booked = self._days_before(self._batch_count)
        size = self._rng.randint(2, max(2, self._profile.max_batch_size))
        ledger = [
            # Wallet payments are small baskets, which also keeps the
            # subset-sum search space modest.
            self._ledger_entry(self._reference("ORD"), self._amount(small_only=True), quill, booked)
            for _ in range(size)
        ]
        total = Money.total(quill.settlement_amount(e.amount) for e in ledger)
        reference = f"QFW-PAYOUT-{booked:%Y%m%d}-{self._batch_count:02d}"
        bank = self._bank_entry(reference, total, quill)
        self._record(
            DiscrepancyKind.BATCH_PAYOUT,
            f"{quill.name} bundled {size} sales into one payment of {total}, "
            f"after its {quill.fee_rule.describe()} fee.",
            [bank],
            ledger,
        )
        if self._chance(self._profile.held_back_rate):
            # A sale flagged by the processor's fraud checks is held back
            # from its day's payout, so the payout no longer equals the
            # whole day and the engine must find the right subset.
            held = self._ledger_entry(
                self._reference("ORD"), self._amount(small_only=True), quill, booked
            )
            self._record(
                DiscrepancyKind.MISSING_FROM_BANK,
                f"{quill.name} held {held.reference} back from the {booked:%d %b} payout "
                "for a risk review.",
                ledger=[held],
            )

    def _plant_refund(self) -> None:
        """A refund leaves the account against an earlier sale.

        Customer services books the refund under its own reference, but the
        processor reports it against the original order reference, so the two
        sides never share a reference and must be linked through the original.
        """
        otterline = self._processors["OTTERLINE"]
        original_reference = self._reference("ORD")
        amount = self._amount()
        booked = self._days_before(otterline.settlement_delay_days)
        bank = self._bank_entry(original_reference, -amount, otterline, "OTTERLINE REFUND")
        ledger = self._ledger_entry(
            self._reference("RFD"),
            -amount,
            otterline,
            booked,
            kind=EntryKind.REFUND,
            original_reference=original_reference,
        )
        self._record(
            DiscrepancyKind.REFUND,
            f"Refund {ledger.reference} of {amount} for order {original_reference}; "
            "the bank shows it under the order's reference.",
            [bank],
            [ledger],
        )

    def _plant_chargeback(self) -> None:
        """A card issuer forcibly reverses a sale and the processor adds a fee.

        When a cardholder disputes a payment, the issuer claws the money back
        and the processor debits a chargeback fee on top. The ledger books
        the reversal of the sale; the bank debit is larger by the fee.
        """
        bramble = self._processors["BRAMBLE"]
        original_reference = self._reference("ORD")
        amount = self._amount()
        debit = -(amount + bramble.chargeback_fee)
        bank = self._bank_entry(
            f"CB-{original_reference}", debit, bramble, "BRAMBLECARD CHARGEBACK"
        )
        ledger = self._ledger_entry(
            self._reference("CHB"),
            -amount,
            bramble,
            self._date,
            kind=EntryKind.CHARGEBACK,
            original_reference=original_reference,
        )
        self._record(
            DiscrepancyKind.CHARGEBACK,
            f"The customer disputed order {original_reference}: {amount} taken back "
            f"plus a {bramble.chargeback_fee} fee, {debit} in total.",
            [bank],
            [ledger],
        )

    def _plant_duplicate(self) -> None:
        """The same payment appears twice on one side.

        Ledger duplicates come from a payment posted twice by hand or by a
        retried integration. Bank duplicates are rarer, but happen when a
        file is reissued. The original pair still matches; the copy must be
        flagged rather than matched.
        """
        bank, ledger = self._clean_pair()
        self._record(
            DiscrepancyKind.CLEAN,
            "Same reference, amount and date on both sides.",
            [bank],
            [ledger],
        )
        if self._rng.random() < 0.75:
            copy = self._ledger_entry(
                ledger.reference, ledger.amount, self._processors["DIRECT"], ledger.value_date
            )
            self._record(
                DiscrepancyKind.DUPLICATE,
                f"{ledger.reference} was recorded twice in your records.",
                ledger=[copy],
                related=[ledger.entry_id],
            )
        else:
            bank_copy = self._bank_entry(
                bank.reference, bank.amount, self._processors["DIRECT"], bank.description
            )
            self._record(
                DiscrepancyKind.DUPLICATE,
                f"{bank.reference} appears twice on the bank statement.",
                bank=[bank_copy],
                related=[bank.entry_id],
            )

    def _plant_missing_from_bank(self) -> None:
        """A booked sale has not been paid out.

        The processor may be holding the funds for a risk review, or the
        payout failed. Either way the ledger expects money the bank has not
        received, and someone has to chase the processor.
        """
        processor = self._processors[self._rng.choice(["BRAMBLE", "OTTERLINE"])]
        booked = self._days_before(processor.settlement_delay_days)
        ledger = self._ledger_entry(self._reference("ORD"), self._amount(), processor, booked)
        self._record(
            DiscrepancyKind.MISSING_FROM_BANK,
            f"{ledger.reference} was due from {processor.name} today but has not arrived.",
            ledger=[ledger],
        )

    def _plant_missing_from_ledger(self) -> None:
        """Money arrived that nobody booked.

        Typically a customer paid an invoice that was never raised in the
        system, or quoted the wrong reference. The cash is real, so it must
        be investigated and booked, not ignored.
        """
        direct = self._processors["DIRECT"]
        reference = self._reference("INV")
        bank = self._bank_entry(reference, self._amount(), direct, f"CUSTOMER TRANSFER {reference}")
        self._record(
            DiscrepancyKind.MISSING_FROM_LEDGER,
            f"{bank.amount} arrived with reference {reference}, which is not in your records.",
            bank=[bank],
        )

    def _plant_transposition(self) -> None:
        """An amount was keyed into the ledger with two digits swapped.

        Invoice receipts are often booked by hand, and swapping adjacent
        digits is one of the most common keying errors. The difference
        between the true and the keyed amount is always a multiple of 9,
        which is what lets the engine spot it.
        """
        direct = self._processors["DIRECT"]
        reference = self._reference("INV")
        while True:
            amount = self._amount()
            digits = str(amount.minor_units)
            # Only swaps of two different digits change the amount, and a
            # swap that moves a zero to the front would drop a digit.
            positions = [
                i
                for i in range(len(digits) - 1)
                if digits[i] != digits[i + 1] and not (i == 0 and digits[1] == "0")
            ]
            if positions:
                break
        keyed = Money.from_minor_units(
            transpose_adjacent_digits(amount.minor_units, self._rng.choice(positions))
        )
        bank = self._bank_entry(reference, amount, direct, f"CUSTOMER TRANSFER {reference}")
        ledger = self._ledger_entry(reference, keyed, direct, self._date)
        self._record(
            DiscrepancyKind.TRANSPOSITION,
            f"{reference} was typed as {keyed} but the bank received {amount}: two digits swapped.",
            [bank],
            [ledger],
        )
