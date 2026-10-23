"""Localization software gates. All ROMs, wrapper identities and layouts are synthetic."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pokeldn.frlg.localization import LocalizationError, PAGE_SIZE, canonical, read_json, sha256
from pokeldn.frlg.localization.codec import EXT_ARGUMENTS, parse_message
from pokeldn.frlg.localization.examples import synthetic_example
from pokeldn.frlg.localization.patch import Edit, Patch, build_patch
from pokeldn.frlg.localization.payloads import (COMMIT_OK, REFUSED, STAGE_DATA, STAGE_OK,
                                               build_commit, build_stage)
from pokeldn.frlg.localization.profiles import Identity, Profile
from pokeldn.frlg.localization.session import Session
from pokeldn.frlg.localization.transport import BufferTransport, ModelTransport, Observation
from pokeldn.frlg.rom import buffer_script as bs

ROOT = Path(__file__).resolve().parents[1]
needs_unicorn = pytest.mark.skipif(not bs.emulation_available(), reason="needs unicorn")


@pytest.fixture
def example():
    return synthetic_example()


def resign(value):
    value["patch_id"] = sha256(canonical({k: v for k, v in value.items() if k != "patch_id"}))
    return value


def test_profile_roundtrip_preserves_address_evidence_and_identity(example):
    profile, base, _ = example
    parsed = Profile.parse(profile.as_dict())
    assert parsed == profile and parsed.fingerprint == profile.fingerprint
    profile.validate_base(base)
    runtime = profile.runtime_base(base)
    assert runtime[13 * PAGE_SIZE:13 * PAGE_SIZE + 2] == b"\x52\xbe"
    assert sha256(runtime) != sha256(base)


@pytest.mark.parametrize("change", ["hash", "revision", "wrapper", "ram-overlap", "payload-overlap",
                                         "resident-overlap", "out-of-ewram", "unknown-build", "no-evidence"])
def test_unknown_or_unsafe_profiles_are_rejected(example, change):
    profile, base, _ = example
    value = profile.as_dict()
    value["identity"] = dict(value["identity"])
    if change == "hash":
        value["base_sha256"] = "0" * 64
    elif change == "revision":
        value["identity"]["revision"] = 0
    elif change == "wrapper":
        value["identity"]["wrapper_sha256"] = "unknown"
    elif change == "ram-overlap":
        value["staging"]["state_address"] = profile.stage_address + 4
    elif change == "payload-overlap":
        value["staging"]["stage_address"] = 0x0201C000
        value["staging"]["available"]["start"] = 0x0201C000
    elif change == "resident-overlap":
        value["staging"]["protected"][0]["start"] = profile.stage_address
    elif change == "out-of-ewram":
        value["staging"]["available"]["end"] = 0x02040004
    elif change == "unknown-build":
        value["identity"]["game_code"] = "ABCD"
    else:
        value["allowed"][0]["evidence"] = ""
    with pytest.raises(LocalizationError):
        Profile.parse(value).validate_base(base)


def test_game_header_is_checked_in_addition_to_file_hash(example):
    profile, base, _ = example
    altered = bytearray(base)
    altered[0xAC:0xB0] = b"BPGE"
    with pytest.raises(LocalizationError, match="game code"):
        replace(profile, base_sha256=sha256(altered)).validate_base(altered)


def test_package_roundtrip_and_deterministic_content_id(example):
    profile, base, patch = example
    parsed = Patch.parse(json.loads(json.dumps(patch.as_dict())))
    assert parsed == patch
    assert parsed.validate(profile, base) == patch.validate(profile, base)
    assert [p.kind for p in patch.pages] == ["resource", "code", "activation"]
    assert "originals" not in patch.as_dict()


def test_merge_keeps_unmodified_bytes_and_known_wrapper_changes(example):
    profile, base, _ = example
    start = 13 * PAGE_SIZE
    profile = replace(profile, allowed=profile.allowed + (replace(profile.allowed[0], start=start,
                                                                 end=start + PAGE_SIZE),))
    edit = Edit(start + 32, base[start + 32:start + 34], b"\x01\x02")
    patch = build_patch(profile, base, [edit])
    original, target = patch.validate(profile, base)[start]
    assert original[:2] == target[:2] == b"\x52\xbe"
    assert target[:32] == original[:32] and target[34:] == original[34:]


def test_edits_split_at_page_boundaries_without_losing_bytes(example):
    profile, base, _ = example
    offset = 2 * PAGE_SIZE - 2
    patch = build_patch(profile, base, [Edit(offset, base[offset:offset + 4], b"\x01\x02\x03\x04")])
    assert [p.offset for p in patch.pages] == [PAGE_SIZE, 2 * PAGE_SIZE]
    assert [e.after for p in patch.pages for e in p.edits] == [b"\x01\x02", b"\x03\x04"]


def test_page_split_omits_a_page_with_no_effective_changes(example):
    profile, base, _ = example
    offset = 2 * PAGE_SIZE - 2
    target = base[offset:offset + 2] + b"\x01\x02"
    patch = build_patch(profile, base, [Edit(offset, base[offset:offset + 4], target)])
    assert [p.offset for p in patch.pages] == [2 * PAGE_SIZE]


@pytest.mark.parametrize("change", ["tamper-id", "tamper-hash", "duplicate", "reorder", "activation",
                                         "wrong-profile", "bad-preimage", "cross-page"])
def test_untrusted_package_is_rejected_even_with_recomputed_id(example, change):
    profile, base, patch = example
    value = patch.as_dict()
    if change == "tamper-id":
        value["patch_id"] = "0" * 64
    elif change == "tamper-hash":
        value["pages"][0]["after_sha256"] = "0" * 64
    elif change == "duplicate":
        value["pages"].insert(0, value["pages"][0])
    elif change == "reorder":
        value["pages"].reverse()
    elif change == "activation":
        value["activation"] = []
    elif change == "wrong-profile":
        value["profile_sha256"] = "0" * 64
    elif change == "bad-preimage":
        value["pages"][0]["edits"][0]["before"] = "00000000"
    else:
        value["pages"][0]["edits"][0]["offset"] = 2 * PAGE_SIZE - 1
    if change != "tamper-id":
        resign(value)
    with pytest.raises(LocalizationError):
        Patch.parse(value).validate(profile, base)


def test_protected_pages_and_mixed_activation_are_refused(example):
    profile, base, _ = example
    for offset in (0, 0xC000):
        with pytest.raises(LocalizationError):
            build_patch(profile, base, [Edit(offset, base[offset:offset + 1], b"\x00")])
    with pytest.raises(LocalizationError, match="separate pages"):
        build_patch(profile, base, [Edit(0x1010, base[0x1010:0x1011], b"\x00", "resource"),
                                    Edit(0x1020, base[0x1020:0x1021], b"\x00", "activation")])


def test_protection_applies_to_whole_committed_page(example):
    profile, base, _ = example
    protected = replace(profile.protected[0], start=0x10F0, end=0x10F2)
    profile = replace(profile, protected=profile.protected + (protected,))
    with pytest.raises(LocalizationError, match="protected"):
        build_patch(profile, base, [Edit(0x1010, base[0x1010:0x1011], b"\x00")])


def test_direct_commit_builder_cannot_modify_bytes_outside_permissions(example):
    profile, base, patch = example
    profile = replace(profile, allowed=(replace(profile.allowed[0], start=0x1010, end=0x1014),))
    old, target = patch.validate(example[0], base)[0x1000]
    bad = bytearray(target)
    bad[0] ^= 1
    with pytest.raises(LocalizationError, match="allowed"):
        build_commit(profile, "12" * 16, patch.patch_id, 0x1000, old, bytes(bad))


def test_lossless_parser_preserves_every_byte_including_unknowns_and_padding():
    for byte in range(256):
        raw = bytes((byte, 0xFF, 0xFD, 0x00, 0x11))
        assert parse_message(raw).to_bytes() == raw
    raw = b"\xbb\xfd\x01\xfc\x04\x01\x02\x03\xfe\xfa\xfb\xff\x00\xff"
    assert parse_message(raw).require_rewrite_safe().to_bytes() == raw


@pytest.mark.parametrize("raw", [b"\xfc\x99\xff\xbb", b"\xfc\x04\x01", b"\xfd", b"\xfd\xfe\xff",
                                      b"\xbb", b"\xf9\xff\xbb\xff", b"\x0a\xff"])
def test_unknown_or_truncated_text_cannot_be_rewritten(raw):
    message = parse_message(raw)
    assert message.to_bytes() == raw
    with pytest.raises(LocalizationError):
        message.require_rewrite_safe()


def test_sound_and_clear_control_parameters_are_not_mistaken_for_terminators():
    assert EXT_ARGUMENTS[0x10] == 2 and EXT_ARGUMENTS[0x11] == 1
    raw = b"\xfc\x10\xff\xfe\xfc\x11\xff\xbb\xff"
    parsed = parse_message(raw).require_rewrite_safe()
    assert [t.kind for t in parsed.tokens] == ["extended", "extended", "glyph", "end"]
    assert parsed.to_bytes() == raw


def test_json_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path):
    path = tmp_path / "input.json"
    for text in ('{"offset":1,"offset":2}', '{"x":NaN}', '{"x":1e9999}'):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(LocalizationError):
            read_json(path)


@pytest.fixture
def model(example):
    if not bs.emulation_available():
        pytest.skip("needs unicorn")
    profile, base, patch = example
    return ModelTransport(profile, base)


def session_for(example, model, tmp_path, **kwargs):
    profile, base, patch = example
    return Session(profile, patch, base, model, tmp_path / "recovery.local.json", **kwargs)


def test_full_arm_loading_repeat_and_reverse_restore(example, model, tmp_path):
    session = session_for(example, model, tmp_path)
    assert session.apply()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]
    assert session.apply()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]
    assert session.restore()["state"] == "RESTORED"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000, 0x3000, 0x2000, 0x1000]
    journal = read_json(session.journal_path)
    assert len(journal["originals"]) == 3
    assert journal["events"][-1]["state"] == "RESTORED"
    assert model.read_rom(0, model.profile.rom_size) == model.profile.runtime_base(model.base)


def test_arm_staging_reorder_duplicate_conflict_and_stale_session(example, model):
    profile, base, patch = example
    old, target = patch.validate(profile, base)[0x1000]
    session = "12" * 16
    model.begin(profile, session, patch.patch_id, 0x1000, target)
    for chunk in reversed(range(8)):
        model.stage(profile, session, patch.patch_id, 0x1000, target, chunk)
    model.stage(profile, session, patch.patch_id, 0x1000, target, 7)
    stale = build_stage(profile, "34" * 16, patch.patch_id, 0x1000, target, chunk=0)
    assert model.run(stale) == REFUSED
    changed = bytearray(build_stage(profile, session, patch.patch_id, 0x1000, target, chunk=7))
    changed[STAGE_DATA] ^= 1
    assert model.run(bytes(changed)) == REFUSED
    assert model.run(build_commit(profile, session, patch.patch_id, 0x1000, old, target)) == REFUSED
    assert not model.machine.rom_writes


def test_arm_missing_chunk_and_changed_target_preimage_prevent_commit(example, model):
    profile, base, patch = example
    old, target = patch.validate(profile, base)[0x1000]
    session = "12" * 16
    model.begin(profile, session, patch.patch_id, 0x1000, target)
    for chunk in range(7):
        model.stage(profile, session, patch.patch_id, 0x1000, target, chunk)
    code = build_commit(profile, session, patch.patch_id, 0x1000, old, target)
    assert model.run(code) == REFUSED
    model.stage(profile, session, patch.patch_id, 0x1000, target, 7)
    model.machine.uc.mem_write(0x08001000, b"\x99")
    assert model.run(code) == REFUSED
    assert not model.machine.rom_writes


def test_uploaded_code_executes_from_the_modified_rom_backing(example, model, tmp_path):
    session_for(example, model, tmp_path).apply()
    # str r1,[r0]; bx lr: an uploaded THUMB routine, reached through the ordinary ROM fetch path.
    run = model.run(bs.build_call(0x08002051, args=(0x02010000, 0x1234ABCD)))
    assert isinstance(run, int)
    assert model.read_memory(0x02010000, 4) == bytes.fromhex("cdab3412")


@pytest.mark.parametrize("failure", ["wrong-wrapper", "unexplained-rom", "unknown-page", "executing"])
def test_preflight_failures_happen_before_any_write(example, model, tmp_path, failure):
    if failure == "wrong-wrapper":
        model.observation = Observation(replace(model.profile.identity, wrapper_sha256="0" * 64),
                                        model.observation.executing)
    elif failure == "unexplained-rom":
        model.machine.uc.mem_write(0x0800F000, b"\x99")
    elif failure == "unknown-page":
        model.machine.uc.mem_write(0x08001000, b"\x99")
    else:
        model.observation = Observation(model.profile.identity, ((0x1000, 0x1002),))
    with pytest.raises(LocalizationError):
        session_for(example, model, tmp_path).apply()
    assert not model.machine.rom_writes
    assert not (tmp_path / "recovery.local.json").exists()


def test_interrupted_second_page_resumes_from_live_bytes(example, model, tmp_path):
    session = session_for(example, model, tmp_path, retries=0)
    commit = model.commit
    def fail_second(profile, session_id, patch_id, offset, before, target):
        if offset == 0x2000:
            raise ConnectionError("injected disconnect")
        return commit(profile, session_id, patch_id, offset, before, target)
    model.commit = fail_second
    with pytest.raises(LocalizationError, match="uncertain"):
        session.apply()
    assert model.machine.rom_writes == [0x1000]
    assert read_json(session.journal_path)["events"][-1]["state"] == "STOPPED"
    model.commit = commit
    assert session_for(example, model, tmp_path).apply()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]


def test_lost_commit_ack_is_read_back_without_rewriting(example, model, tmp_path):
    commit = model.commit
    lost = []
    def lose_once(*args):
        result = commit(*args)
        if not lost:
            lost.append(True)
            raise TimeoutError("ack lost after accepted copy")
        return result
    model.commit = lose_once
    assert session_for(example, model, tmp_path).apply()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]


def test_lost_chunk_ack_can_repeat_the_exact_chunk(example, model, tmp_path):
    stage, lost = model.stage, []
    def lose_once(*args):
        stage(*args)
        if not lost:
            lost.append(True)
            raise TimeoutError("chunk acknowledgement lost")
    model.stage = lose_once
    assert session_for(example, model, tmp_path).apply()["state"] == "COMPLETE"


def test_corrupt_staging_fails_before_a_rom_write(example, model, tmp_path):
    stage = model.stage
    def corrupt(*args):
        stage(*args)
        if args[-1] == 7:
            model.machine.uc.mem_write(model.profile.stage_address, b"\x77")
    model.stage = corrupt
    with pytest.raises(LocalizationError, match="staging SHA-256"):
        session_for(example, model, tmp_path).apply()
    assert not model.machine.rom_writes


def test_silent_syscall_refusal_is_not_reported_as_success(example, model, tmp_path):
    model.commit = lambda *args: None
    with pytest.raises(LocalizationError, match="readback mismatch"):
        session_for(example, model, tmp_path).apply()
    assert not model.machine.rom_writes


def test_neighbor_corruption_stops_before_activation(example, model, tmp_path):
    commit = model.commit
    def corrupt_neighbor(*args):
        commit(*args)
        model.machine.uc.mem_write(0x08002000, b"\x77")
    model.commit = corrupt_neighbor
    with pytest.raises(LocalizationError, match="neighbor"):
        session_for(example, model, tmp_path).apply()
    assert model.machine.rom_writes == [0x1000]


def test_soft_reset_and_full_restart_are_checked_against_current_rom(example, model, tmp_path):
    session_for(example, model, tmp_path).apply()
    model.soft_reset()
    assert session_for(example, model, tmp_path).apply()["state"] == "COMPLETE"
    assert not model.machine.rom_writes
    model.full_restart()
    assert session_for(example, model, tmp_path).apply()["state"] == "COMPLETE"
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]


def test_recovery_log_tampering_blocks_restore(example, model, tmp_path):
    session = session_for(example, model, tmp_path)
    session.apply()
    value = read_json(session.journal_path)
    value["originals"]["4096"] = "00" * PAGE_SIZE
    session.journal_path.write_bytes(canonical(value))
    with pytest.raises(LocalizationError, match="original pages"):
        session_for(example, model, tmp_path).restore()
    assert model.machine.rom_writes == [0x1000, 0x2000, 0x3000]


def test_active_entry_with_missing_dependencies_is_refused(example, model, tmp_path):
    profile, base, patch = example
    _, target = patch.validate(profile, base)[0x3000]
    model.machine.uc.mem_write(0x08003000, target)
    with pytest.raises(LocalizationError, match="dependencies"):
        session_for(example, model, tmp_path).apply()
    assert not model.machine.rom_writes


def test_guest_rejects_a_commit_retargeted_to_save_flash(example, model):
    profile, base, patch = example
    old, target = patch.validate(profile, base)[0x1000]
    session = "12" * 16
    model.begin(profile, session, patch.patch_id, 0x1000, target)
    for chunk in range(8):
        model.stage(profile, session, patch.patch_id, 0x1000, target, chunk)
    code = bytearray(build_commit(profile, session, patch.patch_id, 0x1000, old, target))
    code[16:20] = (0x0E000000).to_bytes(4, "little")
    assert model.run(bytes(code)) == REFUSED
    assert not model.machine.rom_writes and not model.machine.flash_writes


def test_missing_recovery_journal_and_durability_failure_prevent_writes(example, model, tmp_path, monkeypatch):
    session = session_for(example, model, tmp_path)
    with pytest.raises(LocalizationError, match="requires"):
        session.restore()
    from pokeldn.frlg.localization import session as session_module
    def no_space(*args):
        raise OSError("injected fsync failure")
    monkeypatch.setattr(session_module.os, "fsync", no_space)
    with pytest.raises(OSError, match="fsync"):
        session.apply()
    assert not model.machine.rom_writes


def test_existing_flash_and_generic_sloop_cannot_emit_rom_writes():
    with pytest.raises(bs.BufferScriptError):
        bs.build_flash_write(0xFA3FF, unsafe=True)
    with pytest.raises(bs.BufferScriptError):
        bs.build_sloop_svc(0x48, unsafe=True)


def test_bridge_requires_exact_read_sizes_and_measured_observations():
    transport = BufferTransport(lambda: None, lambda addr, size: b"", lambda code: STAGE_OK)
    with pytest.raises(LocalizationError, match="observed"):
        transport.observe()
    with pytest.raises(LocalizationError, match="readback"):
        transport.read_rom(0, 4096)


@needs_unicorn
def test_cli_self_test_report_is_explicitly_software_only(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools/frlg/localization_load.py"),
                             "self-test", "--output-dir", str(tmp_path / "evidence")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["loaded"]["state"] == "COMPLETE" and report["restored"]["state"] == "RESTORED"
    assert report["rom_page_writes"] == 6 and report["software_only"] is True
    assert not report["retail_verified"] and not report["chinese_rendering_verified"]


def test_cli_build_check_and_corrupt_package_refusal(example, tmp_path):
    profile, base, patch = example
    for name, value in (("profile", profile.as_dict()), ("patch", patch.as_dict())):
        (tmp_path / f"{name}.json").write_bytes(canonical(value))
    (tmp_path / "base.gba").write_bytes(base)
    edits = [e.as_dict() for p in patch.pages for e in p.edits]
    (tmp_path / "edits.json").write_bytes(canonical(edits))
    build = subprocess.run([sys.executable, str(ROOT / "tools/frlg/localization_build.py"), "build",
                            "--profile", str(tmp_path / "profile.json"), "--base", str(tmp_path / "base.gba"),
                            "--edits", str(tmp_path / "edits.json"), "--output", str(tmp_path / "built.json")],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    command = [sys.executable, str(ROOT / "tools/frlg/localization_load.py"), "check",
               "--profile", str(tmp_path / "profile.json"), "--base", str(tmp_path / "base.gba"),
               "--patch", str(tmp_path / "built.json")]
    checked = subprocess.run(command, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["state"] == "OFFLINE_VALIDATED"
    value = read_json(tmp_path / "built.json")
    value["patch_id"] = "0" * 64
    (tmp_path / "built.json").write_bytes(canonical(value))
    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode == 2 and "identifier mismatch" in refused.stderr
