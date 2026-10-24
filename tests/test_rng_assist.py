"""Build-time checks for the read-only RNG resident assistant draft."""

import pytest

from pokeldn.frlg.rom import buffer_script as bs
from pokeldn.frlg.rom import builds
from pokeldn.frlg.rom import native_script as ns
from pokeldn.frlg.rom.resident_stubs import STUBS


SYMBOLS = STUBS["rng-assist"][2]


def _word(blob, name):
    at = SYMBOLS[name]
    return int.from_bytes(blob[at:at + 4], "little")


@pytest.mark.parametrize("code", ("BPRF", "BPGF", "BPRE", "BPGE"))
def test_rng_assist_patches_each_build_and_fits(code):
    blob, entry, original = bs.resident_blob("rng-assist", build=code)
    build = builds.BUILDS[code]

    assert entry == SYMBOLS["rng_assist_hook"]
    assert original == SYMBOLS["p_original"]
    assert _word(blob, "p_rng") == build.rng
    assert _word(blob, "p_gmain") == build.gmain
    assert _word(blob, "p_cb1_overworld") == build.cb1_overworld | 1
    assert _word(blob, "p_cb2_overworld") == build.cb2_overworld | 1
    assert _word(blob, "p_original") == build.vblank_intr | 1
    assert _word(blob, "p_input_shim") == ns.RESIDENT_BASE + SYMBOLS["rng_assist_input_shim"]
    assert len(bs.build_install_resident("rng-assist", build=code)) <= bs.MAX_BUFFER_SCRIPT_SIZE


def test_rng_assist_input_shim_follows_custom_destination():
    destination = 0x0203FD00
    blob, _, _ = bs.resident_blob("rng-assist", build="BPRE", dest=destination)
    assert _word(blob, "p_input_shim") == destination + SYMBOLS["rng_assist_input_shim"]


def test_rng_assist_state_reserves_the_whole_record():
    assert bs.RESIDENT_DATA["p_state"] == 64
    with pytest.raises(bs.BufferScriptError, match="runs into its state"):
        bs.build_install_resident("rng-assist", dest=0x0203FD00)
