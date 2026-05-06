"""Google SSO via Supabase Auth — restricted to @motiv-i.com (default).

Mirrors the casestudy_dashboard pattern: OAuth code exchange happens on
redirect, session_state is the source of truth for user_email /
access_token. Domain-restricted via the `ALLOWED_DOMAIN` secret.

Public surface: `require_auth()` is the gate — call it once after
`st.set_page_config(...)`. It either returns the authenticated email
or calls `st.stop()` to halt the script and render the login screen.
"""
from __future__ import annotations

import streamlit as st

from config import load_settings
from data.supabase_client import get_client


def is_email_allowed(email: str) -> bool:
    s = load_settings()
    if not email:
        return False
    return email.lower().endswith(f"@{s.allowed_domain.lower()}")


def handle_oauth_callback():
    """Exchange the OAuth `code` query-param for a session, then cache it."""
    code = st.query_params.get("code")
    if not code or st.session_state.get("auth_handled"):
        return
    client = get_client()
    if not client:
        return
    try:
        client.auth.exchange_code_for_session({"auth_code": code})
        session = client.auth.get_session()
        if session and session.user:
            st.session_state.access_token = session.access_token
            st.session_state.refresh_token = session.refresh_token
            st.session_state.user_email = session.user.email
            st.session_state.auth_handled = True
    except Exception as e:
        st.session_state.auth_error = f"로그인 처리 실패: {e}"
    finally:
        st.query_params.clear()
        st.rerun()


def render_login_screen():
    s = load_settings()
    st.title("📄 Case Study Report Builder")
    st.markdown("---")

    err = st.session_state.pop("auth_error", None)
    if err:
        st.error(err)

    client = get_client()
    if not client:
        st.error("❌ Supabase 연결 실패. SUPABASE_URL/KEY 확인 필요.")
        return

    try:
        res = client.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": s.app_url,
                "query_params": {"hd": s.allowed_domain},
            },
        })
        oauth_url = getattr(res, "url", None)
    except Exception as e:
        st.error(f"로그인 URL 생성 실패: {e}")
        return

    if not oauth_url:
        st.error("로그인 URL을 가져오지 못했습니다.")
        return

    st.markdown(
        f"""
        <div style="display:flex; flex-direction:column; align-items:center; padding: 4rem 1rem;">
            <div style="font-size: 3rem; margin-bottom: 0.5rem;">🔐</div>
            <h3 style="margin: 0 0 0.5rem 0;">접근 권한 확인이 필요합니다</h3>
            <p style="opacity: 0.7; max-width: 480px; text-align: center; line-height: 1.6;">
                케이스 스터디 리포트 빌더는 <strong>@{s.allowed_domain}</strong> 도메인의 Google 계정을 가진 사용자만 접근할 수 있습니다.
            </p>
            <a href="{oauth_url}" target="_self" style="
                display: inline-flex;
                align-items: center;
                gap: 10px;
                padding: 12px 28px;
                background: #ffffff;
                color: #1f2937 !important;
                text-decoration: none !important;
                border-radius: 10px;
                font-weight: 600;
                margin-top: 1.5rem;
                border: 1px solid #dadce0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.08);
            ">
                <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/><path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"/><path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"/><path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571.001-.001.002-.001.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/></svg>
                Google 계정으로 로그인
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def logout():
    """Clear all session state and sign out of Supabase."""
    client = get_client()
    try:
        if client:
            client.auth.sign_out()
    except Exception:
        pass
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()


def require_auth() -> str:
    """Gate the app. Returns the authenticated email or `st.stop()`s."""
    handle_oauth_callback()

    user_email = st.session_state.get("user_email")
    if not user_email:
        render_login_screen()
        st.stop()

    if not is_email_allowed(user_email):
        s = load_settings()
        st.error(
            f"⚠️ 접근 권한이 없습니다. @{s.allowed_domain} 도메인 계정으로만 "
            "로그인할 수 있습니다."
        )
        st.caption(f"현재 로그인: {user_email}")
        if st.button("🚪 다른 계정으로 로그인"):
            logout()
        st.stop()

    return user_email
