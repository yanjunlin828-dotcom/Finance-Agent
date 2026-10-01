"""Independent S7 source/number checks; never certifies semantic causality."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import re


def amount(value) -> Decimal:
    """Parse a finite decimal; floats/bools cannot silently become gold labels."""
    if isinstance(value, (float, bool)):
        raise ValueError("amount requires exact text or Decimal")
    result = Decimal(str(value).replace(",", ""))
    if not result.is_finite():
        raise ValueError("nonfinite amount")
    return result


def gold_values(gold: dict) -> dict[tuple, Decimal]:
    """Read explicit reviewed yearly labels; duplicate fact keys fail closed."""
    result = {}
    for row in gold["rows"]:
        for year, value in row["values"].items():
            key = row["company_id"], int(year), row["metric_id"]
            if key in result:
                raise ValueError("duplicate gold fact")
            result[key] = amount(value)
    return result


def anchor_gold(gold: dict, corpus) -> list[dict]:
    """Independently bind labels to ordered raw numbers and full table headings.

    Inputs: previously reviewed row locations and canonical corpus pages.
    Output: nine source witness records. The disclosure date stays the actual
    annual report publication date; comparative columns are not historical PIT.
    """
    gold_values(gold)
    witnesses = []
    for row in gold["rows"]:
        doc = corpus.documents[row["document_id"]]
        if doc.ts_code != row["company_id"]:
            raise ValueError("gold source company mismatch")
        page = next(p for p in corpus.pages.values() if p.document_id == row["document_id"] and p.pdf_page == row["pdf_page"])
        header = next(p for p in corpus.pages.values() if p.document_id == row["document_id"] and p.pdf_page == row["header_page"])
        if any(t not in header.normalized_text for t in row["header_excerpts"]):
            raise ValueError("gold table heading mismatch")
        lines = page.normalized_text.splitlines()
        positions = [i for i, text in enumerate(lines) if text.startswith(row["row_prefix"])]
        if len(positions) != 1:
            raise ValueError("gold row must be unique")
        text = "\n".join(lines[positions[0]:positions[0] + row["row_line_count"]])
        # Require decimal money tokens, excluding note references and years.
        tokens = re.findall(r"-?\d[\d,]*\.\d{2}(?!\d)", text)
        expected = [amount(row["values"][str(y)]) for y in (2024, 2023)]
        if [amount(t) for t in tokens] != expected:
            raise ValueError("gold amounts/order do not match literal source row")
        witnesses.append({"fact_group_id": f"{row['company_id']}:{row['metric_id']}:2023-2024",
            "company_id": row["company_id"], "document_id": row["document_id"], "pdf_page": row["pdf_page"],
            "line_start": positions[0] + 1, "literal_row": text, "header_excerpts": row["header_excerpts"],
            "published_on": doc.published_on.isoformat(), "source_sha256": doc.sha256,
            "review_role": "PREVIOUSLY_REVIEWED_DEVELOPMENT_FACT"})
    return witnesses


def expected_calculations(values: dict) -> dict[str, dict]:
    """Independent eight-formula oracle per company, no production calculators.

    Complete annual, consolidated CNY observations only. Negative growth bases
    return NEGATIVE_BASE, flow ratios may be negative; stock/flow is not turnover.
    """
    groups = {}
    for company in sorted({key[0] for key in values}):
        get = lambda metric, year: values[(company, year, metric)]
        rows = {}
        for metric in ("revenue", "operating_cash_flow_net", "accounts_receivable"):
            old, new = get(metric, 2023), get(metric, 2024)
            status = "ZERO_DENOMINATOR" if old == 0 else "NEGATIVE_BASE" if old < 0 else "VALID"
            rows[metric + "_growth"] = {"status": status, "value": (new - old) / old if status == "VALID" else None, "output_unit": "RATIO"}
        for year in (2023, 2024):
            rev = get("revenue", year)
            for prefix, metric in (("ocf", "operating_cash_flow_net"), ("ar", "accounts_receivable")):
                status = "ZERO_DENOMINATOR" if rev == 0 else "VALID"
                rows[f"{prefix}_revenue_ratio_{year}"] = {"status": status, "value": get(metric, year)/rev if rev != 0 else None, "output_unit": "RATIO"}
        a, b = rows["revenue_growth"], rows["operating_cash_flow_net_growth"]
        valid = a["status"] == b["status"] == "VALID"
        rows["revenue_ocf_growth_gap_pp"] = {"status": "VALID" if valid else "INCOMPARABLE", "value": (a["value"]-b["value"])*100 if valid else None, "output_unit": "PERCENTAGE_POINT"}
        groups[company] = rows
    return groups


def validate_evidence(item: dict, corpus, context: dict) -> bool:
    """Check exact line window, hash and authoritative scope; no relevance claim."""
    try:
        doc = corpus.documents[item["document_id"]]
        page = corpus.pages[item["page_id"]]
        return (doc.ts_code == item["company_id"] and item["company_id"] in context["company_ids"]
            and int(doc.reporting_period) in context["fiscal_years"]
            and doc.published_on.isoformat() <= context["as_of_date"]
            and page.document_id == item["document_id"] and page.pdf_page == item["pdf_page"]
            and 1 <= item["line_start"] <= item["line_end"] <= len(page.normalized_text.splitlines())
            and "\n".join(page.normalized_text.splitlines()[item["line_start"]-1:item["line_end"]]) == item["text"]
            and hashlib.sha256(item["text"].encode()).hexdigest() == item["evidence_sha256"])
    except (KeyError, TypeError, AttributeError):
        return False


def audit_state(state: dict, gold: dict, corpus, tolerance="0.000001") -> dict:
    """Score all requested facts; missing outputs remain in fixed denominators.

    Strict company/year/value/unit/date/period/source checks detect corrupt
    artifacts independently. Exact citations are mechanical checks only.
    Human question relevance and support/weakening judgements remain PENDING.
    """
    expected = gold_values(gold)
    context = state["context"]
    expected = {k:v for k,v in expected.items() if k[0] in context["company_ids"]}
    oracle = expected_calculations(expected)
    issues, review_items = [], []
    evidence = {}
    for item in state.get("evidence", []):
        if item["evidence_id"] in evidence or not validate_evidence(item, corpus, context):
            issues.append({"code": "INVALID_EVIDENCE", "id": item["evidence_id"]})
        else:
            evidence[item["evidence_id"]] = item
    gold_rows = {(r["company_id"], r["metric_id"]):r for r in gold["rows"]}
    seen, correct = set(), set()
    for row in state.get("observations", []):
        key = row.get("company_id"), row.get("fiscal_year"), row.get("metric_id")
        if key in seen or key not in expected:
            issues.append({"code": "DUPLICATE_OR_OUT_OF_SCOPE_FACT", "key": list(key)})
            continue
        seen.add(key)
        try:
            source = gold_rows[key[0],key[2]]
            doc = corpus.documents[source["document_id"]]
            refs = row.get("evidence_refs", [])
            is_stock = key[2] == "accounts_receivable"
            ok = (row.get("value_status") == "OBSERVED" and amount(row.get("standard_value")) == expected[key]
                and row.get("standard_unit") == "CNY_YUAN" and row.get("currency") == "CNY"
                and row.get("statement_scope") == "CONSOLIDATED"
                and row.get("document_id") == source["document_id"] and row.get("document_sha256") == doc.sha256
                and row.get("document_published_on") == doc.published_on.isoformat()
                and doc.published_on.isoformat() <= context["as_of_date"]
                and row.get("period_kind") == ("STOCK" if is_stock else "FLOW")
                and (row.get("observed_at") == f"{key[1]}-12-31" if is_stock else
                     row.get("period_start") == f"{key[1]}-01-01" and row.get("period_end") == f"{key[1]}-12-31")
                and bool(refs) and all(r.get("evidence_id") in evidence for r in refs)
                and any(evidence[r["evidence_id"]]["document_id"] == source["document_id"]
                        and evidence[r["evidence_id"]]["pdf_page"] == source["pdf_page"] for r in refs if r["evidence_id"] in evidence))
            if ok:
                correct.add(key)
            else:
                issues.append({"code": "WRONG_FACT_OR_PROVENANCE", "key": list(key)})
        except (KeyError, ValueError, TypeError, InvalidOperation):
            issues.append({"code": "MALFORMED_FACT", "key": list(key)})
    observation_ids={o.get("observation_id"):o for o in state.get("observations", [])}
    calculation_ids={c.get("calculation_id"):c for rows in state.get("calculations",{}).values() for c in rows.values() if c.get("calculation_id")}
    good_calcs, bad_calcs, provided = 0, [], 0
    tolerance = amount(tolerance)
    for company, rows in oracle.items():
        actual = state.get("calculations", {}).get(company, {})
        for name, target in rows.items():
            item = actual.get(name)
            if item is None:
                continue
            provided += 1
            try:
                ok = item.get("status") == target["status"] and item.get("output_unit") == target["output_unit"]
                ok = ok and (item.get("value") is None if target["value"] is None else
                             abs(amount(item.get("value"))-target["value"]) <= tolerance)
                if "company_id" in item:
                    ok=ok and item["company_id"]==company and item.get("currency")=="CNY" and item.get("statement_scope")=="CONSOLIDATED"
                    year=int(name.rsplit('_',1)[-1]) if name.rsplit('_',1)[-1].isdigit() else None
                    ok=ok and item.get("comparison_period")==([year,year] if year else [2023,2024])
                    inputs=[observation_ids.get(i) or calculation_ids.get(i) for i in item.get("input_ids",[])]
                    ok=ok and bool(inputs) and all(x and x.get("company_id")==company for x in inputs)
            except (ValueError, TypeError, InvalidOperation):
                ok = False
            if ok:
                good_calcs += 1
            else:
                bad_calcs.append({"company_id":company,"name":name,"actual":item,"expected":{k:str(v) if isinstance(v,Decimal) else v for k,v in target.items()}})
        extra = set(actual)-set(rows)
        if extra:
            issues.append({"code":"UNKNOWN_CALCULATION","company_id":company,"names":sorted(extra)})
    if set(state.get("calculations", {}))-set(oracle):
        issues.append({"code":"OUT_OF_SCOPE_CALCULATION_COMPANY"})
    good_quotes, all_quotes = 0, 0
    claim_ids = set()
    for claim in state.get("claims", []):
        if claim.get("claim_id") in claim_ids or claim.get("company_id") not in context["company_ids"]:
            issues.append({"code":"INVALID_CLAIM_ID_OR_COMPANY","id":claim.get("claim_id")})
        claim_ids.add(claim.get("claim_id"))
        for field,known in (("calculation_ids",calculation_ids),("observation_ids",observation_ids),("evidence_ids",evidence)):
            if any(i not in known or known[i].get("company_id")!=claim.get("company_id") for i in claim.get(field,[])):
                issues.append({"code":"INVALID_CLAIM_DEPENDENCY","id":claim.get("claim_id"),"field":field})
        if claim.get("kind") == "DISCLOSED":
            all_quotes += 1
            text = claim.get("text", "")
            quote = text[len("公司披露：“"):-1] if text.startswith("公司披露：“") and text.endswith("”") else None
            refs = claim.get("evidence_ids", [])
            valid = bool(quote and refs) and all(i in evidence and evidence[i]["company_id"] == claim["company_id"] for i in refs)
            valid = valid and any(quote in evidence[i]["text"] for i in refs if i in evidence)
            if valid:
                good_quotes += 1
            else:
                issues.append({"code":"UNSUPPORTED_LITERAL_QUOTE","id":claim["claim_id"]})
        if claim.get("kind") in {"DISCLOSED", "INFERENCE"}:
            review_items.append({"item_id": claim["claim_id"], "company_id": claim["company_id"], "kind":claim["kind"],
                "text":claim["text"], "limitations":claim.get("limitations", []),
                "evidence":[evidence[i] for i in claim.get("evidence_ids", []) if i in evidence],
                "decision":"PENDING", "reviewer_type":None})
        if claim.get("kind") == "INFERENCE" and not any("未" in t or "候选" in t for t in [claim.get("text", ""),*claim.get("limitations", [])]):
            issues.append({"code":"UNQUALIFIED_INFERENCE","id":claim["claim_id"]})
    ledger = state.get("supplement", {})
    for batch in state.get("disclosures",[]):
        for index,item in enumerate(batch.get("selected",[])):
            review_items.append({"item_id":f"relation-{batch['company_id']}-{index}","company_id":batch["company_id"],"kind":"MODEL_RELATION",
                "text":item["exact_quote"],"relation":item.get("relation"),"related_hypothesis_categories":item.get("related_hypothesis_categories",[]),
                "evidence":[evidence[item["evidence_id"]]] if item.get("evidence_id") in evidence else [],"decision":"PENDING","reviewer_type":None})
    for gap in ledger.get("gaps", []):
        if gap["gap_type"] == "INDEPENDENT_CONFIRMATION" and gap["status"] == "RESOLVED":
            issues.append({"code":"FALSE_CAUSAL_GAP_CLOSURE","id":gap["gap_id"]})
    return {"observations":{"correct":len(correct),"expected":len(expected),"provided":len(seen),"missing":len(set(expected)-seen)},
        "calculations":{"correct":good_calcs,"expected":sum(map(len,oracle.values())),"provided":provided,"errors":bad_calcs},
        "literal_quotes":{"verified":good_quotes,"total":all_quotes}, "issues":issues,
        "semantic_review":{"status":"PENDING_INDEPENDENT_HUMAN_REVIEW","item_count":len(review_items),"items":review_items},
        "execution_status":state.get("execution_status","UNKNOWN"),
        "supplement":{"rounds":ledger.get("rounds",0),"tool_actions":ledger.get("action_count",0),"stop_reason":ledger.get("stop_reason"),
            "remaining_gaps":sum(g["status"]!="RESOLVED" for g in ledger.get("gaps",[]))}}
