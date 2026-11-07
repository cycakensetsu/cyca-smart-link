"""Customer price alternatives. A proposal is inert until selected by the user.

No network, source edits or inferred costs. Receipts bind the complete pricing
context and the calculated proposal, so an old choice cannot follow new inputs.
"""
from decimal import Decimal, ROUND_CEILING

from markup_rules import fixed_price, line_amount, measured_add_cap


def input_context(rows, targets, tax_rate, requested_discount):
    from estimate_review import clean, digest, integer
    facts = [{k:clean(v) for k,v in r.items() if k != '__source_rounding_approved'} for r in rows]
    return digest([facts,[t.__dict__ for t in targets],tax_rate,integer(requested_discount)])


def totals(rows, discount, tax_rate):
    cost = sum(int(r['原価金額']) for r in rows)
    subtotal = sum(int(r['見積金額']) for r in rows)
    net = subtotal + discount
    tax = int(Decimal(net) * Decimal(str(tax_rate)))
    return {'仕入原価': cost, '調整前上乗せ': subtotal-cost, '顧客値引き': discount,
            '最終利益': net-cost, '税抜売価': net, '税額': tax, '税込売価': net+tax}


def _groups(rows, targets):
    from estimate_review import integer, target_total
    groups = []
    used = set()
    if len({t.scope for t in targets}) > 1:
        raise ValueError('利益の指定範囲は一つ選んでください。')
    for target in targets:
        if target.scope not in ('全体', '業者別', '工種別'):
            raise ValueError('配分範囲が不正です。')
        column = '見積元' if target.scope == '業者別' else '工事種別'
        indices = [i for i,r in enumerate(rows) if target.scope == '全体' or str(r[column]) == str(target.key)]
        if not indices or used.intersection(indices):
            raise ValueError('目標が重複しているか、対応する明細がありません。')
        used.update(indices)
        cost = sum(integer(rows[i]['原価金額']) for i in indices)
        groups.append((target, indices, cost, target_total(cost, target)-cost))
    return groups


def _extra_targets(groups, extra):
    from estimate_review import Target
    weights = [max(0, cost) for _,_,cost,_ in groups]
    total = sum(weights)
    if not total:
        raise ValueError('調整案には正の原価と利益指定が必要です。')
    shares = [extra*w//total for w in weights]
    for i in sorted(range(len(groups)), key=lambda i: (extra*weights[i]%total, -i), reverse=True)[:extra-sum(shares)]:
        shares[i] += 1
    return [Target(t.scope,t.key,'上乗せ額',profit+share,t.allow_low_price_cap_override)
            for (t,_,_,profit),share in zip(groups,shares)]


def build_options(rows, targets=(), tax_rate=.10, requested_discount=0):
    from estimate_review import allocate, clean, digest, integer
    rows = [{k:clean(v) for k,v in r.items()} for r in rows]
    requested_discount = integer(requested_discount)
    if requested_discount > 0:
        raise ValueError('値引きの提案額は0円以下で指定してください。')
    if tax_rate not in (0, .08, .10):
        raise ValueError('顧客税率を確認してください。')
    groups = _groups(rows, targets)
    context = input_context(rows, targets, tax_rate, requested_discount)
    desired_profit = sum(g[3] for g in groups)
    # Keep an alternative a small rounding adjustment, never a disguised large
    # markup/discount. Larger business decisions need a different explicit goal.
    limit = min(50000, max(1000, desired_profit//10))
    try:
        base = allocate(rows, targets).to_dict('records')
        base_error = ''
    except ValueError as exc:
        base, base_error = None, str(exc)
    proposal = None
    proposal_error = ''
    if desired_profit > 0:
        try:
            if requested_discount:
                if -requested_discount > limit:
                    raise ValueError(f'値引き案が端数調整の目安（{limit:,}円以内）を超えます。指定利益を変えず、調整額を見直してください。')
                candidate = allocate(rows, _extra_targets(groups, -requested_discount)).to_dict('records')
            elif base:
                candidate = [dict(r) for r in base]
                for target, indices, cost, profit in groups:
                    positive = sum(max(0,float(rows[i]['原価金額'])) for i in indices)
                    for i in indices:
                        r = candidate[i]
                        if (fixed_price(r.get('単価固定',False)) or str(r['単位']).strip() in ('式','一式')
                                or r['上乗せ額'] <= 0 or r.get('__source_rounding_approved')):
                            continue
                        rate = Decimal(str(r['見積単価']))
                        tick = Decimal(10 if rate < 1000 else 100 if rate < 50000 else 1000)
                        rounded = float((rate/tick).to_integral_value(rounding=ROUND_CEILING)*tick)
                        amount = line_amount(r['数量'], rounded)
                        cap = measured_add_cap(float(r['原価金額']), profit/positive if positive else 0,
                                               target.allow_low_price_cap_override)
                        if cap is not None and amount-int(r['原価金額']) > cap:
                            continue
                        r.update(見積単価=rounded, 見積金額=amount, 上乗せ額=amount-int(r['原価金額']))
            else:
                candidate = None
                # A bounded search can offer an explicitly discounted attainable
                # amount when the exact goal is outside the quantity price grid.
                for extra in sorted({1,10,100,500,1000,5000,10000,limit}):
                    if extra > limit:
                        continue
                    try:
                        candidate = allocate(rows, _extra_targets(groups, extra)).to_dict('records')
                        break
                    except ValueError:
                        continue
                if candidate is None:
                    raise ValueError('原価・数量・固定単価を守れる端数調整案がありません。条件の確認が必要です。')
            desired_net = sum(int(r['原価金額']) for r in rows)+desired_profit
            discount = desired_net-sum(int(r['見積金額']) for r in candidate)
            if -limit <= discount < 0:
                proposal = {'rows':candidate, 'totals':totals(candidate, discount, tax_rate)}
                proposal['id'] = digest([context, proposal])
            elif discount < -limit:
                proposal_error = f'単価を丸めると調整額が{-discount:,}円となり、端数調整の目安{limit:,}円を超えるため提案していません。'
        except ValueError as exc:
            proposal_error = str(exc)
    return {'context_id':context, 'no_discount':None if base is None else {'rows':base,'totals':totals(base,0,tax_rate)},
            'no_discount_error':base_error, 'proposal':proposal, 'proposal_error':proposal_error,
            'desired_profit':desired_profit, 'adjustment_limit':limit}


def choice_for(options, adopt):
    if adopt and not options['proposal']:
        raise ValueError('採用できる値引き案がありません。')
    return {'mode':'discount_adjustment' if adopt else 'none', 'context_id':options['context_id'],
            'proposal_id':options['proposal']['id'] if adopt else ''}


def selected(options, choice=None):
    if choice is not None and (not valid_choice(choice) or choice['context_id'] != options['context_id']):
        raise ValueError('金額条件が変わりました。値引き案を選び直してください。')
    if choice and choice['mode'] == 'discount_adjustment':
        proposal = options['proposal']
        if not proposal or choice['proposal_id'] != proposal['id']:
            raise ValueError('値引き案が変わりました。内容を確認して選び直してください。')
        return proposal
    if not options['no_discount']:
        raise ValueError(options['no_discount_error'])
    return options['no_discount']


def valid_choice(choice):
    import re
    return (isinstance(choice,dict) and set(choice)=={'mode','context_id','proposal_id'}
            and choice['mode'] in ('none','discount_adjustment')
            and isinstance(choice['context_id'],str) and re.fullmatch('[a-f0-9]{64}',choice['context_id']) is not None
            and isinstance(choice['proposal_id'],str)
            and (choice['proposal_id']=='' if choice['mode']=='none'
                 else re.fullmatch('[a-f0-9]{64}',choice['proposal_id']) is not None))
