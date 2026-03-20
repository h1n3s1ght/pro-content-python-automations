from app.client_identity import build_client_key, extract_client_identity


def test_build_client_key_prefers_domain():
    key = build_client_key(client_name="Acme MSP", business_domain="https://www.Acme.com/path")
    assert key == "acme.com"


def test_build_client_key_falls_back_to_normalized_name():
    key = build_client_key(client_name="Acme MSP, Inc.", business_domain="")
    assert key == "acmemspinc"


def test_extract_client_identity_reads_metadata():
    payload = {
        "metadata": {
            "businessName": "Acme MSP",
            "businessDomain": "acme.com",
        }
    }
    client_name, business_domain, client_key = extract_client_identity(payload)
    assert client_name == "Acme MSP"
    assert business_domain == "acme.com"
    assert client_key == "acme.com"

