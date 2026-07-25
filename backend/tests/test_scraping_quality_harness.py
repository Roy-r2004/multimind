from app.services.scraping.quality_harness import (
    QualityFacilityRecord,
    QualityThresholds,
    compare_same_country_quality,
    summarize_same_country_quality,
)


def test_same_country_quality_summary_counts_rates():
    records = [
        QualityFacilityRecord(country_code="FR", has_phone=True, has_address=True, verified_with_both=True),
        QualityFacilityRecord(country_code="FR", has_phone=False, has_address=True, verified_with_both=False),
        QualityFacilityRecord(country_code="DE", has_phone=True, has_address=False, verified_with_both=False),
    ]

    summary = summarize_same_country_quality(records, target_country_code="FR")

    assert summary.row_count == 3
    assert summary.wrong_country_rate == 33.33
    assert summary.phone_rate == 66.67
    assert summary.address_rate == 66.67
    assert summary.verified_with_both_rate == 33.33


def test_quality_comparison_prefers_better_kpis_over_more_rows():
    sparse_but_high_quality = [
        QualityFacilityRecord(country_code="FR", has_phone=True, has_address=True, verified_with_both=True),
        QualityFacilityRecord(country_code="FR", has_phone=True, has_address=True, verified_with_both=True),
    ]
    larger_but_noisier = [
        QualityFacilityRecord(country_code="FR", has_phone=True, has_address=False, verified_with_both=False),
        QualityFacilityRecord(country_code="FR", has_phone=False, has_address=True, verified_with_both=False),
        QualityFacilityRecord(country_code="DE", has_phone=True, has_address=True, verified_with_both=False),
        QualityFacilityRecord(country_code="FR", has_phone=False, has_address=False, verified_with_both=False),
    ]

    comparison = compare_same_country_quality(
        left_name="high_quality",
        left_records=sparse_but_high_quality,
        right_name="noisy_large",
        right_records=larger_but_noisier,
        target_country_code="FR",
        thresholds=QualityThresholds(
            max_wrong_country_rate=10.0,
            min_phone_rate=50.0,
            min_address_rate=50.0,
            min_verified_with_both_rate=40.0,
        ),
    )

    assert comparison.winner == "high_quality"
    assert comparison.left.passes_thresholds is True
    assert comparison.right.passes_thresholds is False
