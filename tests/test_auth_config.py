"""B-07: the startup gate that stops an unauthenticated API from serving.

These test `check_auth_configuration` directly rather than through TestClient,
because the interesting cases are the ones where the app must NOT come up.
"""
import pytest

from api.main import AuthConfigurationError, _is_loopback, check_auth_configuration


def test_a_key_is_always_enough():
    check_auth_configuration("secret", allow_no_auth=False, bind_host="0.0.0.0")


def test_no_key_refuses_to_start():
    with pytest.raises(AuthConfigurationError, match="API_KEY is not set"):
        check_auth_configuration(None, allow_no_auth=False, bind_host="127.0.0.1")


def test_opt_out_is_refused_on_a_public_bind_address():
    with pytest.raises(AuthConfigurationError, match="not a loopback"):
        check_auth_configuration(None, allow_no_auth=True, bind_host="0.0.0.0")


def test_opt_out_allowed_on_loopback():
    check_auth_configuration(None, allow_no_auth=True, bind_host="127.0.0.1")
    check_auth_configuration(None, allow_no_auth=True, bind_host="localhost")
    check_auth_configuration(None, allow_no_auth=True, bind_host="::1")


@pytest.mark.parametrize("host", ["", "0.0.0.0", "10.0.0.5", "example.com", "not an address"])
def test_unknown_or_public_hosts_are_not_loopback(host):
    """An unparseable bind address must read as public, never as safe."""
    assert _is_loopback(host) is False
