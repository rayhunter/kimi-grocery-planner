"""Web UI tests: form page, run lifecycle, and HTML report rendering — planner mocked."""
import pytest
from fastapi.testclient import TestClient

import web
from models.report import ShoppingReport, ItemRecommendation, ShoppingTrip
from tests.conftest import make_listing


def _report() -> ShoppingReport:
    listing = make_listing(store_name="H-E-B", regular=3.99, sale=2.98, listing_id="L1")
    return ShoppingReport(
        locale="Austin, TX",
        stores_searched=[],
        items_analyzed=[
            ItemRecommendation(
                item_query="cherry tomatoes",
                best_pick=listing,
                all_listings=[listing],
                reasoning="cheapest verified",
                membership_tip="Target Circle is free",
            ),
            ItemRecommendation(
                item_query="unicorn fruit", best_pick=None, all_listings=[],
                reasoning="no data found",
            ),
        ],
        optimized_trips=[
            ShoppingTrip(primary_store="H-E-B", items_to_buy_here=["cherry tomatoes"],
                         estimated_total=2.98, notes="one stop"),
        ],
        executive_summary="H-E-B <wins> & saves",
        total_potential_savings=1.01,
        data_quality_notes="1/1 verified",
    )


@pytest.fixture
def client(monkeypatch):
    async def fake_planner(locale, items, max_stores, max_concurrency, on_progress):
        on_progress("🔍 discovering...")
        on_progress("✅ done")
        return _report()

    monkeypatch.setattr(web, "run_shopping_planner", fake_planner)
    web.RUNS.clear()
    with TestClient(web.app) as c:
        yield c


class TestWebUI:
    def test_form_page_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "run-form" in resp.text
        assert "Kimi K3 Grocery Planner" in resp.text

    def test_run_lifecycle(self, client):
        resp = client.post("/api/runs", json={"locale": "Austin, TX", "items": ["cherry tomatoes"]})
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        for _ in range(50):
            status = client.get(f"/api/runs/{run_id}").json()
            if status["status"] == "completed":
                break
        assert status["status"] == "completed"
        assert "🔍 discovering..." in status["progress"]
        html_out = status["report_html"]
        assert "H-E-B" in html_out
        assert "$2.98" in html_out
        assert "H-E-B &lt;wins&gt; &amp; saves" in html_out          # summary is escaped
        assert "No price data found" in html_out                     # honest no-data card
        assert "Total deal savings: $1.01" in html_out

    def test_validation_rejects_empty_items(self, client):
        assert client.post("/api/runs", json={"locale": "Austin, TX", "items": []}).status_code == 422
        assert client.post("/api/runs", json={"locale": "Austin, TX", "items": ["  "]}).status_code == 422
        assert client.post("/api/runs", json={"locale": "A", "items": ["x"]}).status_code == 422

    def test_unknown_run_404(self, client):
        assert client.get("/api/runs/nope").status_code == 404

    def test_failed_run_surfaces_error(self, client, monkeypatch):
        async def exploding_planner(**kwargs):
            raise RuntimeError("scout meltdown")

        monkeypatch.setattr(web, "run_shopping_planner", exploding_planner)
        run_id = client.post("/api/runs", json={"locale": "Austin, TX", "items": ["x"]}).json()["run_id"]
        for _ in range(50):
            status = client.get(f"/api/runs/{run_id}").json()
            if status["status"] == "failed":
                break
        assert status["status"] == "failed"
        assert "scout meltdown" in status["error"]
