"""`app/services/ad_auth.py` 單元測試（mock ldap3，不需真實 AD）：
authenticate() / get_user_info() / is_admin() / parse_ntlm_username() / AD_MOCK。"""

import pytest
from ldap3.core.exceptions import LDAPSocketOpenError

from app.config import get_settings
from app.services import ad_auth
from tests.ad_auth.conftest import FakeEntry, make_ntlm_type3_header


def _entry(**attrs) -> FakeEntry:
    return FakeEntry(**attrs)


# -- authenticate()：SIMPLE bind（NETBIOS\samaccount 格式） -------------------------


def test_authenticate_success_binds_netbios_sam_format(enable_ad, fake_ldap):
    """bind 身分必須是 `NETBIOS\\samaccount`（skill 實測驗證成功的格式）。"""
    fake_ldap.add_user(
        sam="jdoe",
        password="secret123",
        entry=_entry(
            sAMAccountName="jdoe",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    result = ad_auth.authenticate("jdoe", "secret123")
    assert result is not None
    assert fake_ldap.bind_attempts == ["TESTDOM\\jdoe"]
    assert result.display_name == "John Doe"
    assert result.mail == "jdoe@corp.local"
    assert result.member_of == ["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]


def test_authenticate_strips_domain_and_upn_from_input(enable_ad, fake_ldap):
    """輸入 `CORP\\jdoe` 或 `jdoe@corp.local` 都萃取 sAMAccountName 後組 NETBIOS\\sam。"""
    fake_ldap.add_user(sam="jdoe", password="secret123")
    assert ad_auth.authenticate("CORP\\jdoe", "secret123") is not None
    assert ad_auth.authenticate("jdoe@corp.local", "secret123") is not None
    assert fake_ldap.bind_attempts == ["TESTDOM\\jdoe", "TESTDOM\\jdoe"]


def test_authenticate_wrong_password_returns_none(enable_ad, fake_ldap):
    fake_ldap.add_user(sam="jdoe", password="secret123")
    assert ad_auth.authenticate("jdoe", "wrong-password") is None


def test_authenticate_unknown_user_returns_none(enable_ad, fake_ldap):
    assert ad_auth.authenticate("nobody", "whatever") is None


def test_authenticate_connection_error_raises(enable_ad, fake_ldap):
    """AD 伺服器連線層級錯誤（非帳密錯誤）raise `ADConnectionError`，不可靜默回 None。"""
    fake_ldap.connection_error = LDAPSocketOpenError("DNS 解析失敗")
    with pytest.raises(ad_auth.ADConnectionError):
        ad_auth.authenticate("jdoe", "secret123")


def test_authenticate_no_ad_server_configured_raises(enable_ad, monkeypatch):
    monkeypatch.delenv("AD_SERVER", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ad_auth.ADConnectionError):
        ad_auth.authenticate("jdoe", "secret123")


# -- AD_MOCK（僅供開發） -------------------------------------------------------------


def test_ad_mock_accepts_any_user_with_nonempty_password(enable_ad, monkeypatch):
    """AD_MOCK=true：不連 AD，任何非空密碼皆放行、回固定測試身分。"""
    monkeypatch.setenv("AD_MOCK", "true")
    get_settings.cache_clear()
    result = ad_auth.authenticate("anyone", "whatever")
    assert result is not None
    assert result.sam_account_name == "anyone"
    assert result.mail == "anyone@corp.local"
    assert result.member_of == []


def test_ad_mock_rejects_empty_password(enable_ad, monkeypatch):
    monkeypatch.setenv("AD_MOCK", "true")
    get_settings.cache_clear()
    assert ad_auth.authenticate("anyone", "") is None


# -- get_user_info()：SSO 路徑需要服務帳號 -------------------------------------------


def test_get_user_info_with_bind_account(enable_ad_bind_account, fake_ldap):
    """有服務帳號：以 `TESTDOM\\svc-sqlagent` bind 後查使用者屬性（含群組）。"""
    fake_ldap.add_bind_credential("TESTDOM\\svc-sqlagent", "svc-password")
    fake_ldap.add_user(
        sam="jdoe",
        password="irrelevant",
        entry=_entry(
            sAMAccountName="jdoe",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    info = ad_auth.get_user_info("jdoe")
    assert info is not None
    assert fake_ldap.bind_attempts == ["TESTDOM\\svc-sqlagent"]
    assert info.display_name == "John Doe"
    assert info.member_of == ["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]


def test_get_user_info_without_bind_account_returns_none(enable_ad, fake_ldap):
    """沒有服務帳號：不查群組（也不做匿名查詢），直接回 None。"""
    fake_ldap.add_user(sam="jdoe", password="irrelevant", entry=_entry(sAMAccountName="jdoe"))
    assert ad_auth.get_user_info("jdoe") is None
    assert fake_ldap.bind_attempts == []  # 完全沒有嘗試 bind


# -- is_admin() / group_cns() -------------------------------------------------------


def test_is_admin_matches_cn_name(enable_ad):
    """`ad_admin_group` 設為 CN 名（非完整 DN），比對群組完整 DN 的 CN 部分。"""
    assert ad_auth.is_admin(["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]) is True


def test_is_admin_matches_full_dn(enable_ad, monkeypatch):
    """`ad_admin_group` 設為完整 DN 時，比對群組完整 DN 精確相符。"""
    monkeypatch.setenv("AD_ADMIN_GROUP", "CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local")
    get_settings.cache_clear()
    assert ad_auth.is_admin(["cn=sqlagentadmins,ou=groups,dc=corp,dc=local"]) is True


def test_is_admin_no_match_returns_false(enable_ad):
    assert ad_auth.is_admin(["CN=SomeOtherGroup,OU=Groups,DC=corp,DC=local"]) is False


def test_is_admin_no_admin_group_configured_returns_false(monkeypatch):
    monkeypatch.delenv("AD_ADMIN_GROUP", raising=False)
    get_settings.cache_clear()
    assert ad_auth.is_admin(["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]) is False


def test_group_cns_extracts_first_cn_component():
    assert ad_auth.group_cns(
        [
            "CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local",
            "CN=Domain Users,CN=Users,DC=corp,DC=local",
        ]
    ) == ["SQLAgentAdmins", "Domain Users"]


# -- parse_ntlm_username()：NTLM Type-3 純 Python 解碼 -------------------------------


def test_parse_ntlm_username_fixed_vector():
    """固定 bytes 測試向量：Type-3 訊息帳號欄位在 offset 36/40。"""
    header = make_ntlm_type3_header("K11879")
    assert ad_auth.parse_ntlm_username(header) == "K11879"


def test_parse_ntlm_username_rejects_non_type3():
    """訊息型別非 3（如 Type-1 NEGOTIATE）→ 回空字串。"""
    import base64
    import struct

    data = bytearray(64)
    data[0:8] = b"NTLMSSP\x00"
    struct.pack_into("<I", data, 8, 1)  # Type-1
    header = "NTLM " + base64.b64encode(bytes(data)).decode("ascii")
    assert ad_auth.parse_ntlm_username(header) == ""


def test_parse_ntlm_username_garbage_returns_empty():
    assert ad_auth.parse_ntlm_username("NTLM not-base64!!") == ""
    assert ad_auth.parse_ntlm_username("Negotiate AAAA") == ""
