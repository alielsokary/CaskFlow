"""Reviewed products stay separate from raw observations and legacy identity feeds."""
import copy
import json
from pathlib import Path

import pytest

from app_identities import compose_release, load_manifest, project_package_products
from style_standards import write_json

REVIEWS = Path(__file__).resolve().parents[1] / "data/package_product_reviews.json"


def test_release_projects_reviewed_products_and_retains_provenance(tmp_path):
    categories, extracted, variants, output = [tmp_path / name for name in
                                               ["categories.json", "extracted.json", "variants.json", "output.json"]]
    write_json(categories, {"tokenToCategory": {}})
    raw = {"schemaVersion": 1, "casks": {"microsoft-office": {"apps": [
        {"bundleName": "Microsoft Excel.app", "bundleIdentifier": "com.microsoft.Excel"}]}}}
    write_json(extracted, raw)
    write_json(variants, {"schemaVersion": 1, "casks": {}})
    compose_release(categories, extracted, variants, output, package_reviews=REVIEWS)
    result = json.loads(categories.read_text())
    products = result["packageProductIdentities"]
    assert len(products) == 14
    assert sum(bool(values) for values in products.values()) == 9
    assert products["microsoft-office"] == products["microsoft-office-businesspro"] == []
    assert products["gpg-suite"] == products["gpg-suite-no-mail"] == products["soundtoys"] == []
    assert products["microsoft-teams"][0]["packageIdentifier"] == "com.microsoft.teams2"
    assert products["realvnc-connect"][0]["bundleIdentifier"] == products["realvnc-connect-viewer"][0]["bundleIdentifier"]
    assert products["realvnc-connect"][0]["installedPath"] != products["realvnc-connect-viewer"][0]["installedPath"]
    assert result["appIdentities"] == {"microsoft-office": raw["casks"]["microsoft-office"]["apps"]}
    assert result["packageAppCandidates"] == {}
    assert result["tokenToCategory"] == {}
    manifest = json.loads(output.read_text())
    assert manifest["packageProductReviews"] == load_manifest(REVIEWS)["casks"]
    assert manifest["casks"]["microsoft-office"]["apps"] == raw["casks"]["microsoft-office"]["apps"]
    compose_release(categories, extracted, variants, output, seed=output)
    assert json.loads(categories.read_text())["packageProductIdentities"] == {}
    assert json.loads(output.read_text())["packageProductReviews"] == {}


@pytest.mark.parametrize("case", ["missing-evidence", "unknown-decision", "empty-product", "multiple-products",
                                 "unresolved-with-product", "wrong-path", "missing-receipt", "invalid-id", "missing-plist-hash"])
def test_invalid_review_cannot_be_published(case):
    review = copy.deepcopy(load_manifest(REVIEWS)["casks"]["microsoft-teams"])
    if case == "missing-evidence":
        review["evidence"] = {}
    elif case == "unknown-decision":
        review["decision"] = "guessed"
    elif case == "empty-product":
        review["products"] = []
    elif case == "multiple-products":
        review["products"] *= 2
    elif case == "unresolved-with-product":
        review["decision"] = "shared-components"
    elif case == "wrong-path":
        review["products"][0]["installedPath"] = "/Applications/Helpers/Microsoft Teams.app"
    elif case == "missing-receipt":
        del review["products"][0]["packageIdentifier"]
    elif case == "invalid-id":
        review["products"][0]["bundleIdentifier"] = "invalid id"
    else:
        del review["evidence"]["infoSHA256"]
    with pytest.raises(ValueError):
        project_package_products({"microsoft-teams": review})
