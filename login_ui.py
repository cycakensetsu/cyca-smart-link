"""Submit login inputs together; never log or persist authentication inputs."""
import time

from app_auth import check_login, configuration_signature


def render_login(st, users, authenticate=None):
    authenticate = authenticate or check_login
    with st.form('cyca_login', clear_on_submit=False):
        username = st.text_input('ユーザー名', key='_login_user',
                                 placeholder='ユーザー名を入力', autocomplete='username')
        password = st.text_input('パスワード', key='_login_password', type='password',
                                 placeholder='保存済みのパスワードを選ぶか入力',
                                 autocomplete='current-password')
        submitted = st.form_submit_button('ログイン', type='primary')
    if not submitted:
        return False
    # A missing/autofill-not-applied input is not a failed authentication
    # attempt. Both inputs are sent in one form submission before verification.
    if not username or not password:
        st.warning('ユーザー名とパスワードの両方を入力してから、ログインを押してください。')
        return False
    if not authenticate(username, password, users):
        st.error('ユーザー名またはパスワードが正しくありません。')
        st.caption('保存済みの候補が入力欄に反映されているか確認してください。続けて押さず、入力を確認してからお試しください。')
        return False
    st.session_state.update(logged_in=True, username=username,
                            _auth_signature=configuration_signature(users),
                            _auth_started=time.time(), _login_clear_password=True)
    st.rerun()


def clear_login_password(st):
    # On the next run the login widgets are absent, so their secret can be
    # removed without mutating an already-created widget.
    if st.session_state.pop('_login_clear_password', False):
        st.session_state.pop('_login_password', None)
