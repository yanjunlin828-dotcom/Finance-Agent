from __future__ import annotations

from finresearch.ingestion import normalize_page_text


def test_normalize_page_text_keeps_financial_numbers_and_line_order() -> None:
    raw = "  合并利润表\r\n  营业收入   29,838,069,162.26  \r\n\r\n 单位：元 "
    assert normalize_page_text(raw) == "合并利润表\n营业收入 29,838,069,162.26\n单位:元"
