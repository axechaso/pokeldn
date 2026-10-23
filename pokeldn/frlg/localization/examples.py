"""Small synthetic fixture for a reproducible software-only smoke test. No game assets."""

from . import PAGE_SIZE, sha256
from .patch import Edit, build_patch
from .profiles import Profile


def synthetic_example():
    base = bytearray((i * 37 + 11) % 256 for i in range(16 * PAGE_SIZE))
    base[0xAC:0xB0], base[0xBC] = b"BPRE", 0x0A
    profile = Profile.parse({
        "format_version": 1, "profile_id": "synthetic-bpre-0a-software-test",
        "identity": {"game_code": "BPRE", "revision": 10, "title_id": "0000000000000000",
                     "update_version": "synthetic", "wrapper_sha256": sha256(b"model, not a wrapper")},
        "rom_size": len(base), "base_sha256": sha256(base),
        "allowed": [{"start": PAGE_SIZE, "end": 12 * PAGE_SIZE, "evidence": "synthetic fixture"}],
        "protected": [{"start": 0, "end": PAGE_SIZE, "evidence": "synthetic header"}],
        "startup_changes": [{"offset": 13 * PAGE_SIZE, "before": base[13 * PAGE_SIZE:13 * PAGE_SIZE + 2].hex(),
                             "after": "52be"}],
        "staging": {"stage_address": 0x0201C400, "state_address": 0x0201D400,
                    "available": {"start": 0x0201C400, "end": 0x0201D440,
                                  "evidence": "reserved only in the synthetic model"},
                    "protected": [{"start": 0x0203FC00, "end": 0x02040000,
                                   "evidence": "model reservation for Resident/RNG"}]},
        "evidence": "Synthetic model fixture. Never use these addresses as a retail profile.",
    })
    edits = [Edit(0x1010, bytes(base[0x1010:0x1014]), bytes.fromhex("01607047"), "resource"),
             Edit(0x2050, bytes(base[0x2050:0x2054]), bytes.fromhex("01607047"), "code"),
             Edit(0x3030, bytes(base[0x3030:0x3032]), bytes.fromhex("0148"), "activation")]
    patch = build_patch(profile, bytes(base), edits,
                        resources={"font": None, "translated_messages": 0},
                        compatibility={"backend": "synthetic-unicorn-model", "retail_verified": False})
    return profile, bytes(base), patch
