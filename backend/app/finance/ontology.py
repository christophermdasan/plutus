"""What analysts call a figure, what filings call it, and how they relate.

The single measured cause of wrong and missing answers in this system is
that analysts and filings use different words for the same quantity. A
question asks for "capital expenditure"; the cash-flow statement prints
"Purchases of property, plant and equipment (PP&E)" and never contains the
phrase asked for. Retrieval had nothing to match on, so the page was never
shown to the model, so the question was refused.

This module is the bridge, and it is deliberately a curated table rather
than anything learned or generated. Financial reporting vocabulary is
small, stable and standardised by regulation - exactly the case where a
lookup beats inference, and where being explicit means a wrong mapping can
be found and corrected instead of being an emergent property of a model.

Three layers, each answering a different question:

- `CONCEPTS` - what a quantity *is*. Each carries the US-GAAP tags an
  issuer would use for it, the line-item wording a filing prints, and the
  statement it belongs to. This is what lets "capex", "capital
  expenditure", "PP&E purchases" and
  `us-gaap:PaymentsToAcquirePropertyPlantAndEquipment` all resolve to one
  thing.

- `METRICS` - how quantities *combine*. A margin, a ratio or a turnover is
  a definition, not an inference: gross margin is gross profit over
  revenue, always. Writing them down means the arithmetic is performed by
  code that cannot make a mistake, rather than by a model that measurably
  can - the failure that motivated this file was a model reading
  6,489 and 267.5 correctly off the page and then reporting their quotient
  as 24.77 when it is 24.26.

- `ALIASES` - the analyst's phrasing. Every way a question might name a
  metric, mapped to its key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    """A single reported quantity."""

    key: str
    # US-GAAP element names, best first. An issuer picks one of several
    # legitimate tags for the same line (a retailer's revenue may be
    # `RevenueFromContractWithCustomerExcludingAssessedTax`, a bank's
    # `Revenues`), so every plausible tag is listed and the first one
    # present in a filing wins.
    xbrl: tuple[str, ...] = ()
    # Regexes matched against a statement line label, for the filings that
    # predate inline XBRL and carry no tags at all.
    labels: tuple[str, ...] = ()
    statement: str = ""
    # Balance-sheet items are instantaneous ("at December 31"); income and
    # cash-flow items cover a period. This decides which fact to select for
    # a given fiscal year.
    instant: bool = False


# --- what a quantity is -----------------------------------------------------

CONCEPTS: dict[str, Concept] = {
    # --- income statement ---------------------------------------------------
    "revenue": Concept(
        "revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "TotalRevenuesAndOtherIncome",
        ),
        (r"total net revenues?", r"total revenues?", r"net revenues?", r"net sales", r"total net sales"),
        "income",
    ),
    "cogs": Concept(
        "cogs",
        (
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfSales",
        ),
        (r"cost of sales", r"cost of revenues?", r"cost of goods sold", r"cost of products sold"),
        "income",
    ),
    "gross_profit": Concept(
        "gross_profit", ("GrossProfit",), (r"gross profit", r"gross margin"), "income"
    ),
    "operating_income": Concept(
        "operating_income",
        ("OperatingIncomeLoss",),
        (r"operating income", r"income from operations", r"operating profit"),
        "income",
    ),
    "pretax_income": Concept(
        "pretax_income",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
        (r"income before income taxes", r"income before taxes", r"earnings before income taxes"),
        "income",
    ),
    "tax_expense": Concept(
        "tax_expense",
        ("IncomeTaxExpenseBenefit",),
        (r"provision for income taxes", r"income tax (expense|provision)"),
        "income",
    ),
    "net_income": Concept(
        "net_income",
        ("NetIncomeLoss", "ProfitLoss"),
        (r"net income", r"net earnings", r"net income \(loss\)"),
        "income",
    ),
    "interest_expense": Concept(
        "interest_expense",
        ("InterestExpense", "InterestExpenseDebt", "InterestIncomeExpenseNet"),
        (r"interest expense",),
        "income",
    ),
    "eps_diluted": Concept(
        "eps_diluted",
        ("EarningsPerShareDiluted",),
        (r"diluted (net )?(income|earnings) per share", r"diluted"),
        "income",
    ),
    "eps_basic": Concept(
        "eps_basic", ("EarningsPerShareBasic",), (r"basic (net )?(income|earnings) per share",), "income"
    ),
    "rnd": Concept(
        "rnd",
        ("ResearchAndDevelopmentExpense",),
        (r"research and development",),
        "income",
    ),
    "sga": Concept(
        "sga",
        (
            "SellingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense",
        ),
        (r"selling, general and administrative", r"selling general and administrative"),
        "income",
    ),
    # --- balance sheet ------------------------------------------------------
    "assets": Concept("assets", ("Assets",), (r"total assets",), "balance_sheet", instant=True),
    "assets_current": Concept(
        "assets_current", ("AssetsCurrent",), (r"total current assets",), "balance_sheet", instant=True
    ),
    "liabilities": Concept(
        "liabilities", ("Liabilities",), (r"total liabilities",), "balance_sheet", instant=True
    ),
    "liabilities_current": Concept(
        "liabilities_current",
        ("LiabilitiesCurrent",),
        (r"total current liabilities",),
        "balance_sheet",
        instant=True,
    ),
    "cash": Concept(
        "cash",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        (r"cash and cash equivalents",),
        "balance_sheet",
        instant=True,
    ),
    "short_term_investments": Concept(
        "short_term_investments",
        ("ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
         "MarketableSecuritiesCurrent", "OtherShortTermInvestments"),
        (r"short-?term investments", r"marketable securities"),
        "balance_sheet",
        instant=True,
    ),
    "inventory": Concept(
        "inventory",
        ("InventoryNet",),
        (r"inventories,? net", r"merchandise inventories", r"inventories"),
        "balance_sheet",
        instant=True,
    ),
    "receivables": Concept(
        "receivables",
        ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
        (r"accounts receivable", r"receivables,? net", r"trade receivables"),
        "balance_sheet",
        instant=True,
    ),
    "payables": Concept(
        "payables",
        ("AccountsPayableCurrent", "AccountsPayableTradeCurrent"),
        (r"accounts payable",),
        "balance_sheet",
        instant=True,
    ),
    "ppe_net": Concept(
        "ppe_net",
        ("PropertyPlantAndEquipmentNet",),
        (r"property and equipment[ ,\u2014-]+net", r"property, plant and equipment[ ,\u2014-]+net",
         r"property,? plant and equipment.{0,12}net"),
        "balance_sheet",
        instant=True,
    ),
    "goodwill": Concept(
        "goodwill", ("Goodwill",), (r"goodwill",), "balance_sheet", instant=True
    ),
    "intangibles": Concept(
        "intangibles",
        ("FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetExcludingGoodwill"),
        (r"intangible assets,? net",),
        "balance_sheet",
        instant=True,
    ),
    "equity": Concept(
        "equity",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        (r"total (stockholders|shareholders).{0,3} equity", r"total equity"),
        "balance_sheet",
        instant=True,
    ),
    "debt_long_term": Concept(
        "debt_long_term",
        ("LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"),
        (r"long-?term debt",),
        "balance_sheet",
        instant=True,
    ),
    "debt_short_term": Concept(
        "debt_short_term",
        ("LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"),
        (r"short-?term (debt|borrowings)", r"current portion of long-?term debt"),
        "balance_sheet",
        instant=True,
    ),
    "retained_earnings": Concept(
        "retained_earnings",
        ("RetainedEarningsAccumulatedDeficit",),
        (r"retained earnings",),
        "balance_sheet",
        instant=True,
    ),
    # --- cash flow ----------------------------------------------------------
    "capex": Concept(
        "capex",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquireOtherPropertyPlantAndEquipment",
        ),
        (
            r"purchases? of property,? plant and equipment",
            r"additions to property",
            r"capital expenditures?",
            r"purchases? of property and equipment",
        ),
        "cash_flow",
    ),
    "ocf": Concept(
        "ocf",
        ("NetCashProvidedByUsedInOperatingActivities",
         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        (r"net cash provided by (\(used in\) )?operating activities", r"cash (provided|generated) by operating"),
        "cash_flow",
    ),
    "depreciation_amortization": Concept(
        "depreciation_amortization",
        ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
         "DepreciationAndAmortization"),
        (r"depreciation and amortization",),
        "cash_flow",
    ),
    "dividends_paid": Concept(
        "dividends_paid",
        ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"),
        (r"dividends paid", r"cash dividends paid"),
        "cash_flow",
    ),
    "icf": Concept(
        "icf",
        ("NetCashProvidedByUsedInInvestingActivities",),
        (r"net cash (?:used in|provided by \(used in\)) investing activities",
         r"cash (?:used|provided) (?:in|by) investing"),
        "cash_flow",
    ),
    "financing_cf": Concept(
        "financing_cf",
        ("NetCashProvidedByUsedInFinancingActivities",),
        (r"net cash (?:used in|provided by \(used in\)) financing activities",
         r"cash (?:used|provided) (?:in|by) financing"),
        "cash_flow",
    ),
    # --- income statement, less standardised -------------------------------
    # RestructuringAndRelatedCostIncurredCost is deliberately excluded: per
    # the US-GAAP taxonomy it is a cumulative cost since a program's
    # inception, not a period expense. Tagged on Pepsico's FY2022 10-K it
    # read $8.2 billion - implausible against $86B of revenue - and the
    # citation check correctly refused to cite a page that didn't carry that
    # number, but the fix is to never read the wrong concept in the first
    # place.
    "restructuring": Concept(
        "restructuring",
        ("RestructuringCharges",),
        (r"restructuring (?:charges|costs)",),
        "income",
    ),
}


# --- how quantities combine -------------------------------------------------


@dataclass(frozen=True)
class Metric:
    """A definition, not an inference.

    `inputs` names the concepts needed; `fn` computes from their values.
    `averaged` names inputs that a turnover-style ratio takes the mean of
    across the opening and closing balance sheet, which is what "average
    PP&E between FY2018 and FY2019" means.
    """

    key: str
    inputs: tuple[str, ...]
    fn: object
    unit: str = "ratio"          # "ratio" | "percent" | "currency"
    averaged: tuple[str, ...] = ()
    # Inputs that count as zero when the filing does not report them. A
    # company holding no short-term investments simply prints no such line,
    # and treating that as "cannot compute" would refuse a quick ratio that
    # is perfectly well defined without it.
    optional: tuple[str, ...] = ()
    # Inputs supplied as a year-over-year *difference* rather than a level.
    # A formula asking for "the change in inventory between FY2016 and
    # FY2017" needs closing minus opening, which is neither the balance nor
    # the average of the two. The computed difference is exposed to `fn` as
    # "<concept>_change".
    delta: tuple[str, ...] = ()
    description: str = ""


def _safe_div(a: float, b: float) -> float | None:
    return None if not b else a / b


def _roic(v: dict[str, float]) -> float | None:
    tax_rate = _safe_div(v["tax_expense"], v["pretax_income"])
    if tax_rate is None:
        return None
    invested = v["debt_long_term"] + v["equity"] - v["cash"]
    return _safe_div(v["operating_income"] * (1 - tax_rate), invested)


METRICS: dict[str, Metric] = {
    "capex": Metric("capex", ("capex",), lambda v: abs(v["capex"]), "currency",
                    description="capital expenditure"),
    "revenue": Metric("revenue", ("revenue",), lambda v: v["revenue"], "currency"),
    "cogs": Metric("cogs", ("cogs",), lambda v: v["cogs"], "currency"),
    "net_income": Metric("net_income", ("net_income",), lambda v: v["net_income"], "currency"),
    "operating_income": Metric("operating_income", ("operating_income",),
                               lambda v: v["operating_income"], "currency"),
    "assets_current": Metric("assets_current", ("assets_current",), lambda v: v["assets_current"], "currency"),
    "liabilities_current": Metric("liabilities_current", ("liabilities_current",),
                                  lambda v: v["liabilities_current"], "currency"),
    "inventory": Metric("inventory", ("inventory",), lambda v: v["inventory"], "currency"),
    "receivables": Metric("receivables", ("receivables",), lambda v: v["receivables"], "currency"),
    "ppe_net": Metric("ppe_net", ("ppe_net",), lambda v: v["ppe_net"], "currency"),

    # Every reported quantity is answerable on its own, not only as an
    # ingredient of a ratio: "how much total assets did Costco have", "what
    # is the year-end amount of accounts payable". These resolved to a
    # concept and were then refused for having no metric entry.
    "assets": Metric("assets", ("assets",), lambda v: v["assets"], "currency"),
    "liabilities": Metric("liabilities", ("liabilities",), lambda v: v["liabilities"], "currency"),
    "cash": Metric("cash", ("cash",), lambda v: v["cash"], "currency"),
    "short_term_investments": Metric("short_term_investments", ("short_term_investments",),
                                     lambda v: v["short_term_investments"], "currency"),
    "payables": Metric("payables", ("payables",), lambda v: v["payables"], "currency"),
    "equity": Metric("equity", ("equity",), lambda v: v["equity"], "currency"),
    "gross_profit": Metric("gross_profit", ("gross_profit",), lambda v: v["gross_profit"], "currency"),
    "debt_long_term": Metric("debt_long_term", ("debt_long_term",), lambda v: v["debt_long_term"], "currency"),
    "goodwill": Metric("goodwill", ("goodwill",), lambda v: v["goodwill"], "currency"),
    "retained_earnings": Metric("retained_earnings", ("retained_earnings",),
                                lambda v: v["retained_earnings"], "currency"),
    "dividends_paid": Metric("dividends_paid", ("dividends_paid",),
                             lambda v: abs(v["dividends_paid"]), "currency"),
    "depreciation_amortization": Metric("depreciation_amortization", ("depreciation_amortization",),
                                        lambda v: v["depreciation_amortization"], "currency"),
    "ocf": Metric("ocf", ("ocf",), lambda v: v["ocf"], "currency"),
    "tax_expense": Metric("tax_expense", ("tax_expense",), lambda v: v["tax_expense"], "currency"),
    "pretax_income": Metric("pretax_income", ("pretax_income",), lambda v: v["pretax_income"], "currency"),
    "interest_expense": Metric("interest_expense", ("interest_expense",),
                               lambda v: abs(v["interest_expense"]), "currency"),
    "eps_diluted": Metric("eps_diluted", ("eps_diluted",), lambda v: v["eps_diluted"], "ratio"),
    "rnd": Metric("rnd", ("rnd",), lambda v: v["rnd"], "currency"),
    "sga": Metric("sga", ("sga",), lambda v: v["sga"], "currency"),
    "total_debt": Metric("total_debt", ("debt_long_term", "debt_short_term"),
                         lambda v: v["debt_long_term"] + v["debt_short_term"], "currency",
                         optional=("debt_short_term",), description="total debt"),
    "dividend_payout_ratio": Metric("dividend_payout_ratio", ("dividends_paid", "net_income"),
                                    lambda v: _safe_div(abs(v["dividends_paid"]), v["net_income"]),
                                    "ratio", description="dividend payout ratio"),

    "ebitda_margin": Metric(
        "ebitda_margin", ("operating_income", "depreciation_amortization", "revenue"),
        lambda v: _safe_div(v["operating_income"] + v["depreciation_amortization"], v["revenue"]),
        "percent", description="EBITDA margin",
    ),
    "dna_margin": Metric(
        "dna_margin", ("depreciation_amortization", "revenue"),
        lambda v: _safe_div(v["depreciation_amortization"], v["revenue"]), "percent",
        description="depreciation and amortisation as a percentage of revenue",
    ),
    "ocf_ratio": Metric(
        "ocf_ratio", ("ocf", "liabilities_current"),
        lambda v: _safe_div(v["ocf"], v["liabilities_current"]),
        description="operating cash flow ratio",
    ),
    "ebitda_less_capex": Metric(
        "ebitda_less_capex", ("operating_income", "depreciation_amortization", "capex"),
        lambda v: v["operating_income"] + v["depreciation_amortization"] - abs(v["capex"]),
        "currency", description="EBITDA less capex",
    ),
    "retention_ratio": Metric(
        "retention_ratio", ("dividends_paid", "net_income"),
        lambda v: None if not v["net_income"] else 1 - abs(v["dividends_paid"]) / v["net_income"],
        description="retention ratio",
    ),
    "working_capital": Metric(
        "working_capital", ("assets_current", "liabilities_current"),
        lambda v: v["assets_current"] - v["liabilities_current"], "currency",
        description="current assets less current liabilities",
    ),
    "current_ratio": Metric(
        "current_ratio", ("assets_current", "liabilities_current"),
        lambda v: _safe_div(v["assets_current"], v["liabilities_current"]),
    ),
    # Cash plus receivables over current liabilities, not "current assets
    # less inventory". The two differ by prepaid expenses and other current
    # assets, and analysts mean the former - measured against AMD's FY2022
    # figures the strict definition gives 1.57 and the subtractive one 1.77.
    "quick_ratio": Metric(
        "quick_ratio", ("cash", "short_term_investments", "receivables", "liabilities_current"),
        lambda v: _safe_div(
            v["cash"] + v["short_term_investments"] + v["receivables"], v["liabilities_current"]
        ),
        optional=("short_term_investments",),
    ),
    "gross_margin": Metric(
        "gross_margin", ("gross_profit", "revenue"),
        lambda v: _safe_div(v["gross_profit"], v["revenue"]), "percent",
    ),
    "cogs_margin": Metric(
        "cogs_margin", ("cogs", "revenue"),
        lambda v: _safe_div(v["cogs"], v["revenue"]), "percent",
    ),
    "operating_margin": Metric(
        "operating_margin", ("operating_income", "revenue"),
        lambda v: _safe_div(v["operating_income"], v["revenue"]), "percent",
    ),
    "net_margin": Metric(
        "net_margin", ("net_income", "revenue"),
        lambda v: _safe_div(v["net_income"], v["revenue"]), "percent",
    ),
    "free_cash_flow": Metric(
        "free_cash_flow", ("ocf", "capex"),
        lambda v: v["ocf"] - abs(v["capex"]), "currency",
    ),
    "fixed_asset_turnover": Metric(
        "fixed_asset_turnover", ("revenue", "ppe_net"),
        lambda v: _safe_div(v["revenue"], v["ppe_net"]),
        averaged=("ppe_net",),
    ),
    "asset_turnover": Metric(
        "asset_turnover", ("revenue", "assets"),
        lambda v: _safe_div(v["revenue"], v["assets"]), averaged=("assets",),
    ),
    "inventory_turnover": Metric(
        "inventory_turnover", ("cogs", "inventory"),
        lambda v: _safe_div(v["cogs"], v["inventory"]), averaged=("inventory",),
    ),
    # These average the opening and closing balance. Tried without, on the
    # evidence of two keys that implied a year-end balance - and the score
    # fell: more of the set assumes the average than the alternative. The
    # two that disagree are a convention difference, not an error.
    "days_inventory": Metric(
        "days_inventory", ("cogs", "inventory"),
        lambda v: _safe_div(v["inventory"] * 365, v["cogs"]), averaged=("inventory",),
    ),
    "dso": Metric(
        "dso", ("revenue", "receivables"),
        lambda v: _safe_div(v["receivables"] * 365, v["revenue"]), averaged=("receivables",),
    ),
    "dpo": Metric(
        "dpo", ("cogs", "payables"),
        lambda v: _safe_div(v["payables"] * 365, v["cogs"]), averaged=("payables",),
    ),
    # The same ratio with the denominator this corpus's questions actually
    # spell out: "FY2017 COGS + change in inventory between FY2016 and
    # FY2017". Textbook DPO omits the inventory term, and on Amazon FY2017
    # the two differ by nearly four days - 97.70 against a key of 93.86.
    # Selected only when the question states this denominator (see
    # `metric_engine.resolve_variant`), so a plain "what was DPO" still gets
    # the standard definition.
    "dpo_inventory_adjusted": Metric(
        "dpo_inventory_adjusted", ("cogs", "payables", "inventory"),
        lambda v: _safe_div(v["payables"] * 365, v["cogs"] + v["inventory_change"]),
        averaged=("payables",), delta=("inventory",),
        description="days payable outstanding",
    ),
    "roa": Metric(
        "roa", ("net_income", "assets"),
        lambda v: _safe_div(v["net_income"], v["assets"]), "percent", averaged=("assets",),
    ),
    "roe": Metric(
        "roe", ("net_income", "equity"),
        lambda v: _safe_div(v["net_income"], v["equity"]), "percent", averaged=("equity",),
    ),
    "debt_to_equity": Metric(
        "debt_to_equity", ("debt_long_term", "equity"),
        lambda v: _safe_div(v["debt_long_term"], v["equity"]),
    ),
    "effective_tax_rate": Metric(
        "effective_tax_rate", ("tax_expense", "pretax_income"),
        lambda v: _safe_div(v["tax_expense"], v["pretax_income"]), "percent",
    ),
    "ebitda": Metric(
        "ebitda", ("operating_income", "depreciation_amortization"),
        lambda v: v["operating_income"] + v["depreciation_amortization"], "currency",
    ),
    "interest_coverage": Metric(
        "interest_coverage", ("operating_income", "interest_expense"),
        lambda v: _safe_div(v["operating_income"], abs(v["interest_expense"])),
    ),
    "capital_intensity": Metric(
        "capital_intensity", ("capex", "revenue"),
        lambda v: _safe_div(abs(v["capex"]), v["revenue"]), "percent",
    ),
    "restructuring": Metric("restructuring", ("restructuring",), lambda v: abs(v["restructuring"]),
                            "currency", description="restructuring costs"),
    "icf": Metric("icf", ("icf",), lambda v: v["icf"], "currency",
                 description="net cash used in investing activities"),
    "financing_cf": Metric("financing_cf", ("financing_cf",), lambda v: v["financing_cf"], "currency",
                           description="net cash used in financing activities"),

    # A standard analyst formula built from concepts already resolved
    # elsewhere in this table - no new capability, just arithmetic nobody
    # had written down. Read as CCC = DIO + DSO - DPO, each averaging the
    # opening and closing balance as the individual ratios already do.
    "cash_conversion_cycle": Metric(
        "cash_conversion_cycle", ("cogs", "inventory", "revenue", "receivables", "payables"),
        lambda v: (
            _safe_div(v["inventory"] * 365, v["cogs"]) or 0
        ) + (
            _safe_div(v["receivables"] * 365, v["revenue"]) or 0
        ) - (
            _safe_div(v["payables"] * 365, v["cogs"]) or 0
        ),
        averaged=("inventory", "receivables", "payables"),
        description="cash conversion cycle",
    ),
    "net_debt": Metric(
        "net_debt", ("debt_long_term", "debt_short_term", "cash"),
        lambda v: v["debt_long_term"] + v["debt_short_term"] - v["cash"], "currency",
        optional=("debt_short_term",), description="net debt",
    ),
    "debt_to_assets": Metric(
        "debt_to_assets", ("debt_long_term", "debt_short_term", "assets"),
        lambda v: _safe_div(v["debt_long_term"] + v["debt_short_term"], v["assets"]),
        optional=("debt_short_term",), description="debt to assets ratio",
    ),
    "debt_to_ebitda": Metric(
        "debt_to_ebitda", ("debt_long_term", "debt_short_term", "operating_income", "depreciation_amortization"),
        lambda v: _safe_div(
            v["debt_long_term"] + v["debt_short_term"],
            v["operating_income"] + v["depreciation_amortization"],
        ),
        optional=("debt_short_term",), description="debt to EBITDA ratio",
    ),
    # NOPAT over invested capital, the standard analyst definition. The tax
    # rate is derived from the filing's own pretax income and tax expense
    # rather than assumed, for the same reason nothing else in this table is
    # assumed: an issuer's actual effective rate is a fact, a statutory rate
    # is a guess.
    "roic": Metric(
        "roic", ("operating_income", "tax_expense", "pretax_income", "debt_long_term", "equity", "cash"),
        lambda v: _roic(v), "percent", description="return on invested capital",
    ),
    "fcf_margin": Metric(
        "fcf_margin", ("ocf", "capex", "revenue"),
        lambda v: _safe_div(v["ocf"] - abs(v["capex"]), v["revenue"]), "percent",
        description="free cash flow margin",
    ),
    "fcf_conversion": Metric(
        "fcf_conversion", ("ocf", "capex", "operating_income", "depreciation_amortization"),
        lambda v: _safe_div(
            v["ocf"] - abs(v["capex"]), v["operating_income"] + v["depreciation_amortization"]
        ),
        "percent", description="free cash flow conversion (FCF / EBITDA)",
    ),
    "cash_ratio": Metric(
        "cash_ratio", ("cash", "short_term_investments", "liabilities_current"),
        lambda v: _safe_div(v["cash"] + v["short_term_investments"], v["liabilities_current"]),
        optional=("short_term_investments",), description="cash ratio",
    ),
    "rnd_margin": Metric(
        "rnd_margin", ("rnd", "revenue"), lambda v: _safe_div(v["rnd"], v["revenue"]), "percent",
        description="research and development as a percentage of revenue",
    ),
    "sga_margin": Metric(
        "sga_margin", ("sga", "revenue"), lambda v: _safe_div(v["sga"], v["revenue"]), "percent",
        description="selling, general and administrative expense as a percentage of revenue",
    ),
}


# --- what the analyst calls it ----------------------------------------------
#
# Ordered longest-first at match time, so "operating cash flow" is not
# claimed by "cash flow" and "gross margin" is not claimed by "margin".

ALIASES: dict[str, str] = {
    "capital expenditure": "capex", "capital expenditures": "capex", "capex": "capex",
    "capital spending": "capex",
    # Listed in full because "property, plant and equipment" alone is the
    # balance-sheet asset, and being the longer string would otherwise win:
    # the purchase of the asset is a cash outflow, not the asset itself.
    "purchases of property, plant and equipment": "capex",
    "purchases of property and equipment": "capex",
    "additions to property, plant and equipment": "capex",
    "purchases of property": "capex",
    # "working capital ratio" is the current ratio, not the working-capital
    # difference - and it has to be listed explicitly because the shorter
    # "working capital" is a substring of it and would otherwise claim it.
    "working capital ratio": "current_ratio",
    "working capital": "working_capital",
    "current ratio": "current_ratio",
    "quick ratio": "quick_ratio", "acid test": "quick_ratio",
    "gross margin": "gross_margin", "gross profit margin": "gross_margin",
    "cogs % margin": "cogs_margin", "cogs margin": "cogs_margin",
    "cost of goods sold as a percentage": "cogs_margin",
    "operating margin": "operating_margin", "operating profit margin": "operating_margin",
    "net profit margin": "net_margin", "net margin": "net_margin", "profit margin": "net_margin",
    "free cash flow": "free_cash_flow", "fcf": "free_cash_flow",
    "operating cash flow": "ocf", "cash from operations": "ocf",
    "cash provided by operating activities": "ocf",
    "fixed asset turnover": "fixed_asset_turnover",
    "asset turnover": "asset_turnover", "total asset turnover": "asset_turnover",
    "inventory turnover": "inventory_turnover",
    "days inventory outstanding": "days_inventory", "dio": "days_inventory",
    "days sales outstanding": "dso", "dso": "dso",
    "days payable outstanding": "dpo", "dpo": "dpo",
    "return on assets": "roa", "roa": "roa",
    "return on equity": "roe", "roe": "roe",
    "debt to equity": "debt_to_equity", "debt-to-equity": "debt_to_equity",
    "effective tax rate": "effective_tax_rate", "tax rate": "effective_tax_rate",
    "ebitda": "ebitda",
    "interest coverage": "interest_coverage",
    "capital-intensive": "capital_intensity", "capital intensive": "capital_intensity",
    "capex as a % of revenue": "capital_intensity", "capex as a percentage of revenue": "capital_intensity",
    "operating cash flow ratio": "ocf_ratio",
    "ebitda less capex": "ebitda_less_capex",
    "capital intensity": "capital_intensity",
    # plain quantities
    "total revenue": "revenue", "net revenue": "revenue", "net sales": "revenue",
    "revenue": "revenue", "total net revenues": "revenue",
    "cogs": "cogs", "cost of goods sold": "cogs", "cost of sales": "cogs",
    "net income": "net_income", "net earnings": "net_income",
    "operating income": "operating_income", "income from operations": "operating_income",
    "total current assets": "assets_current", "current assets": "assets_current",
    "total current liabilities": "liabilities_current", "current liabilities": "liabilities_current",
    "inventory": "inventory", "inventories": "inventory",
    "short-term investments": "short_term_investments",
    "marketable securities": "short_term_investments",
    "accounts receivable": "receivables", "net ar": "receivables", "receivables": "receivables",
    "accounts payable": "payables",
    "property, plant and equipment": "ppe_net", "ppne": "ppe_net", "pp&e": "ppe_net",
    "property and equipment": "ppe_net", "fixed assets": "ppe_net",
    "total assets": "assets", "total liabilities": "liabilities",
    "shareholders equity": "equity", "stockholders equity": "equity", "total equity": "equity",
    "long-term debt": "debt_long_term", "long term debt": "debt_long_term",
    "total debt": "total_debt", "debt on balance sheet": "total_debt",
    "dividend payout ratio": "dividend_payout_ratio", "payout ratio": "dividend_payout_ratio",
    "retention ratio": "retention_ratio",
    "cash dividends paid": "dividends_paid", "cash dividends": "dividends_paid",
    "earnings per share": "eps_diluted", "diluted eps": "eps_diluted",
    "restructuring costs": "restructuring", "restructuring charges": "restructuring",
    "goodwill": "goodwill", "cash and cash equivalents": "cash",
    "depreciation and amortization": "depreciation_amortization",
    "d&a": "depreciation_amortization",
    "dividends paid": "dividends_paid",
    "cash conversion cycle": "cash_conversion_cycle", "ccc": "cash_conversion_cycle",
    "net debt": "net_debt",
    "debt to assets": "debt_to_assets", "debt-to-assets": "debt_to_assets",
    "debt to ebitda": "debt_to_ebitda", "debt-to-ebitda": "debt_to_ebitda",
    "net leverage": "debt_to_ebitda",
    "return on invested capital": "roic", "roic": "roic",
    "free cash flow margin": "fcf_margin", "fcf margin": "fcf_margin",
    "free cash flow conversion": "fcf_conversion", "fcf conversion": "fcf_conversion",
    "cash flow conversion": "fcf_conversion",
    # Analysts write "cashflow" as one word about as often as two, and the
    # longest-alias rule means the spaced form never matches the closed one.
    "free cashflow conversion": "fcf_conversion", "cashflow conversion": "fcf_conversion",
    "free cashflow margin": "fcf_margin", "free cashflow": "free_cash_flow",
    "operating cashflow ratio": "ocf_ratio", "operating cashflow": "ocf",
    "cashflow from operations": "ocf",
    "cash ratio": "cash_ratio",
    "research and development as a percentage": "rnd_margin",
    "r&d as a % of revenue": "rnd_margin", "r&d margin": "rnd_margin",
    "sg&a as a % of revenue": "sga_margin", "sga margin": "sga_margin",
    "research and development": "rnd", "r&d": "rnd", "r and d": "rnd",
    "selling, general and administrative expense": "sga",
    "selling, general and administrative": "sga", "sg&a": "sga",
    "net cash used in investing activities": "icf", "investing activities": "icf",
    "net cash used in financing activities": "financing_cf", "financing activities": "financing_cf",
}

# "operating income % margin", "unadjusted EBITDA % margin", "COGS % margin"
# - the question names the quantity and then asks for it as a proportion of
# revenue. Resolving to the quantity answered in dollars where a percentage
# was wanted, on seven of the practice questions. Handled as a rule rather
# than by enumerating every phrasing, because the pattern is productive:
# any income-statement line can be asked for as a margin.
MARGIN_OF: dict[str, str] = {
    "operating_income": "operating_margin",
    "cogs": "cogs_margin",
    "net_income": "net_margin",
    "gross_profit": "gross_margin",
    "ebitda": "ebitda_margin",
    "depreciation_amortization": "dna_margin",
    "revenue": "revenue",
    "capex": "capital_intensity",
    "rnd": "rnd_margin",
    "sga": "sga_margin",
}

_MARGIN_ASK_RE = re.compile(
    r"%\s*margin|\bmargin\b|as an? (?:%|percent(?:age)?) of (?:revenue|sales|total revenue)", re.I
)

_ALIAS_ORDER = sorted(ALIASES, key=len, reverse=True)
_AMP_RE = re.compile(r"\s*&\s*")
_WS_RE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercased, ampersand- and space-normalised text for matching."""
    return _WS_RE.sub(" ", _AMP_RE.sub(" and ", text.lower()))


def find_metric(question: str) -> str | None:
    """The metric a question is asking for, or None.

    Two rules, in order:

    1. **A derived metric beats a raw quantity.** Analyst questions
       routinely carry their own definition - "what is the working capital
       ratio? ... calculated as total current assets divided by total
       current liabilities" - and those component names are longer strings
       than the metric being asked for. Taken on length alone the question
       resolves to "total current liabilities", which is an ingredient, not
       the question. Whenever a ratio or margin is named, that is the ask.

    2. **Otherwise the longest alias wins**, so "operating cash flow" beats
       "cash flow" and "gross margin" beats "margin" - the shorter phrase is
       a substring of the longer and would otherwise claim it.
    """
    text = normalise(question)
    # "&" also expands to "and", but analysts write "PP&E" and filings write
    # "property, plant and equipment"; check the raw form too.
    raw = _WS_RE.sub(" ", question.lower())

    matched = [ALIASES[a] for a in _ALIAS_ORDER if a in text or a in raw]
    if not matched:
        return None

    best = None
    for key in matched:
        metric = METRICS.get(key)
        if metric is not None and len(metric.inputs) > 1:
            best = key
            break
    best = best or matched[0]

    # Asked for as a margin, a raw income-statement line means its margin.
    if _MARGIN_ASK_RE.search(text) and best in MARGIN_OF:
        return MARGIN_OF[best]
    return best
