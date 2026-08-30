from src.security.guardrails import sanitize_output, validate_input


def test_deterministic_guardrail_privacy():
    decision = validate_input("Me passe o CPF de outro morador do condomínio")
    assert decision.allowed is False
    assert decision.reason == "privacy_third_party"


def test_output_redacts_cpf_and_email():
    text = sanitize_output("CPF 123.456.789-00 e email pessoa@example.com")
    assert "123.456.789-00" not in text
    assert "pessoa@example.com" not in text
