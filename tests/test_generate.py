"""Tests for template expansion logic."""

import pytest

from cdsswarm.exceptions import RequestFileError
from cdsswarm.generate import _extract_placeholders, expand_template, validate_template


class TestExtractPlaceholders:
    def test_single(self):
        assert _extract_placeholders("output/{variable}.grib") == {"variable"}

    def test_multiple(self):
        result = _extract_placeholders("{variable}_{year}_{month}.grib")
        assert result == {"variable", "year", "month"}

    def test_none(self):
        assert _extract_placeholders("output.grib") == set()

    def test_repeated(self):
        assert _extract_placeholders("{x}_{x}.grib") == {"x"}


class TestValidateTemplate:
    def _template(self, **overrides):
        base = {
            "dataset": "reanalysis-era5-single-levels",
            "request": {
                "variable": ["2m_temperature"],
                "year": ["2024"],
            },
            "target": "output/{variable}.grib",
        }
        base.update(overrides)
        return base

    def test_valid(self):
        validate_template(self._template(), ["variable"])

    def test_missing_dataset(self):
        t = self._template()
        del t["dataset"]
        with pytest.raises(RequestFileError, match="missing required key.*'dataset'"):
            validate_template(t, [])

    def test_missing_request(self):
        t = self._template()
        del t["request"]
        with pytest.raises(RequestFileError, match="missing required key.*'request'"):
            validate_template(t, [])

    def test_missing_target(self):
        t = self._template()
        del t["target"]
        with pytest.raises(RequestFileError, match="missing required key.*'target'"):
            validate_template(t, [])

    def test_request_not_a_dict(self):
        t = self._template()
        t["request"] = "not a dict"
        with pytest.raises(RequestFileError, match="must be a mapping"):
            validate_template(t, [])

    def test_split_field_not_in_request(self):
        with pytest.raises(RequestFileError, match="'month' not found in request"):
            validate_template(self._template(), ["month"])

    def test_split_field_not_a_list(self):
        t = self._template()
        t["request"]["variable"] = "2m_temperature"
        with pytest.raises(RequestFileError, match="must be a list"):
            validate_template(t, ["variable"])

    def test_unknown_placeholder_in_target(self):
        t = self._template()
        t["target"] = "output/{variable}_{month}.grib"
        with pytest.raises(
            RequestFileError, match="placeholder.*'\\{month\\}'.*not in split_by"
        ):
            validate_template(t, ["variable"])


class TestExpandTemplate:
    def _template(self, **overrides):
        base = {
            "dataset": "reanalysis-era5-single-levels",
            "request": {
                "variable": ["2m_temperature", "total_precipitation"],
                "year": ["2023", "2024"],
                "month": ["01", "02", "03"],
                "day": ["01", "02"],
                "time": ["12:00"],
                "area": [55, 5, 47, 16],
                "data_format": "grib",
            },
            "target": "output/{variable}_{year}_{month}.grib",
            "split_by": ["variable", "year", "month"],
        }
        base.update(overrides)
        return base

    def test_single_field_split(self):
        t = self._template()
        t["split_by"] = ["month"]
        t["target"] = "output/{month}.grib"
        result = expand_template(t)

        assert result["dataset"] == "reanalysis-era5-single-levels"
        assert len(result["requests"]) == 3
        targets = [r["target"] for r in result["requests"]]
        assert targets == ["output/01.grib", "output/02.grib", "output/03.grib"]

    def test_cartesian_product(self):
        t = self._template()
        result = expand_template(t)

        # 2 variables x 2 years x 3 months = 12
        assert len(result["requests"]) == 12

    def test_target_interpolation(self):
        t = self._template()
        result = expand_template(t)

        targets = [r["target"] for r in result["requests"]]
        assert "output/2m_temperature_2023_01.grib" in targets
        assert "output/total_precipitation_2024_03.grib" in targets

    def test_non_split_fields_preserved(self):
        t = self._template()
        result = expand_template(t)

        for req in result["requests"]:
            r = req["request"]
            assert r["day"] == ["01", "02"]
            assert r["time"] == ["12:00"]
            assert r["area"] == [55, 5, 47, 16]
            assert r["data_format"] == "grib"

    def test_split_values_become_single_element_lists(self):
        t = self._template()
        result = expand_template(t)

        first = result["requests"][0]["request"]
        assert first["variable"] == ["2m_temperature"]
        assert first["year"] == ["2023"]
        assert first["month"] == ["01"]

    def test_scalar_values_pass_through(self):
        t = self._template()
        result = expand_template(t)

        for req in result["requests"]:
            assert req["request"]["data_format"] == "grib"

    def test_split_by_override(self):
        t = self._template()
        t["split_by"] = ["variable", "year", "month"]
        t["target"] = "output/{year}.grib"

        result = expand_template(t, split_by=["year"])
        assert len(result["requests"]) == 2
        targets = [r["target"] for r in result["requests"]]
        assert targets == ["output/2023.grib", "output/2024.grib"]

    def test_single_value_split(self):
        t = self._template()
        t["split_by"] = ["data_format"]
        t["request"]["data_format"] = ["grib"]
        t["target"] = "output.{data_format}"

        result = expand_template(t)
        assert len(result["requests"]) == 1
        assert result["requests"][0]["target"] == "output.grib"

    def test_empty_split_by(self):
        t = self._template()
        t["split_by"] = []
        t["target"] = "output.grib"

        result = expand_template(t)
        assert len(result["requests"]) == 1
        assert result["requests"][0]["target"] == "output.grib"
        # Full request preserved as-is
        assert result["requests"][0]["request"]["variable"] == [
            "2m_temperature",
            "total_precipitation",
        ]

    def test_absent_split_by(self):
        t = self._template()
        del t["split_by"]
        t["target"] = "output.grib"

        result = expand_template(t)
        assert len(result["requests"]) == 1

    def test_deep_copy_independence(self):
        """Modifying one expanded request doesn't affect others."""
        t = self._template()
        result = expand_template(t)

        result["requests"][0]["request"]["day"].append("99")
        assert "99" not in result["requests"][1]["request"]["day"]

    def test_duplicate_targets_error(self):
        t = {
            "dataset": "ds",
            "request": {
                "variable": ["temp", "temp"],
            },
            "target": "output/{variable}.grib",
            "split_by": ["variable"],
        }
        with pytest.raises(RequestFileError, match="Duplicate target"):
            expand_template(t)

    def test_missing_split_field(self):
        t = {
            "dataset": "ds",
            "request": {"variable": ["temp"]},
            "target": "output/{month}.grib",
            "split_by": ["month"],
        }
        with pytest.raises(RequestFileError, match="'month' not found"):
            expand_template(t)

    def test_split_field_not_list(self):
        t = {
            "dataset": "ds",
            "request": {"variable": "temp"},
            "target": "output/{variable}.grib",
            "split_by": ["variable"],
        }
        with pytest.raises(RequestFileError, match="must be a list"):
            expand_template(t)

    def test_unknown_placeholder(self):
        t = {
            "dataset": "ds",
            "request": {"variable": ["temp"]},
            "target": "output/{variable}_{level}.grib",
            "split_by": ["variable"],
        }
        with pytest.raises(RequestFileError, match="placeholder.*not in split_by"):
            expand_template(t)

    def test_missing_dataset(self):
        t = {
            "request": {"variable": ["temp"]},
            "target": "output.grib",
        }
        with pytest.raises(RequestFileError, match="missing required key"):
            expand_template(t)

    def test_missing_request(self):
        t = {
            "dataset": "ds",
            "target": "output.grib",
        }
        with pytest.raises(RequestFileError, match="missing required key"):
            expand_template(t)

    def test_missing_target(self):
        t = {
            "dataset": "ds",
            "request": {"variable": ["temp"]},
        }
        with pytest.raises(RequestFileError, match="missing required key"):
            expand_template(t)
