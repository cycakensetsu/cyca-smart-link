"""Deterministic, source-preserving review. No AI invents balancing lines."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import hashlib
import json
import math
import os
import re
import unicodedata
from datetime import datetime, timezone

import pandas as pd

from estimate_pipeline import _allocate_group_markup, validate_intermediate

REVIEW_MODE = "承認付き統合（業者別・工種別・全体）"
TRADE_OPTIONS = ("防水工事", "シーリング工事", "足場工事", "塗装工事", "板金工事", "電気工事", "諸経費", "未分類")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d != d.to_integral_value():
            raise ValueError
        return int(d)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("金額は有限の整数円で指定してください。") from None


def yen(value):
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clean(value):
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    if hasattr(value, "item"):
        return value.item()
    return value


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)))


class OriginalVault:
    """Private, content-addressed files; never overwrite originals or receipts.

    CYCA_DATA_DIR must point at durable private storage in a hosted deployment.
    The default directory is local/ephemeral and is explicitly described in UI.
    """
    def __init__(self, root, user):
        if not str(user).strip():
            raise ValueError("ログインが必要です。")
        self.root = Path(root) / hashlib.sha256(str(user).encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def _put(self, relative, data):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if target.read_bytes() != data:
                raise ValueError("保存済み原本との不一致を検出しました。") from None
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        return target

    def preserve(self, name, data):
        data = bytes(data)
        source_id = hashlib.sha256(data).hexdigest()
        self._put(f"originals/{source_id}/original", data)
        record = {"id": source_id, "name": str(name), "size": len(data)}
        self.snapshot("uploads", record)
        return record

    def snapshot(self, kind, value):
        if kind not in ("uploads", "extractions", "approvals"):
            raise ValueError("保存種別が不正です。")
        key = digest(value)
        self._put(f"{kind}/{key}.json", canonical(value).encode())
        return key

    def export(self, approval, kind, data):
        if kind not in ("xlsx", "pdf"):
            raise ValueError("保存形式が不正です。")
        if not re.fullmatch(r"[a-f0-9]{64}", str(approval.get('id', ''))):
            raise ValueError("承認IDが不正です。")
        return self._put(f"exports/{approval['id']}/{hashlib.sha256(data).hexdigest()}.{kind}", data)

    def read(self, source_id):
        if not re.fullmatch(r"[a-f0-9]{64}", source_id):
            raise ValueError("原本IDが不正です。")
        data = (self.root / "originals" / source_id / "original").read_bytes()
        if hashlib.sha256(data).hexdigest() != source_id:
            raise ValueError("原本のハッシュが一致しません。")
        return data

    def entries(self):
        for path in sorted(self.root.rglob('*')):
            if path.is_symlink():
                raise ValueError('保存先に不正なリンクがあります。')
            if path.is_file():
                yield path.relative_to(self.root).as_posix(), path.stat().st_size, path.read_bytes

    def get_entry(self, name):
        from vault_archive import _PATH
        if not _PATH.fullmatch(name):
            raise ValueError('保存先が不正です。')
        path = self.root / name
        cursor = path
        while cursor != self.root.parent:
            if cursor.is_symlink():
                raise ValueError('保存先に不正なリンクがあります。')
            cursor = cursor.parent
        return path.read_bytes() if path.is_file() else None


def suggest_trade(row):
    """A suggestion only. The UI requires explicit classification approval."""
    existing = str(row.get("工事種別", "")).strip()
    if existing:
        return existing
    text = str(row.get("品名", ""))
    for words, trade in [(('諸経費','運搬','福利','処分','養生'), '諸経費'),
                         (('足場',), '足場工事'), (('シーリング',), 'シーリング工事'),
                         (('板金','笠木'), '板金工事'), (('電気','配線'), '電気工事'),
                         (('防水','ウレタン'), '防水工事'), (('塗装',), '塗装工事')]:
        if any(w in text for w in words):
            return trade
    return "未分類"


def review_rows(detail, sources):
    names = [s["name"] for s in sources]
    if len(names) != len(set(names)):
        raise ValueError("同名ファイルがあります。異なる名前で取り込み直してください。")
    mapping = {s["name"]: s["id"] for s in sources}
    source_metadata = {s['name']: s for s in sources}
    result = []
    for i, (_, row) in enumerate(detail.iterrows()):
        r = {str(k): clean(v) for k, v in row.items()}
        original_section = str(r.get("工事種別", "")).strip()
        source = str(r.get("元ファイル", ""))
        if source not in mapping:
            raise ValueError("抽出明細と原本の対応を確認できません。")
        r.update({"行ID": digest([mapping[source], i, r])[:24], "原本ID": mapping[source],
                  "工事種別": suggest_trade(r), "採用": True, "訂正理由": "", "除外理由": ""})
        r["部位"] = str(r.get("部位") or original_section) if str(r.get("部位") or original_section) not in ('', '未分類', '不明') else ''
        r["顧客備考"] = ""
        # Whole-document extraction may omit a page number. Only a measured
        # single-page source makes its location certain without AI inference.
        if r.get('ページ') in ('', None) and source_metadata[source].get('pages') == 1:
            r['ページ'] = 1
            r['ページ根拠'] = '原本ファイルの実測ページ数が1'
        result.append(r)
    return result


@dataclass(frozen=True)
class Target:
    scope: str  # 全体 / 業者別 / 工種別
    key: str
    mode: str  # 上乗せ額 / 掛け率 / 粗利益率
    value: float


def target_total(cost, target):
    try:
        value = Decimal(str(target.value))
        if not value.is_finite():
            raise ValueError
    except (InvalidOperation, ValueError):
        raise ValueError("目標値は有限の数値で指定してください。") from None
    if target.mode == "上乗せ額":
        add = integer(value)
        if add < 0:
            raise ValueError("上乗せ額は0円以上で指定してください。値引きは別欄です。")
        return cost + add
    if cost <= 0:
        raise ValueError("掛け率・粗利益率には正の仕入原価が必要です。")
    if target.mode == "掛け率" and value >= 1:
        return yen(Decimal(cost) * value)
    if target.mode == "粗利益率" and 0 <= value < 100:
        return yen(Decimal(cost) / (1 - value / 100))
    raise ValueError("掛け率は1以上、粗利益率は0%以上100%未満で指定してください。")


def allocate(rows, targets):
    out = pd.DataFrame(rows).copy()
    if out.empty:
        raise ValueError("採用する明細がありません。")
    out["見積単価"] = pd.to_numeric(out["原価単価"]).astype(float)
    out["見積金額"] = pd.to_numeric(out["原価金額"]).astype(float)
    out["上乗せ額"] = 0.0
    if not targets and any(r.get('__source_rounding_approved') for r in rows):
        targets = [Target('全体','全体','上乗せ額',0)]
    covered = set()
    scopes = {t.scope for t in targets}
    if len(scopes) > 1:
        raise ValueError("全体・業者別・工種別は同時に指定できません。二重の上乗せを防ぐため一つ選択してください。")
    for t in targets:
        if t.scope == "全体":
            group = out
        elif t.scope in ("業者別", "工種別"):
            col = "見積元" if t.scope == "業者別" else "工事種別"
            group = out[out[col].astype(str) == str(t.key)]
        else:
            raise ValueError("配分範囲が不正です。")
        if group.empty:
            raise ValueError("目標に対応する明細がありません。")
        if covered.intersection(group.index):
            raise ValueError("同じ明細に複数の目標が指定されています。")
        covered.update(group.index)
        cost = sum(integer(x) for x in group["原価金額"])
        total = target_total(cost, t)
        if group.get('__source_rounding_approved', pd.Series(False,index=group.index)).any():
            allocated = _price_rounded_source(group, total)
        else:
            allocated = _allocate_group_markup(group, total)
        for col in ("見積単価", "見積金額", "上乗せ額"):
            out.loc[allocated.index, col] = allocated[col].astype(float)
    return out


def _price_rounded_source(group, total):
    """Source costs stay immutable; only customer unit prices are calculated.

    Validation has already required human confirmation of the printed facts,
    bounded quantity rounding and an exact final source total. No residual is
    inserted as a fee, discount or extra item.
    """
    from markup_rules import line_amount, quantity_steps, exact_remainder
    pricing=group.copy()
    for idx,row in pricing.iterrows():
        if row.get('__source_rounding_approved'):
            pricing.at[idx,'原価金額']=line_amount(row['数量'],row['原価単価'])
    baseline=sum(integer(v) for v in pricing['原価金額'])
    if total >= baseline:
        result=_allocate_group_markup(pricing,total)
    else:
        difference=baseline-total
        candidates=[]
        for idx,row in pricing.iterrows():
            if str(row['単位']) in ('式','一式') or float(row['原価単価'])<=0 or float(row['原価金額'])<=0:continue
            step,_=quantity_steps(float(row['数量']),False)
            capacity=min(difference,int(float(row['原価金額'])*.01))
            if capacity>=step:candidates.append((idx,step,capacity))
        adjustments=exact_remainder(difference,candidates) if difference<=1000000 else None
        if adjustments is None:raise ValueError('原本の数量表示の丸めにより、この金額では売価単価を確定できません。希望する利益額を指定してください。')
        result=pricing.copy()
        result['見積単価']=result['原価単価'].astype(float)
        result['見積金額']=result['原価金額'].astype(float)
        for idx,amount in adjustments.items():
            rate=round(float(result.at[idx,'原価単価'])-amount/float(result.at[idx,'数量']),2)
            result.at[idx,'見積単価']=rate
            result.at[idx,'見積金額']=line_amount(result.at[idx,'数量'],rate)
        if sum(integer(v) for v in result['見積金額'])!=total:raise ValueError('売価の単価と合計を一致させられませんでした。利益額を変更してください。')
    # Return original purchase facts even though a separate mathematical basis
    # was used to determine customer prices.
    result['原価金額']=group['原価金額']
    result['原価単価']=group['原価単価']
    result['上乗せ額']=result['見積金額']-group['原価金額']
    return result



def section_tax_matches(source):
    """A PDF with several quotes retains each printed tax rounding separately."""
    sections=source.get('amount_reading',{}).get('quote_sections',[])
    if len(sections)<2:return False
    try:
        return (len({integer(s['page']) for s in sections})==len(sections)
            and sum(integer(s['net_total']) for s in sections)==integer(source['net_total'])
            and sum(integer(s['tax']) for s in sections)==integer(source['tax'])
            and all(abs(Decimal(integer(s['tax']))-Decimal(integer(s['net_total']))*Decimal(str(source['tax_rate'])))<=1 for s in sections))
    except (ValueError,KeyError,TypeError):return False

def build_draft(original_rows, edited_rows, sources, targets=(), discount=0, metadata=None, resolutions=None):
    """Return a complete reviewable snapshot. Corrections never change raw data."""
    metadata = dict(metadata or {})
    metadata['出力デザイン'] = '既存横型流し込み（工種別ページ・配置確認版2026-10-20）'
    page_breaks = metadata.get('改ページ', [])
    if not isinstance(page_breaks, list) or len(set(page_breaks)) != len(page_breaks) or any(k not in {r['行ID'] for r in edited_rows if r.get('採用')} for k in page_breaks):
        raise ValueError('改ページする品目を確認してください。')
    requested = metadata.get('希望ページ', {})
    if not isinstance(requested, dict) or any(k not in page_breaks or type(v) is not int or not 2 <= v <= 10 for k, v in requested.items()):
        raise ValueError('希望するページ番号を確認してください。')
    resolutions = dict(resolutions or {})
    issues = []
    def issue(code, rows, message, level="確認"):
        key = digest([code, sorted(rows), message])[:20]
        issues.append({"id": key, "種類": code, "レベル": level, "行": list(rows), "内容": message})
    raw = {r["行ID"]: r for r in original_rows}
    if len(raw) != len(original_rows) or len(edited_rows) != len(raw) or {r["行ID"] for r in edited_rows} != set(raw):
        raise ValueError("原本にない行の追加、行の削除、行IDの変更はできません。")
    source_map = {s["id"]: s for s in sources}
    if len(source_map) != len(sources):
        issue("同一原本", [], "同じ原本が複数回取り込まれています。", "停止")
    allowed = {"採用", "訂正理由", "除外理由", "工事種別", "品名", "仕様", "数量", "単位", "原価単価", "原価金額", "見積元", "集計のみ", "部位", "顧客備考", "ページ"}
    financial = {"品名", "仕様", "数量", "単位", "原価単価", "原価金額", "見積元", "部位", "ページ"}
    accepted = []
    for row in edited_rows:
        before = raw[row["行ID"]]
        if any(row.get(k) != v for k, v in before.items() if k not in allowed):
            raise ValueError("原本の参照情報は変更できません。")
        if any(row.get(k) != before.get(k) for k in financial) and not str(row.get("訂正理由", "")).strip():
            issue("訂正理由", [row["行ID"]], "原文の訂正には原本を確認した理由が必要です。", "停止")
        if not row.get("採用", True):
            if not str(row.get("除外理由", "")).strip():
                issue("除外理由", [row["行ID"]], "除外する行の理由を記録してください。", "停止")
            continue
        if str(row.get("工事種別", "")).strip() in ("", "未分類"):
            issue("工種未分類", [row["行ID"]], "工種を確認してください。", "停止")
        if str(row.get("見積元", "")).strip().lower() in ("", "不明", "unknown", "不明な会社"):
            issue("業者不明", [row["行ID"]], "発行会社を確認してください。", "停止")
        try:
            page = integer(row.get("ページ"))
            maximum = source_map.get(row.get("原本ID"), {}).get("pages")
            valid_page = page >= 1 and (maximum is None or page <= maximum)
        except ValueError:
            valid_page = False
        if row.get("原本ID") not in source_map or not valid_page:
            issue("原本参照", [row["行ID"]], "原本とページの対応が必要です。", "停止")
        accepted.append(dict(row))
    for s in sources:
        source_rows=[r for r in edited_rows if r['原本ID']==s['id']]
        if s.get('reference_only'):
            if source_rows or not s.get('reference_confirmed') or not s.get('confirmed'):
                issue('参考資料の確認', [], '品目のある原本は参考資料として除外できません。原本の内容を確認してください。', '停止')
            else:
                continue
        if s.get('amount_reading',{}).get('amount_notes') and not str(s.get('amount_notes_resolution') or '').strip():
            issue('原本金額の選択', [], 'NET金額・税込後の値引きがあります。原本の記載と採用する金額を確認してください。', '停止')
        if s.get("read_errors"):
            issue("読取未完了", [], "解析できなかった原本・ページがあります。再解析または原本の再確認が必要です。", "停止")
        if s.get("tax_basis") not in ("税抜", "非課税") or s.get("tax_rate") not in (0, 0.08, 0.10):
            issue("税区分", [], "原本ごとに税抜・非課税と税率を確認してください。税込・不明の原価は集計できません。", "停止")
        if s.get("tax_basis") == "非課税" and s.get("tax_rate") != 0:
            issue("税区分", [], "非課税の原本に税率が設定されています。", "停止")
        if not s.get("confirmed"):
            issue("原本確認", [], "全原本の税区分と明細を確認してください。", "停止")
        all_rows = [r for r in edited_rows if r["原本ID"] == s["id"]]
        if not all_rows:
            issue("原本明細なし", [], "明細がない原本が含まれています。取込漏れを確認してください。", "停止")
        if s.get("net_total") is None:
            issue("原本小計", [], "原本の税抜小計を入力してください。", "停止")
        else:
            try:
                actual = sum(integer(r["原価金額"]) for r in all_rows)
                if actual != integer(s["net_total"]):
                    issue("原本小計", [r["行ID"] for r in all_rows], "訂正後の全原本明細と原本の税抜小計が一致しません。", "停止")
            except ValueError:
                issue("数値不正", [], "原本小計・明細金額を整数円で確認してください。", "停止")
        if s.get("tax") is not None and s.get("gross_total") is not None and s.get("net_total") is not None:
            if integer(s["net_total"]) + integer(s["tax"]) != integer(s["gross_total"]):
                issue("税合計", [], "原本の税抜小計＋税額と税込総額が一致しません。", "停止")
            if s.get("tax_rate") in (0, 0.08, 0.10) and abs(integer(s["tax"]) - integer(s["net_total"]) * s["tax_rate"]) > 1 and not section_tax_matches(s):
                issue("税率不一致", [], "原本の税額と選択税率が一致しません。", "停止")
        elif s.get('gross_total') is not None and s.get('net_total') is not None and s.get('tax_rate') in (0,.08,.10):
            expected_tax = int((Decimal(str(s['net_total'])) * Decimal(str(s['tax_rate']))).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            if integer(s['net_total']) + expected_tax != integer(s['gross_total']):
                issue('税合計', [], '原本の合計と、工事金額に消費税を足した金額が一致しません。', '停止')
    rates = {s.get("tax_rate") for s in sources if not s.get("reference_only")}
    if len(rates) > 1:
        issue("税率混在", [], "仕入の税率が混在しています。顧客向け税率との対応を確認してください。")
    try:
        frame = pd.DataFrame(accepted)
        if frame.empty:
            raise ValueError("採用する明細がありません。")
        for col in ("数量", "原価単価", "原価金額"):
            frame[col] = pd.to_numeric(frame[col], errors="raise")
            if not frame[col].map(lambda x: math.isfinite(float(x))).all():
                raise ValueError("有限の数値が必要です。")
        for value in frame["原価金額"]:
            integer(value)
        from source_rounding import printed_rounding
        for idx,row in frame.iterrows():
            before=raw[row['行ID']]
            source=source_map[row['原本ID']]
            unchanged=all(row.get(k)==before.get(k) for k in ('数量','単位','原価単価','原価金額','抽出元テキスト範囲'))
            allowed_rounding=(unchanged and source.get('confirmed') and row['行ID'] in source.get('rounding_rows',[])
                              and printed_rounding(before) is not None)
            frame.at[idx,'__source_rounding_approved']=bool(allowed_rounding)
            accepted[idx]['__source_rounding_approved']=bool(allowed_rounding)
            for check in validate_intermediate(frame.loc[[idx]], {}):
                if allowed_rounding and '単価×数量' in check['内容']:continue
                issue("明細検算", [row['行ID']], check["内容"], "停止")
        allocated = allocate(accepted, targets) if not any(i["レベル"] == "停止" for i in issues) else frame
    except (ValueError, TypeError, KeyError) as exc:
        issue("配分・数値", [], str(exc), "停止")
        allocated = pd.DataFrame(accepted)
    # Compare facts, never delete a suspected duplicate or silently unify prices.
    for i, left in enumerate(accepted):
        for right in accepted[i+1:]:
            ids = [left["行ID"], right["行ID"]]
            different = left["原本ID"] != right["原本ID"] or left["見積元"] != right["見積元"]
            if all(compact(left.get(k, "")) == compact(right.get(k, "")) for k in ("品名", "仕様", "数量", "単位", "原価単価", "原価金額")):
                issue("明細重複疑い", ids, "別原本に同じ品名・仕様・数量・単価・金額があります。施工範囲を確認してください。")
            for word in ("足場", "運搬", "諸経費"):
                if different and word in str(left.get("品名")) and word in str(right.get("品名")):
                    issue("共通費重複疑い", ids, f"複数原本に{word}があります。範囲・負担区分を確認してください。")
    priced = [{k: clean(v) for k, v in r.items()} for r in allocated.to_dict("records")]
    for i, left in enumerate(priced):
        for right in priced[i+1:]:
            same = all(compact(left.get(k, "")) == compact(right.get(k, "")) for k in ("品名", "仕様", "単位"))
            if same and left.get("見積単価") != right.get("見積単価"):
                issue("売価単価差", [left["行ID"], right["行ID"]], "同じ品名・仕様・単位で顧客向け単価が異なります。適用範囲を確認してください。")
    discount = integer(discount)
    subtotal = sum(integer(r.get("見積金額", r["原価金額"])) for r in priced)
    if discount > 0 or subtotal + discount < 0:
        issue("値引き", [], "顧客値引きは0円以下、税抜合計が0円以上になる額で指定してください。", "停止")
    if discount and not str(metadata.get("値引き理由", "")).strip():
        issue("値引き理由", [], "顧客値引きの理由を入力してください。", "停止")
    rate = metadata.get("顧客税率", 0.10)
    if rate not in (0, 0.08, 0.10):
        issue("顧客税率", [], "顧客税率を確認してください。", "停止")
        rate = 0
    net = subtotal + discount
    tax = int(Decimal(net) * Decimal(str(rate)))
    cost = sum(integer(r["原価金額"]) for r in priced)
    totals = {"仕入原価": cost, "売価小計": subtotal, "顧客値引き": discount,
              "税抜売価": net, "税額": tax, "税込売価": net + tax,
              "粗利益": net - cost, "粗利益率": (net - cost) / net if net else None}
    payload = {"原本明細": original_rows, "確認明細": edited_rows, "計算明細": priced, "原本": sources,
               "目標": [t.__dict__ for t in targets], "値引き": discount, "表題": metadata,
               "指摘": issues, "確認記録": resolutions, "合計": totals, "schema": 1}
    return {"fingerprint": digest(payload), "payload": payload,
            "unresolved": [i for i in issues if i["レベル"] == "停止" or not str(resolutions.get(i["id"], "")).strip()]}


def approve(draft, actor, approved_at=None):
    if draft["unresolved"]:
        raise ValueError("未確認または停止中の指摘があります。")
    if not str(actor).strip():
        raise ValueError("承認者が必要です。")
    record = {"fingerprint": draft["fingerprint"], "actor": str(actor), "payload": draft["payload"],
              "approved_at": approved_at or datetime.now(timezone.utc).isoformat()}
    return {"id": digest(record), **record}


def verify_approval(draft, approval):
    if not approval or draft["unresolved"] or approval.get("fingerprint") != draft["fingerprint"]:
        raise ValueError("内容が変更されています。最新の内容を承認してください。")
    expected = approve(draft, approval.get("actor", ""), approval.get("approved_at"))
    if expected != approval:
        raise ValueError("承認記録の整合性を確認できません。")


def build_approved_estimate(draft, approval):
    verify_approval(draft, approval)
    from template_fill_test_v2 import fill_estimate_v2
    return fill_estimate_v2(estimate_for_output(draft), save_to_disk=False)


def _customer_source_project(source, rows):
    """Use a short work title only when it cannot identify the source issuer."""
    import unicodedata
    title = str(source.get('project_name') or '').strip()
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', title).casefold())
    if (not title or len(title) > 24 or re.search(r'株式会社|有限会社|合同会社|会社|御中|見積書|\(株\)|\(有\)|\.(?:pdf|png|jpe?g)', normalized)):
        return ''
    vendors = [source.get('vendor_name')] + [r.get('見積元') for r in rows if r.get('原本ID') == source['id']]
    for vendor in vendors:
        name = re.sub(r'\s+|株式会社|有限会社|合同会社|\(株\)|\(有\)', '', unicodedata.normalize('NFKC', str(vendor or '')).casefold())
        if name and name not in ('不明', 'unknown', '業者名の記載なし') and name in normalized:
            return ''
    return title


def estimate_for_output(draft):
    from template_fill_test_v2 import Category, Estimate, LineItem
    p = draft["payload"]
    categories = []
    source_projects = {s['id']: _customer_source_project(s, p['計算明細']) for s in p['原本']}
    for r in p["計算明細"]:
        item = LineItem(name=r["品名"], spec=r.get("仕様", ""), qty=float(r["数量"]), unit=r["単位"],
                        unit_price=float(r["見積単価"]), amount=integer(r["見積金額"]),
                        remark=str(r.get("顧客備考", "")), section=r.get("部位", ""), row_id=r['行ID'],
                        source_id=r.get('原本ID', ''), source_project=source_projects.get(r.get('原本ID'), ''))
        summary_only = bool(r.get("集計のみ", False))
        if summary_only:
            categories.append(Category(item.name, [item], summary_only=True))
        else:
            category = next((c for c in categories if c.name == r["工事種別"] and not c.summary_only), None)
            if category is None:
                category = Category(r["工事種別"], [])
                categories.append(category)
            category.items.append(item)
    m = p["表題"]
    if not str(m.get("宛名", "")).strip() or not str(m.get("工事名称", "")).strip():
        raise ValueError("宛名と工事名称を入力してください。")
    estimate = Estimate("", m["宛名"], m["工事名称"], m.get("見積日", ""), m.get("有効期限", "30日"),
                        categories, p["値引き"], m.get("顧客税率", .10), reference_layout=False,
                        pour_layout=True, source_count=sum(not s.get('reference_only') for s in p['原本']), page_breaks=tuple(m.get('改ページ', [])),
                        requested_pages=m.get('希望ページ', {}))
    return estimate


def preview_allowed(draft):
    # A suspected duplicate is a review choice, not a reason to hide the draft.
    return all(i['種類'] in ('明細重複疑い','共通費重複疑い') and i['レベル']!='停止'
               for i in draft['unresolved'])


def build_preview(draft):
    if not preview_allowed(draft):
        raise ValueError('原本・金額・確認事項を先に確認してください。')
    from template_fill_test_v2 import fill_estimate_v2
    result = fill_estimate_v2(estimate_for_output(draft), save_to_disk=False)
    return result
