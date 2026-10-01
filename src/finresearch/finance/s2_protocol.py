"""The current narrow S2 protocol, shared by export and independent gate replay."""
from finresearch.contracts import MetricObservation, CalculationResult
from .calculations import calculate_growth, calculate_flow_ratio, calculate_stock_to_flow, calculate_growth_gap


def calculate_s2_comparison(selected: dict[tuple[str, int], MetricObservation]) -> dict[str, CalculationResult]:
    """Calculate three annual changes and four ratios from the same PIT selection."""
    calculations = {
        "revenue_growth_2024": calculate_growth(selected[("revenue", 2024)], selected[("revenue", 2023)]),
        "ocf_growth_2024": calculate_growth(selected[("operating_cash_flow_net", 2024)], selected[("operating_cash_flow_net", 2023)]),
        "ar_growth_2024": calculate_growth(selected[("accounts_receivable", 2024)], selected[("accounts_receivable", 2023)]),
    }
    for year in (2023, 2024):
        calculations[f"ocf_to_revenue_{year}"] = calculate_flow_ratio(selected[("operating_cash_flow_net", year)], selected[("revenue", year)])
        calculations[f"ar_to_revenue_{year}"] = calculate_stock_to_flow(selected[("accounts_receivable", year)], selected[("revenue", year)])
    calculations["revenue_minus_ocf_growth_gap"] = calculate_growth_gap(calculations["revenue_growth_2024"], calculations["ocf_growth_2024"])
    return calculations
