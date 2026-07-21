from server.services.media_identity_service import MediaIdentityService


def test_quality_score_prefers_4k_hdr() -> None:
    score, labels = MediaIdentityService.score_quality("Show.S01E01.2160p.HDR.HEVC.mkv")
    assert score > 100
    assert "2160p" in labels
    assert "HDR" in labels


def test_identity_key_is_stable() -> None:
    assert MediaIdentityService.identity_key(123, 1, 2) == "tmdb:123:s01:e02"
