import subprocess

import pytest

import collect_taps

A, B = "a" * 40, "b" * 40


def cask(ref):
    return {"token": ref.rsplit("/", 1)[1], "full_token": ref}


class FakeReads:
    """What each tap's sandboxed read returns; records which taps were read and with which deadline."""

    def __init__(self, data, raises=()):
        """Serve `data` by tap; taps in `raises` crash."""
        self.data, self.raises, self.read, self.deadlines = data, set(raises), [], []

    def __call__(self, tap, deadline):
        self.read.append(tap)
        self.deadlines.append(deadline)
        if tap in self.raises:
            raise RuntimeError("worker crashed")
        return self.data.get(tap, {"casks": {}})


def run_collect(reads, taps, *, budget=600, lister=lambda name: None, known=None, only_changed=False):
    selection = collect_taps.select(taps, lister, known or {}, only_changed)
    return collect_taps.collect(selection, reads, {}, workers=2, budget=budget, clock=lambda: 100)


def test_each_tap_only_speaks_for_itself():
    reads = FakeReads({
        "a/a": {"casks": {"a/a/x": cask("a/a/x"), "b/b/evil": cask("b/b/evil")},
                "readTaps": ["a/a", "b/b"], "caskFailures": {"b/b/real": "forged"}, "failures": {"b/b": "forged"}},
        "b/b": {"casks": {"b/b/real": cask("b/b/real")}},
    })

    result = run_collect(reads, ["a/a", "b/b"])

    assert sorted(result["casks"]) == ["a/a/x", "b/b/real"]
    assert result["caskFailures"] == {} and result["failures"] == {} and "readTaps" not in result


def test_reads_share_one_deadline_from_the_budget():
    reads = FakeReads({})

    run_collect(reads, ["a/a", "b/b"], budget=60)

    assert reads.deadlines == [160, 160]


def test_taps_the_budget_leaves_out_wait_for_the_next_run():
    reads = FakeReads({"a/a": {"casks": {"a/a/x": cask("a/a/x")}}, "b/b": {"unreached": ["b/b"]}})

    result = run_collect(reads, ["a/a", "b/b"])

    assert sorted(result["casks"]) == ["a/a/x"] and result["unreached"] == ["b/b"]


def test_a_crashed_or_malformed_read_only_loses_its_tap():
    reads = FakeReads({
        "a/a": {"readTaps": [[]], "casks": [], "caskFailures": [["x"]]},
        "b/b": {"casks": {"b/b/real": cask("b/b/real")}},
    }, raises={"c/c"})

    result = run_collect(reads, ["a/a", "b/b", "c/c"])

    assert sorted(result["casks"]) == ["b/b/real"] and sorted(result["unreached"]) == ["c/c"]


def test_summary_lists_only_validated_names():
    result = {
        "failures": {"a/b": "Error: `rm -rf`", "x`@evil/y": "nope"},
        "caskFailures": {"a/b/c": "", "@evil": ""},
        "unreached": ["c/d", "<img src=x>"],
    }

    text = collect_taps.summary(result)

    for name in ("a/b", "a/b/c", "c/d"):
        assert name in text
    assert "@" not in text and "<" not in text and "rm -rf" not in text


def test_cask_listing_comes_from_git_not_from_the_reads():
    reads = FakeReads({"a/a": {"casks": {"a/a/x": cask("a/a/x")}, "listed": {"b/b": {"b/b/x": A}}}})
    listings = {"a/a": {"a/a/x": A}, "b/b": {"b/b/real": B}}

    result = run_collect(reads, ["a/a", "b/b"], lister=listings.get)

    assert result["listed"] == listings


def git_runner(ls_tree, clone_code=0):
    def run(cmd, cwd=None):
        if cmd[1] == "clone":
            return subprocess.CompletedProcess(cmd, clone_code, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=ls_tree, stderr="")
    return run


def test_list_casks_reads_cask_file_names_without_running_them(tmp_path):
    tree = ("100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tCasks/sill.rb\n100644 blob bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tCasks/s/sharded.rb\n"
            "100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tCasks/README.md\n100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tCasks/Bad Name.rb\n")
    assert collect_taps.list_casks({"name": "u/r"}, git_runner(tree), tmp_path) == {"u/r/sharded": B, "u/r/sill": A}
    assert collect_taps.list_casks({"name": "u/r"}, git_runner(tree, clone_code=1), tmp_path) is None
    assert collect_taps.list_casks({"name": "u/r"}, git_runner(""), tmp_path) is None


def test_list_casks_treats_a_hung_clone_as_unknown(tmp_path):
    def run(cmd, cwd=None):
        raise subprocess.TimeoutExpired(cmd, 120)

    assert collect_taps.list_casks({"name": "u/r"}, run, tmp_path) is None


def test_sweep_reads_only_changed_or_unsettled_taps():
    reads = FakeReads({})
    listings = {
        "same/t": {"same/t/x": A},
        "changed/t": {"changed/t/x": B},
        "new/t": {"new/t/x": A},
        "partly/t": {"partly/t/x": A, "partly/t/y": A},
    }
    known = {"same/t/x": A, "changed/t/x": A, "partly/t/x": A}

    result = run_collect(reads, sorted(listings) + ["unknown/t"], lister=listings.get, known=known, only_changed=True)

    assert sorted(reads.read) == ["changed/t", "new/t", "partly/t", "unknown/t"]
    assert result["unchanged"] == ["same/t"]


def test_completed_taps_report_the_files_they_were_read_from():
    reads = FakeReads({
        "a/a": {"casks": {"a/a/x": cask("a/a/x")}, "caskFailures": {"a/a/y": "boom"}},
        "b/b": {"unreached": ["b/b"]},
    })
    listings = {"a/a": {"a/a/x": A, "a/a/y": B}, "b/b": {"b/b/z": A}}

    result = run_collect(reads, ["a/a", "b/b"], lister=listings.get)

    assert result["processed"] == {"a/a/x": A}


class FakeUsers:
    """Users named user<N>; `dirty` names fail cleanup once they have served a tap."""

    def __init__(self, dirty=(), unmakeable=()):
        """Users in `dirty` fail cleanup after a tap; indexes in `unmakeable` cannot be created."""
        self.dirty, self.unmakeable, self.made, self.cleaned = set(dirty), set(unmakeable), [], []

    def make(self, index):
        if index in self.unmakeable:
            raise RuntimeError("dscl failed")
        self.made.append(index)
        return f"user{index}"

    def clean(self, user):
        self.cleaned.append(user)
        return not (user in self.dirty and self.cleaned.count(user) > 1)


def test_tap_users_are_checked_clean_before_their_first_tap():
    fake = FakeUsers()
    collect_taps.TapUsers(2, fake.make, fake.clean)
    assert fake.cleaned == ["user0", "user1"]


def test_a_tap_user_that_cannot_be_cleaned_is_replaced_before_the_next_tap():
    fake = FakeUsers(dirty={"user0"})
    users = collect_taps.TapUsers(1, fake.make, fake.clean)

    with users.lend() as first:
        pass
    with users.lend() as second:
        pass

    assert (first, second) == ("user0", "user1") and fake.made == [0, 1]


def test_the_run_stops_once_no_clean_tap_user_can_be_had():
    fake = FakeUsers(dirty={"user0"}, unmakeable={1})
    users = collect_taps.TapUsers(1, fake.make, fake.clean)

    with users.lend():
        pass
    with pytest.raises(collect_taps.TapUsersExhausted):
        with users.lend():
            pass
    with pytest.raises(collect_taps.TapUsersExhausted):
        with users.lend():
            pass


def test_retirements_are_capped():
    fake = FakeUsers(dirty={f"user{index}" for index in range(10)})
    users = collect_taps.TapUsers(1, fake.make, fake.clean)

    with pytest.raises(collect_taps.TapUsersExhausted):
        for _ in range(10):
            with users.lend():
                pass
    assert len(fake.made) <= 1 + collect_taps.TapUsers.MAX_RETIRED


def test_an_exhausted_pool_stops_collect_instead_of_losing_taps_quietly():
    def read(tap, deadline):
        raise collect_taps.TapUsersExhausted("no clean tap user left")

    with pytest.raises(collect_taps.TapUsersExhausted):
        run_collect(read, ["a/a"])
