import pytest

from pokeldn.frlg.remote.trade_journal import TradeJournal, TradeJournalError


RUN = "91" * 16


def test_trade_journal_fsyncs_a_verifiable_hash_chained_record(tmp_path):
    journal = TradeJournal(RUN, root=tmp_path)
    first = journal.append_durable("prepared", side="A", selection_hash="ab" * 32)
    second = journal.append_durable("decision", kind="start", decision_hash="cd" * 32)
    journal.close()

    records = TradeJournal.verify(journal.path)
    assert len(records) == 2
    assert records[0]["record_hash"] == first
    assert records[1]["record_hash"] == second
    assert records[1]["prev_hash"] == first
    assert TradeJournal.classify_recovery(records) == "CANCELLED_BEFORE_RELEASE"


def test_trade_journal_refuses_to_resume_an_existing_run(tmp_path):
    journal = TradeJournal(RUN, root=tmp_path)
    journal.append_durable("session_opened")
    journal.close()

    with pytest.raises(TradeJournalError, match="do not resume"):
        TradeJournal(RUN, root=tmp_path)


@pytest.mark.parametrize("fields", [
    {"data": "00"},
    {"nested": {"room_key": "secret"}},
    {"selection": {"pokemon_digest": "ab" * 32}},
    {"party": ["hidden"]},
])
def test_trade_journal_rejects_sensitive_fields_recursively(tmp_path, fields):
    journal = TradeJournal(RUN, root=tmp_path)
    with pytest.raises(ValueError, match="sensitive field"):
        journal.append_durable("unsafe", **fields)
    journal.close()


def test_trade_journal_detects_torn_tail_and_record_tampering(tmp_path):
    journal = TradeJournal(RUN, root=tmp_path)
    journal.append_durable("decision", kind="start", decision_hash="ab" * 32)
    journal.close()
    original = journal.path.read_bytes()

    journal.path.write_bytes(original[:-1])
    with pytest.raises(TradeJournalError, match="incomplete final"):
        TradeJournal.verify(journal.path)

    journal.path.write_bytes(original.replace(b'"kind":"start"', b'"kind":"finish"'))
    with pytest.raises(TradeJournalError, match="hash failed"):
        TradeJournal.verify(journal.path)


def test_recovery_classification_never_recommends_replay_after_release(tmp_path):
    journal = TradeJournal(RUN, root=tmp_path)
    journal.append_durable("release_created", kind="start", release_hash="ab" * 32)
    journal.close()
    records = TradeJournal.verify(journal.path)
    assert TradeJournal.classify_recovery(records) == "IN_DOUBT"


def test_recovery_classifies_command_permit_as_in_doubt_even_if_command_send_is_unknown(tmp_path):
    journal = TradeJournal(RUN, root=tmp_path)
    journal.append_durable("command_permit_issued", kind="start", command="START_TRADE")
    journal.close()
    records = TradeJournal.verify(journal.path)
    assert TradeJournal.classify_recovery(records) == "IN_DOUBT"


def test_trade_journal_rejects_invalid_run_ids_and_metadata_overrides(tmp_path):
    with pytest.raises(ValueError, match="128-bit"):
        TradeJournal("not-a-run", root=tmp_path)
    journal = TradeJournal(RUN, root=tmp_path)
    with pytest.raises(ValueError, match="override"):
        journal.append_durable("bad", sequence=99)
    journal.close()
