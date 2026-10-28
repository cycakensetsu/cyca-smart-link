"""One numbering scheme for the continuous, beginner-friendly estimate flow."""
STEPS = {
    1: '業者の見積書を入れる',
    2: '元の見積書と品目を確認する',
    3: 'お客様に出す金額を決める',
    4: '完成イメージを確認する',
    5: '見積書を作成して保存する',
}
SHORT = {1:'見積を入れる',2:'内容を確認',3:'金額を決める',4:'完成形を確認',5:'作成・保存'}


def render_step_heading(st, number):
    st.subheader(f'ステップ{number}｜{STEPS[number]}', anchor=f'cyca-step-{number}')


def render_next_action(st, number, label):
    from html import escape
    st.html('<style>.cyca-next-action{display:inline-block;margin:8px 0 20px;padding:14px 22px;'
            'border-radius:10px;background:#164f68;color:white!important;text-decoration:none;font-weight:700;font-size:17px}'
            '.cyca-next-action:hover{background:#24677e}</style>'
            f'<a class="cyca-next-action" href="#cyca-step-{number}">{escape(label)}</a>')


def current_step(state):
    if not state.get('_review_original_rows'):
        return 1
    number = state.get('_review_workflow_step', 2)
    return number if isinstance(number, int) and number in STEPS else 2


def progress_markup(number):
    parts=[]
    for step,label in SHORT.items():
        kind='current' if step==number else ('done' if step<number else 'upcoming')
        selected=' aria-current="step"' if step==number else ''
        content=f'<span>{step}</span>{label}'
        if step<=number:
            content=f'<a href="#cyca-step-{step}" aria-label="ステップ{step}：{STEPS[step]}へ移動">{content}</a>'
        parts.append(f'<li class="cyca-step-{kind}"{selected}>{content}</li>')
    return ('''<style>[id^="cyca-step-"],[id^="cyca-source-"]{scroll-margin-top:84px}.cyca-workflow{margin:8px 0 22px;padding:15px 18px;border:1px solid #d5e4eb;border-radius:13px;background:#f4f8fa;color:#203b50}
.cyca-workflow strong{display:block;font-size:16px;line-height:1.6}.cyca-workflow ol{display:flex;flex-wrap:wrap;list-style:none;gap:8px 12px;margin:12px 0 0;padding:0}
.cyca-workflow li{display:flex;align-items:center;gap:5px;font-size:14px;color:#617786;min-height:44px}.cyca-workflow a{display:flex;align-items:center;gap:5px;min-height:44px;padding:0 6px;color:inherit!important;text-decoration:none}.cyca-workflow a:hover{text-decoration:underline}.cyca-workflow a:focus-visible{outline:3px solid #164f68;outline-offset:2px;border-radius:5px}.cyca-workflow li span{display:inline-grid;place-items:center;width:24px;height:24px;border:1px solid #bfd0dc;border-radius:50%;font-variant-numeric:tabular-nums}
.cyca-workflow li.cyca-step-current{font-weight:700;color:#164f68}.cyca-step-current span{background:#164f68;color:white;border-color:#164f68!important}.cyca-step-done span{background:#e0eef0;color:#275b65}
</style><nav class="cyca-workflow" aria-label="見積書作成の進み具合">'''
            f'<strong>いま：ステップ{number} / 5　{STEPS[number]}</strong>'
            '<ol aria-label="見積書作成の5ステップ">'+''.join(parts)+'</ol></nav>')


def render_workflow_status(st):
    st.html(progress_markup(current_step(st.session_state)))
