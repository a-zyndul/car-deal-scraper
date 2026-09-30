import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Listing, find_price, find_mileage, find_year, to_int, with_page
from adapters.olx_adapter import OlxAdapter
from adapters.autoplac_adapter import AutoplacAdapter
from adapters.otomoto_adapter import OtomotoAdapter
from database import Database
from filters import Criteria
from notifier import format_message

OTOMOTO = """<footer><a href="https://facebook.com/otomotopl">Znajdź nas</a></footer>
<article data-id="1"><section><h2><a href="https://www.otomoto.pl/osobowe/oferta/fiat-tipo-ID6X.html?x=1">Fiat Tipo 1.4 T-Jet</a></h2>
<p>120 KM • LPG, salon PL</p><dl><dt>mileage</dt><dd data-parameter="mileage">84 553 km</dd>
<dd data-parameter="fuel_type">Benzyna+LPG</dd><dd data-parameter="year">2018</dd></dl>
<ul><li><p>Poznań (Wielkopolskie)</p></li></ul><div><h3>29 900</h3><p>PLN</p></div></section></article>"""

OLX = """<div data-cy="l-card" id="998"><a href="/d/oferta/fiat-tipo-CID5-ID1abc.html?reason=x"><h4>Fiat Tipo kombi LPG</h4></a>
<p data-testid="ad-price">28 500 zł Do negocjacji</p><span>2017 - 120 000 km</span>
<p data-testid="location-date">Poznań - Odświeżono dnia 29 września 2026</p></div>"""

AUTOPLAC = """<nav><a href="/oferty">Oferty</a></nav>
<div class="grid"><div class="c"><a href="/oferta/fiat-tipo-123456"><h3>Fiat Tipo 1.4</h3></a><span>27 900 zł</span><span>2017</span><span>95 000 km</span></div>
<div class="c"><a href="/oferta/opel-astra-654321"><h3>Opel Astra</h3></a><span>31 000 zł</span><span>2015</span><span>140 000 km</span></div></div>"""


def test_helpers():
    assert to_int("84 553 km") == 84553 and to_int("29\u00a0900,50 zł") == 29900 and to_int("brak") is None
    assert find_price("Cena 28 500 zł Do negocjacji") == 28500
    assert find_mileage("2017 - 120 000 km") == 120000
    assert find_year("Odświeżono 29 września 2040") is None and find_year("rok 2017") == 2017
    assert "page=2" in with_page("https://x.pl/a?b=1", 2) and with_page("https://x.pl/a", 1) == "https://x.pl/a"


def test_otomoto():
    items = OtomotoAdapter().parse(OTOMOTO)
    assert len(items) == 1  # stopka ignorowana
    l = items[0]
    assert (l.ext_id, l.price, l.year, l.mileage, l.fuel) == ("1", 29900, 2018, 84553, "Benzyna+LPG")
    assert l.url.endswith("ID6X.html") and l.location.startswith("Poznań")


def test_olx():
    l = OlxAdapter().parse(OLX)[0]
    assert (l.ext_id, l.price, l.year, l.mileage) == ("998", 28500, 2017, 120000)


def test_autoplac():
    items = AutoplacAdapter().parse(AUTOPLAC)
    assert len(items) == 2 and items[0].price == 27900 and items[0].year == 2017 and items[0].mileage == 95000


def test_criteria():
    c = Criteria.from_dict(dict(price_max=30000, year_min=2016, title_keywords_any=["tipo"],
                                exclude_fuels=["diesel"], exclude_phrases=["po wypadku"]))
    base = dict(platform="x", ext_id="1", url="u", title="Fiat Tipo", price=25000, year=2018, fuel="Benzyna")
    assert c.check(Listing(**base))[0]
    assert not c.check(Listing(**{**base, "price": None}))[0]
    assert not c.check(Listing(**{**base, "price": 31000}))[0]
    assert not c.check(Listing(**{**base, "fuel": "Diesel"}))[0]
    assert not c.check(Listing(**{**base, "title": "Opel Astra"}))[0]
    assert not c.check(Listing(**{**base, "description": "auto po wypadku"}))[0]
    assert c.check(Listing(**{**base, "year": None, "mileage": None}))[0]  # nieznane pola nie odrzucają
    assert c.check(Listing(**{**base, "description": "bezwypadkowy"}))[0]


def test_db_and_notifier():
    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "t.db"))
        l = Listing(platform="olx", ext_id="9", title="A_B *x* <b>", url="https://a.pl/?q=1&r=2", price=1000)
        assert not db.is_seen(l.uid); db.add(l); db.add(l)
        assert db.is_seen("olx:9") and db.count() == 1
    msg = format_message(l, "ok <script>")
    assert "&lt;b&gt;" in msg and "&lt;script&gt;" in msg and "1 000 zł" in msg and "&amp;r=2" in msg


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("OK", name)
