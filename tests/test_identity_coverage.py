"""Coverage reports must not confuse unsupported software with failed app extraction."""
from app_identities import EXTRACTION_VERSION
from audit_identity_coverage import audit


def test_audit_distinguishes_publication_gaps_failures_and_non_applicable_casks():
    artifacts = {
        "mapped": {"app": ["Known.app"]}, "unpublished": {"app": ["Known.app"]},
        "failed": {"pkg": ["Installer.pkg"]}, "empty": {"app": ["Known.app"]},
        "pkg-choices": {"pkg": ["Installer.pkg", {"choices": [{"attributeSetting": 0}]}]},
        "custom": {"installer": [{"manual": "Installer.app"}]},
        "generic": {"artifact": ["Known.app", {"target": "$APPDIR/Known.app"}]},
        "binary": {"binary": ["tool"]}, "plugin": {"screen_saver": ["Clock.saver"]},
        "archive": {"app": ["Known.app"]},
        "linux": {"app_image": ["Linux.AppImage"]}, "unknown": {"artifact": ["data", {"target": "/opt/data"}]},
        "excluded@2": {"app": ["Known.app"]}
    }
    casks = [{"token": token, "version": "1", "url": "https://example.com/source.zip", "artifacts": [stanza]}
             for token, stanza in artifacts.items()]
    identity = {"bundleName": "Known.app", "bundleIdentifier": "org.example.known"}
    raw = {"casks": {"mapped": {"apps": [identity]}, "unpublished": {"apps": [identity]},
                     "empty": {"apps": [], "caskVersion": "1", "sourceURL": casks[0]["url"],
                               "extractionVersion": EXTRACTION_VERSION, "diagnostics": [{"reason": "missing ID"}]}}}
    result = audit(casks, raw, {"appIdentities": {"mapped": [identity]}},
                   {"failed": {"status": "failed", "reason": "checksum mismatch"},
                    "archive": {"status": "no_icon", "reason": "unsupported container: download.7z"}})
    rows = {row["token"]: row for row in result["casks"]}
    assert {token: row["status"] for token, row in rows.items()} == {
        "mapped": "mapped", "unpublished": "extracted_unpublished", "failed": "failed", "empty": "empty",
        "pkg-choices": "unsupported", "custom": "unsupported", "generic": "needs_inspection",
        "binary": "not_applicable", "plugin": "not_applicable", "linux": "not_applicable", "unknown": "unsupported",
        "archive": "unsupported"
    }
    assert rows["failed"]["reason"] == "checksum mismatch"
    assert rows["empty"]["diagnostics"] == [{"reason": "missing ID"}]
    assert rows["generic"]["declaredGenericApps"] == ["Known.app"]
    assert result["catalogCasks"] == sum(result["counts"].values()) == 12
