import httpx

from tools import resolve


def test_slug_guesses():
    assert resolve.slugs("Acme  Rocket Co") == ["acme rocket co", "acme-rocket-co", "acmerocketco"]
    assert resolve.slugs("Stripe") == ["stripe"]


def test_resolve_prints_hits_as_companies_lines():
    def handler(req):
        url = str(req.url)
        if "greenhouse.io/v1/boards/acme-co/" in url:
            return httpx.Response(200, json={"jobs": [{}, {}]})
        if "lever.co/v0/postings/acmeco" in url:
            return httpx.Response(200, json=[{}])
        if "ashbyhq.com" in url:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        lines = resolve.resolve(["Acme Co"], c)
    assert lines == ['- {name: "Acme Co", ats: greenhouse, slug: acme-co}  # 2 jobs',
                     '- {name: "Acme Co", ats: lever, slug: acmeco}  # 1 jobs']
