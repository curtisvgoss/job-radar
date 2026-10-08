from radar import digest


def row(i, score, comp=None):
    return {"company": f"C{i}", "title": "T|x", "location": "L", "comp": comp, "score": score,
            "fit": "Line one\nline two", "url": f"https://x/{i}"}


def test_sorted_thresholded_and_escaped():
    rows = digest.matches([row(1, 59), row(2, 60, "$1"), row(3, 95)], 60)
    assert [r["score"] for r in rows] == [95, 60]
    md = digest.render(rows)
    assert "T\\|x" in md and "Line one line two" in md and "| $1 |" in md
    assert md.index("C3") < md.index("C2")


def test_max_60_rows():
    md = digest.render(digest.matches([row(i, 90) for i in range(75)], 60))
    assert sum(l.startswith("| C") and not l.startswith("| Company") for l in md.splitlines()) == 60
    assert "60 of 75" in md
