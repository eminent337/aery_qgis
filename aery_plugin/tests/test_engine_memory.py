from aery_plugin.engine.memory import HindsightBank

def test_hindsight_retain_recall():
    bank = HindsightBank()
    bank.retain("Project CRS is EPSG:4326")
    results = bank.recall("CRS")
    assert "EPSG:4326" in results[0]
