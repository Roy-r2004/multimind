from app.services.scraping.execution_orchestrator import _extract_crawl_links


def test_extract_crawl_links_prefers_same_site_facility_pages():
    html = """
    <html><body>
      <a href="/standorte/wien">Therapiezentrum Wien</a>
      <a href="/kliniken/graz">Suchtklinik Graz</a>
      <a href="/datenschutz">Datenschutz</a>
      <a href="https://external.example/rehab">External rehab</a>
      <a href="/assets/logo.png">Logo</a>
    </body></html>
    """

    links = _extract_crawl_links(
        html,
        base_url="https://provider.example/einrichtungen",
        limit=10,
    )

    assert links == [
        ("https://provider.example/standorte/wien", "Therapiezentrum Wien"),
        ("https://provider.example/kliniken/graz", "Suchtklinik Graz"),
    ]


def test_extract_crawl_links_deduplicates_and_obeys_limit():
    html = """
    <a href="/rehab/a">Rehab A</a>
    <a href="/rehab/a#contact">Rehab A contact</a>
    <a href="/rehab/b">Rehab B</a>
    <a href="/rehab/c">Rehab C</a>
    """

    links = _extract_crawl_links(
        html,
        base_url="https://provider.example/directory",
        limit=2,
    )

    assert len(links) == 2
    assert len({url for url, _ in links}) == 2


def test_extract_crawl_links_without_limit_returns_every_relevant_unique_link():
    html = """\
    <a href="/rehab/a">Rehab A</a>
    <a href="/rehab/b">Rehab B</a>
    <a href="/rehab/c">Rehab C</a>
    <a href="/rehab/d">Rehab D</a>
    """

    links = _extract_crawl_links(
        html,
        base_url="https://provider.example/directory",
        limit=None,
    )

    assert len(links) == 4

from app.services.scraping.document_text_preparation_service import _prepare_html


def test_prepare_html_keeps_visible_text_after_nested_hidden_content():
    text, _ = _prepare_html(
        '<div hidden><span>Hidden</span><strong>Secret</strong></div><p>Visible clinic</p>'
    )

    assert "Hidden" not in text
    assert "Secret" not in text
    assert "Visible clinic" in text


def test_prepare_html_includes_json_ld_facility_data():
    text, _ = _prepare_html(
        '<script type="application/ld+json">'
        '{"name":"Therapiezentrum Wien","address":"Vienna"}'
        '</script><main>Provider</main>'
    )

    assert "Therapiezentrum Wien" in text
    assert "Vienna" in text
