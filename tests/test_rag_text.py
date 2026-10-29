from src.services.rag_service import _TextExtractor


def test_text_extractor_skips_scripts():
    parser = _TextExtractor()
    parser.feed("<html><style>x{}</style><p>Olá</p><script>alert(1)</script><svg><text>n</text></svg><p>mundo</p></html>")
    assert "".join(parser.parts) == "Olámundo"
