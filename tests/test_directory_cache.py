"""Yahoo source snapshots survive restarts; transient throttling never becomes an empty catalog."""
import os
import json
import subprocess
import sys
from datetime import date, timedelta
from email.utils import formatdate
from unittest.mock import MagicMock

import research


def test_gallery_scan_reaches_last_photo_and_refreshes_a_truncated_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "CACHE", tmp_path / "registry.json")
    url = "https://local.yahoo.com/info-123-cypress-painting-test-city/"
    day = date(2026, 11, 21)
    photos = [f"https://s.yimg.com/bj/0001/{i:04x}.jpg" for i in range(40)]
    old = {"url": url, "status": 200, "listing_photos": photos[:12]}
    research._directory_snapshot(url, day, old)
    tags = "".join(f'<meta property="og:image" content="{src}">' for src in photos)
    identity = json.dumps({"@type": "HousePainter", "url": url, "name": "Cypress Painting"})
    body = (f'{tags}<div id="business-photo-grid"></div>'
            f'<script type="application/ld+json">{identity}</script>').encode()
    monkeypatch.setattr(research, "read_public", lambda *_: (url, 200, "text/html", body))
    current = research.directory_page(url, day)
    sampled = current["listing_photos"]
    assert current["listing_photo_count"] == 40
    assert len(set(sampled)) == 24 and sampled[0] == photos[0] and sampled[-1] == photos[-1]


def test_only_successful_canonical_listing_is_shared_across_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "CACHE", tmp_path / "registry.json")
    url = "https://local.yahoo.com/info-123-cypress-painting-test-city/"
    day = date(2026, 11, 21)
    valid = {"url": url, "status": 200, "business_nodes": [{"name": "Cypress Painting", "url": url}]}
    responses = iter([{"url": url, "status": 429}, {**valid, "business_nodes": []}, valid, {**valid, "text": "New day"}])
    monkeypatch.setattr(research, "fetch_page", lambda target: next(responses))
    assert research.directory_page(url, day)["status"] == 429
    assert not research.directory_page(url, day)["business_nodes"]
    assert research.directory_page(url, day) == valid
    reader = """
import sys,research
from datetime import date
def offline(*args):
    raise AssertionError("same-day source should come from disk")
research.fetch_page = offline
print(research.directory_page(sys.argv[1], date.fromisoformat(sys.argv[2]))["business_nodes"][0]["name"])
"""
    result = subprocess.check_output([sys.executable, "-c", reader, url, day.isoformat()],
                                     env={**os.environ, "SITESCOUT_SOURCE_CACHE": str(tmp_path)}, text=True)
    assert result.strip() == "Cypress Painting"
    assert research.directory_page(url, day + timedelta(days=1))["text"] == "New day"


def test_yahoo_retry_after_pauses_traffic_then_retries_same_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "CACHE", tmp_path / "registry.json")
    now = [1_800_000_000]
    monkeypatch.setattr(research.time, "time", lambda: now[0])
    denied, success = MagicMock(), MagicMock()
    denied.status, success.status = 429, 200
    denied.getheader.side_effect = {"Retry-After": "45", "Content-Type": "text/html"}.get
    success.getheader.return_value = "text/html"
    success.read.return_value = b'<div class="group"><h2><a href="/info-123-cypress-painting-test-city/">Cypress</a></h2></div>'
    connect = MagicMock()
    connect.return_value.getresponse.side_effect = [denied, success]
    monkeypatch.setattr(research.http.client, "HTTPSConnection", connect)
    day = date(2026, 11, 21)
    assert research.catalog_page("Test City", day, 1) == ([], None)
    assert research.directory_retry_after() == 45
    research._pause_directory(formatdate(now[0] + 60, usegmt=True))
    research._pause_directory("1")   # A shorter concurrent response cannot weaken the limit.
    assert research.directory_retry_after() == 60
    assert research.catalog_page("Test City", day, 1) == ([], None)
    assert connect.call_count == 1
    now[0] += 60
    cards, _ = research.catalog_page("Test City", day, 1)
    assert cards[0]["name"] == "Cypress" and connect.call_count == 2
    assert research.catalog_page("Test City", day, 1)[0] == cards and connect.call_count == 2
