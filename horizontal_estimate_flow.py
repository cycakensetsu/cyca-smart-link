"""Build the Numbers-compatible estimate from the current upload, not demo data."""

import pandas as pd

from estimate_pipeline import apply_company_profit_to_details
from template_fill_test_v2 import estimate_from_production, fill_estimate_v2


def build_horizontal_estimate(detail_df, base_cost_df, quoted_cost_df, metadata=None):
    """Allocate the calculated vendor totals to detail rows and fill the workbook."""
    if detail_df is None or detail_df.empty:
        raise ValueError("見積書の明細が読み取れませんでした。元のPDFを確認してください。")

    detail_vendors = set(detail_df["見積元"].dropna().astype(str))
    cost_vendors = set(base_cost_df["見積元"].dropna().astype(str))
    if detail_vendors != cost_vendors:
        raise ValueError("見積元と明細の対応が一致しません。抽出結果を確認してください。")

    profit_by_vendor = {}
    for vendor, group in base_cost_df.groupby("見積元", sort=False):
        key = str(vendor)
        base = pd.to_numeric(group["原価金額"], errors="coerce").fillna(0).sum()
        quoted = quoted_cost_df[quoted_cost_df["見積元"].astype(str) == key]
        target = pd.to_numeric(quoted["見積金額"], errors="coerce").fillna(0).sum()
        profit_by_vendor[key] = int(round(target - base))

    allocated_detail, allocated_cost = apply_company_profit_to_details(
        detail_df, base_cost_df, profit_by_vendor
    )
    actual = int(round(pd.to_numeric(allocated_detail["見積金額"], errors="coerce").fillna(0).sum()))
    expected = int(round(pd.to_numeric(quoted_cost_df["見積金額"], errors="coerce").fillna(0).sum()))
    if actual != expected:
        raise ValueError(f"明細合計（{actual:,}円）と出力合計（{expected:,}円）が一致しません。")
    estimate = estimate_from_production(allocated_detail, allocated_cost, metadata=metadata)
    return fill_estimate_v2(estimate, save_to_disk=False)
