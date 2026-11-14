import json

import seed_taps
import tap_registry


def analytics(*rows):
    return {"items": [{"number": i, "cask": cask, "count": count} for i, (cask, count) in enumerate(rows, 1)]}


def head_from(moves):
    """Fake GitHub: repo path -> where it now lives, None when gone; unlisted repos stay put."""
    def head(url):
        path = url.removeprefix("https://github.com/")
        target = moves.get(path, path)
        return None if target is None else f"https://github.com/{target}"
    return head


def test_select_ranks_third_party_taps_by_summed_cask_installs():
    data = analytics(("figma", "9,999"), ("homebrew/cask-versions/foo", "500"),
                     ("u/a/x", "60"), ("u/a/y", "50"), ("u/b/z", "99"), ("v/c/w", "1,200"))

    assert seed_taps.select(data, threshold=100) == ["v/c", "u/a"]


def test_resolve_follows_moves_and_drops_missing_or_official_taps():
    head = head_from({
        "gone/homebrew-x": None,
        "old/homebrew-x": "New/homebrew-x",
        "caskroom/homebrew-cask": "Homebrew/homebrew-cask",
        "o/homebrew-r": "O/plain-repo",
    })

    assert seed_taps.resolve("ok/tap", head) == {"name": "ok/tap"}
    assert seed_taps.resolve("gone/x", head) is None
    assert seed_taps.resolve("old/x", head) == {"name": "new/x"}
    assert seed_taps.resolve("caskroom/cask", head) is None
    assert seed_taps.resolve("o/r", head) == {"name": "o/plain-repo", "remote": "https://github.com/O/plain-repo"}


def test_seed_drops_taps_that_moved_onto_a_higher_ranked_one():
    head = head_from({"kegworks-app/homebrew-kegworks": "Sikarugir-App/homebrew-sikarugir"})

    entries = seed_taps.seed(["sikarugir-app/sikarugir", "kegworks-app/kegworks", "a/b"], head, limit=10)

    assert entries == [{"name": "sikarugir-app/sikarugir"}, {"name": "a/b"}]


def test_main_writes_the_first_batches_merged_into_the_registry(tmp_path, monkeypatch):
    source = tmp_path / "analytics.json"
    source.write_text(json.dumps(analytics(*[(f"u{i}/t/c", str(1000 - i)) for i in range(5)])), encoding="utf-8")
    out = tmp_path / "taps.json"
    out.write_text('{"version": 1, "taps": [{"name": "u1/t"}, {"name": "z/z"}]}', encoding="utf-8")
    monkeypatch.setattr(seed_taps, "github_head", head_from({}))

    seed_taps.main(["--analytics", str(source), "--out", str(out), "--batch-size", "2", "--batch", "2"])

    assert [entry["name"] for entry in tap_registry.load(out)] == ["u0/t", "u1/t", "u2/t", "u3/t", "z/z"]
