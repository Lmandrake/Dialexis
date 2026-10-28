"""Tests for the read-path access log.

The log exists to answer one question the graph's own git history cannot: how often
does a lookup get SERVED, DECLINED (ambiguous — the system refusing to guess), or
MISSED? Dialexis's own commits record only failures, because read-only lookups never
commit. See docs/paper/HISTORY.md, "The success signal exists — in the CONSUMERS' repos".

Two invariants these tests defend:

1. **A logging failure must never break a lookup.** The log is instrumentation, not
   function. If the disk is full or the path is unwritable, `entity` must still print
   the record and exit 0.
2. **The roll-up must never leak query text.** Raw queries stay in the gitignored
   .jsonl; the tracked roll-up carries counts only.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import access_log  # noqa: E402


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / ".access-log.jsonl"
    monkeypatch.setattr(access_log, "LOG_PATH", p)
    return p


def _lines(p):
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


class TestRecordLookup:
    def test_writes_one_json_line_per_lookup(self, log_path):
        access_log.record("entity", "odalys", "SERVED", n=1)
        access_log.record("entity", "odalys", "DECLINED", n=4)
        rows = _lines(log_path)
        assert len(rows) == 2
        assert rows[0]["verdict"] == "SERVED"
        assert rows[1]["verdict"] == "DECLINED"
        assert rows[1]["n"] == 4

    def test_records_the_query_and_command(self, log_path):
        access_log.record("resolve", "beeman", "MISS", n=0)
        row = _lines(log_path)[0]
        assert row["q"] == "beeman"
        assert row["cmd"] == "resolve"
        assert row["via"] == "cli"

    def test_appends_rather_than_truncating(self, log_path):
        for i in range(5):
            access_log.record("entity", f"q{i}", "SERVED", n=1)
        assert len(_lines(log_path)) == 5

    def test_rejects_an_unknown_verdict(self, log_path):
        with pytest.raises(ValueError):
            access_log.record("entity", "x", "PROBABLY_FINE", n=1)

    def test_a_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        # Instrumentation must never break the read path it instruments.
        monkeypatch.setattr(access_log, "LOG_PATH",
                            tmp_path / "nonexistent-dir" / "log.jsonl")
        access_log.record("entity", "odalys", "SERVED", n=1)  # must not raise


class TestRecordExternal:
    """Consumption that happened outside the CLI — Cowork reading files directly, a
    roster cross-referenced by hand, a fact quoted into a proposal. Recording
    these matters, since they are exactly the episodes that produced the
    only real denominators we have (Roster A: 32 served / 7 / 25)."""

    def test_records_episode_counts(self, log_path):
        access_log.record_external("Roster A", served=32, declined=7,
                                   absent=25, note="conference roster cross-ref")
        row = _lines(log_path)[0]
        assert row["via"] == "external"
        assert row["consumer"] == "Roster A"
        assert (row["served"], row["declined"], row["absent"]) == (32, 7, 25)

    def test_requires_a_consumer_name(self, log_path):
        with pytest.raises(ValueError):
            access_log.record_external("", served=1)

    def test_rejects_an_empty_episode(self, log_path):
        # An episode with no counts at all records nothing and would inflate the
        # episode tally for free.
        with pytest.raises(ValueError):
            access_log.record_external("Cowork", served=0, declined=0, absent=0)


class TestRollup:
    def test_counts_cli_verdicts(self, log_path):
        for v, n in [("SERVED", 1), ("SERVED", 1), ("DECLINED", 3), ("MISS", 0)]:
            access_log.record("entity", "q", v, n=n)
        r = access_log.rollup()
        assert r["cli"]["SERVED"] == 2
        assert r["cli"]["DECLINED"] == 1
        assert r["cli"]["MISS"] == 1
        assert r["cli"]["asked"] == 4

    def test_sums_external_episodes_separately_from_cli(self, log_path):
        access_log.record("entity", "q", "SERVED", n=1)
        access_log.record_external("BETA", served=32, declined=7, absent=25)
        r = access_log.rollup()
        assert r["cli"]["asked"] == 1
        assert r["external"]["episodes"] == 1
        assert r["external"]["served"] == 32
        # CLI and external must not be pooled: one is a per-lookup rate, the other
        # an episode with its own denominator. Pooling them would invent a rate.
        assert "SERVED" not in r["external"]

    def test_empty_log_rolls_up_to_zeros_not_an_error(self, log_path):
        log_path.write_text("")
        r = access_log.rollup()
        assert r["cli"]["asked"] == 0

    def test_a_corrupt_line_is_skipped_not_fatal(self, log_path):
        access_log.record("entity", "q", "SERVED", n=1)
        with log_path.open("a") as f:
            f.write("{not json at all\n")
        r = access_log.rollup()
        assert r["cli"]["asked"] == 1
        assert r["skipped"] == 1


class TestRollupReportLeaksNothing:
    def test_report_contains_no_query_text(self, log_path):
        access_log.record("entity", "odalys ferncastle", "SERVED", n=1)
        access_log.record("resolve", "a-very-private-name", "DECLINED", n=2)
        report = access_log.render_report(access_log.rollup())
        assert "odalys" not in report.lower()
        assert "private-name" not in report

    def test_report_states_the_counts_it_does_carry(self, log_path):
        access_log.record("entity", "q", "SERVED", n=1)
        report = access_log.render_report(access_log.rollup())
        assert "SERVED" in report
        assert "1" in report
