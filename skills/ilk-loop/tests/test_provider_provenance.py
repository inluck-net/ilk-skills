import pytest
from provider_provenance import configured_provider

@pytest.mark.parametrize("url,origin", [
    ("https://user:secret@api.example:443/private/key?token=secret#secret", "https://api.example:443"),
    ("https://[::1]:8443/v1/messages?key=secret", "https://[::1]:8443"),
    ("", None), ("malformed-secret", None), ("https://host:secret", None),
])
def test_public_provenance_excludes_url_secrets(url, origin):
    row = configured_provider(url)
    assert row == {"origin": origin, "identity_basis": "configured URL; actual provider unobserved"}
    assert "secret" not in str(row)
