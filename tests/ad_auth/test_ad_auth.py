"""`app/services/ad_auth.py` 單元測試：authenticate() / is_admin()（mock ldap3，不需真實 AD）。"""

import pytest
from ldap3.core.exceptions import LDAPSocketOpenError

from app.services import ad_auth
from tests.ad_auth.conftest import FakeEntry


def _entry(**attrs) -> FakeEntry:
    return FakeEntry(**attrs)


def test_authenticate_success_via_upn(enable_ad, fake_ldap):
    """UPN 格式（`user@corp.local`）bind 成功，回傳補齊屬性的 ADUser。"""
    fake_ldap.add_user(
        sam="jdoe",
        password="secret123",
        upn="jdoe@corp.local",
        entry=_entry(
            sAMAccountName="jdoe",
            userPrincipalName="jdoe@corp.local",
            displayName="John Doe",
            mail="jdoe@corp.local",
            memberOf=["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"],
        ),
    )
    result = ad_auth.authenticate("jdoe", "secret123")
    assert result is not None
    assert result.display_name == "John Doe"
    assert result.mail == "jdoe@corp.local"
    assert result.upn == "jdoe@corp.local"
    assert result.member_of == ["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]


def test_authenticate_success_via_domain_user(enable_ad, fake_ldap):
    """UPN 候選失敗、但 DOMAIN\\user 候選成功時也能 bind。"""
    fake_ldap.add_user(
        sam="jdoe",
        password="secret123",
        domain="CORP",
        entry=_entry(sAMAccountName="jdoe", displayName="John Doe"),
    )
    result = ad_auth.authenticate("jdoe", "secret123")
    assert result is not None
    assert result.sam_account_name == "jdoe"


def test_authenticate_wrong_password_returns_none(enable_ad, fake_ldap):
    fake_ldap.add_user(sam="jdoe", password="secret123", upn="jdoe@corp.local")
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
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ad_auth.ADConnectionError):
        ad_auth.authenticate("jdoe", "secret123")


def test_is_admin_matches_cn_name(enable_ad):
    """`ad_admin_group` 設為 CN 名（非完整 DN），比對群組完整 DN 的 CN 部分。"""
    assert ad_auth.is_admin(["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]) is True


def test_is_admin_matches_full_dn(enable_ad, monkeypatch):
    """`ad_admin_group` 設為完整 DN 時，比對群組完整 DN 精確相符。"""
    from app.config import get_settings

    monkeypatch.setenv("AD_ADMIN_GROUP", "CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local")
    get_settings.cache_clear()
    assert ad_auth.is_admin(["cn=sqlagentadmins,ou=groups,dc=corp,dc=local"]) is True


def test_is_admin_no_match_returns_false(enable_ad):
    assert ad_auth.is_admin(["CN=SomeOtherGroup,OU=Groups,DC=corp,DC=local"]) is False


def test_is_admin_no_admin_group_configured_returns_false(monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("AD_ADMIN_GROUP", raising=False)
    get_settings.cache_clear()
    assert ad_auth.is_admin(["CN=SQLAgentAdmins,OU=Groups,DC=corp,DC=local"]) is False
