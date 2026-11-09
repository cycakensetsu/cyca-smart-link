"""Opt-in customer unit-price settings; no source facts are changed."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
from markup_rules import fixed_price, line_amount


def welfare(row):
    return any(word in str(row.get('品名', '')) for word in ('法定福利', '法定厚生'))


def adjustable(row):
    try:
        quantity, amount, rate = float(row['数量']), float(row['原価金額']), float(row['原価単価'])
        return (not fixed_price(row.get('単価固定', False))
                and math.isfinite(quantity) and math.isfinite(amount) and math.isfinite(rate) and rate >= 0
                and quantity > 0 and amount > 0 and not row.get('__source_rounding_approved'))
    except (ValueError, TypeError, KeyError):
        return False


def validate_settings(settings, rows):
    if settings is None:
        return None
    if not isinstance(settings, dict) or set(settings) != {'step', 'rates', 'limit', 'welfare_reason'}:
        raise ValueError('単価案の設定を確認してください。')
    if type(settings['step']) is not int or settings['step'] not in (10, 50, 100):
        raise ValueError('単価の刻みは10円・50円・100円から選んでください。')
    if type(settings['limit']) is not int or not 0 <= settings['limit'] <= 1000000:
        raise ValueError('調整値引きの上限は0円から100万円で指定してください。')
    rates = settings['rates']
    if not isinstance(rates, dict) or not isinstance(settings['welfare_reason'], str):
        raise ValueError('提案単価と法定福利費の確認内容を確認してください。')
    by_id = {r['行ID']: r for r in rows}
    for key, value in rates.items():
        if key not in by_id or not adjustable(by_id[key]):
            raise ValueError('固定単価・原本丸め・負数行・対象外の行は変更できません。')
        try:
            rate = Decimal(str(value))
            if not rate.is_finite() or not 0 <= rate <= 1000000000000 or rate != rate.to_integral_value():
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError('提案単価は0円以上1兆円以下の整数で入力してください。') from None
        if not welfare(by_id[key]) and rate < Decimal(str(by_id[key]['原価単価'])):
            raise ValueError('通常明細の提案単価は原価単価以上で指定してください。')
        if welfare(by_id[key]) and not settings['welfare_reason'].strip():
            raise ValueError('法定福利費を変える場合は、根拠を確認した内容と理由を入力してください。')
    return settings


def custom_candidate(rows, targets, base, settings):
    """The whole-estimate adjustment is never redistributed across named scopes."""
    from estimate_review import allocate
    validate_settings(settings, rows)
    if len(targets) != 1 or targets[0].scope != '全体' or targets[0].mode != '上乗せ額':
        raise ValueError('個別の丸い単価案は「全体・上乗せ額」で指定してください。')
    if targets[0].value <= 0:
        raise ValueError('個別の単価案には正の上乗せ額を指定してください。0円の原価出力は変更しません。')
    candidate = [dict(r) for r in (base or allocate(rows, []).to_dict('records'))]
    for original, row in zip(rows, candidate):
        if welfare(original):
            # Welfare never receives an automatic profit-balancing adjustment.
            row.update(見積単価=original['原価単価'], 見積金額=original['原価金額'], 上乗せ額=0)
        if not adjustable(original):
            continue
        key = original['行ID']
        if key in settings['rates']:
            rate = settings['rates'][key]
        elif welfare(original) or str(original['単位']).strip() in ('式', '一式'):
            continue
        else:
            tick = settings['step']
            rate = float((Decimal(str(row['見積単価'])) / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
        amount = line_amount(original['数量'], rate)
        row.update(見積単価=float(rate), 見積金額=amount, 上乗せ額=amount-int(original['原価金額']))
    # Keeping welfare at its source amount can lower the previous allocation.
    # Fill that gap only through unpinned, measured work, using the chosen tick.
    # No source figure or explicit proposed rate is changed to hit the target.
    from estimate_review import target_total
    goal = target_total(sum(int(r['原価金額']) for r in rows), targets[0])
    gap = goal - sum(int(r['見積金額']) for r in candidate)
    if gap > 0:
        available = [(source, row) for source, row in zip(rows, candidate)
                     if adjustable(source) and not welfare(source)
                     and source['行ID'] not in settings['rates']
                     and str(source['単位']).strip() not in ('式', '一式')]
        weight = sum(Decimal(str(source['原価金額'])) for source, _ in available)
        if weight:
            tick = Decimal(settings['step'])
            for source, row in available:
                share = Decimal(gap) * Decimal(str(source['原価金額'])) / weight
                rate = ((Decimal(str(row['見積金額'])) + share) / Decimal(str(source['数量'])) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
                if rate > 1000000000000:
                    raise ValueError('調整単価が大きすぎます。指定利益・数量を確認してください。')
                amount = line_amount(source['数量'], rate)
                row.update(見積単価=float(rate), 見積金額=amount, 上乗せ額=amount-int(source['原価金額']))
    return candidate


def price_flags(source_rows, priced_rows):
    """Conservative display checks, not a claim about market or legal rates."""
    flags = []
    if not any(float(p['見積金額']) > float(s['原価金額']) for s, p in zip(source_rows, priced_rows)):
        return flags
    for source, priced in zip(source_rows, priced_rows):
        if not adjustable(source):
            continue
        rate = Decimal(str(priced['見積単価']))
        if welfare(source):
            if str(source['単位']).strip() in ('式', '一式') and rate % 1000:
                flags.append((source['行ID'], f"{source['品名']}が{rate:,.0f}円です。算定根拠のある端数か、丸い金額へ調整するか確認してください。"))
        elif (str(source['単位']).strip() not in ('式', '一式')
              and priced['見積金額'] > source['原価金額']
              and (rate < 100 or rate % (50 if rate < 1000 else 100))):
            flags.append((source['行ID'], f"{source['品名']}の単価{rate:,.2f}円を確認してください。刻みを整える案と、そのままの案を比較できます。"))
    return flags


def render_settings(st, rows, targets, digest):
    accepted = [r for r in rows if r.get('採用', True)]
    flags = []
    try:
        from estimate_review import allocate
        baseline = allocate(accepted, targets).to_dict('records')
        if any(t.value > 0 for t in targets):
            flags = price_flags(accepted, baseline)
    except (ValueError, TypeError, KeyError):
        baseline = []
    if flags:
        st.warning('単価や法定福利費に確認したい端数があります。調整して値引きするか、そのまま作るか選んでください。')
        for _, message in flags:
            st.caption(message)
        for floor in (150, 200):
            if any(float(r['見積単価']) < 100 and not welfare(r) for r in baseline):
                if st.button(f'低額単価を{floor}円に整える案を見る', key=f'review_round_quick_{floor}'):
                    st.session_state['review_round_enabled'] = True
                    st.session_state['review_round_low_floor'] = floor
    with st.expander('単価を個別に整える（比較案のみ）', expanded=bool(flags)):
        if not st.checkbox('丸い単価の比較案を作る', key='review_round_enabled'):
            return None
        st.caption('指定利益まで不足する場合は、単価未指定の実数量工事へ丸い単価で配分します。単価倍率上限を超える案も比較できます。適用は後の「この値引き案を採用」で行います。法定福利費は自動で増額しません。25,000円・30,000円などへ変える場合も、算定根拠を確認してください。')
        step = st.selectbox('通常明細の単価刻み（円）', [50, 100, 10], key='review_round_step')
        limit = st.number_input('差額値引きの上限（円）', min_value=0, max_value=1000000, value=100000, step=1000, key='review_round_limit')
        accepted = [r for r in rows if r.get('採用', True)]
        context = digest([accepted, [t.__dict__ for t in targets]])
        floor = st.selectbox('100円未満の単価の比較候補', [0, 100, 150, 200],
                             format_func=lambda value: '指定しない' if not value else f'{value}円',
                             key='review_round_low_floor')
        by_id = {r['行ID']: r for r in baseline}
        rates = {}
        for row in accepted:
            if not adjustable(row):
                continue
            key = digest([context, row['行ID']])
            current_rate = float(by_id.get(row['行ID'], row).get('見積単価', row['原価単価']))
            if (floor and current_rate < 100 and not welfare(row)
                    and str(row['単位']).strip() not in ('式', '一式')):
                rates[row['行ID']] = max(floor, float(row['原価単価']))
            if welfare(row):
                amount = current_rate
                lower = int(amount // 5000) * 5000
                st.caption(f"{row['品名']}：候補 {lower:,}円 / {lower+5000:,}円。算定根拠に合う金額を選んで指定してください。")
            elif float(row['原価単価']) < 100:
                st.caption(f"{row['品名']}：100円・150円・200円などを比較できます。相場の断定ではありません。")
            if st.checkbox(f"{row['品名']}（{row.get('仕様','')}）の単価を指定", key='review_round_override_'+key):
                rates[row['行ID']] = st.number_input(
                    f"{row['品名']}：提案単価（数量{row['数量']}{row['単位']}・原価単価{row['原価単価']}円）",
                    min_value=0, value=int(row['原価単価']), step=step, key='review_round_rate_'+key)
        reason = st.text_input('法定福利費の根拠確認・変更理由（変更する場合）', key='review_round_welfare_reason')
        return {'step': step, 'rates': rates, 'limit': limit, 'welfare_reason': reason}
