"""Explicit build evidence, byte permissions and non-overlapping staging layouts.

ROM ranges are file offsets; RAM ranges are guest addresses. A profile is local research
configuration, not proof that the named retail device has passed the hardware gate.
"""

from dataclasses import dataclass

from pokeldn.frlg.rom import builds
from . import (LocalizationError, PAGE_SIZE, array, canonical, digest, fields, integer, sha256,
               unhex)


@dataclass(frozen=True)
class Region:
    start: int
    end: int
    evidence: str

    @classmethod
    def parse(cls, value):
        fields(value, ("start", "end", "evidence"))
        start = integer(value["start"], "region start")
        end = integer(value["end"], "region end", start + 1)
        if not isinstance(value["evidence"], str) or not value["evidence"].strip():
            raise LocalizationError("each region needs address evidence")
        return cls(start, end, value["evidence"])

    def contains(self, start, end):
        return self.start <= start < end <= self.end

    def overlaps(self, start, end):
        return self.start < end and start < self.end


@dataclass(frozen=True)
class StartupChange:
    offset: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class Identity:
    game_code: str
    revision: int
    title_id: str
    update_version: str
    wrapper_sha256: str

    @classmethod
    def parse(cls, value):
        fields(value, ("game_code", "revision", "title_id", "update_version", "wrapper_sha256"))
        if value["game_code"] not in builds.BUILDS:
            raise LocalizationError("unknown game code")
        integer(value["revision"], "revision", 0, 255)
        if value["revision"] != 0x0A:
            raise LocalizationError("no localization address evidence for this ROM revision")
        title = value["title_id"]
        if not isinstance(title, str) or len(title) != 16 or any(
                c not in "0123456789abcdef" for c in title):
            raise LocalizationError("title_id must be 16 lowercase hex digits")
        if not isinstance(value["update_version"], str) or not value["update_version"].strip():
            raise LocalizationError("a known Switch update version is required")
        digest(value["wrapper_sha256"], "wrapper_sha256")
        return cls(**value)


@dataclass(frozen=True)
class Profile:
    profile_id: str
    identity: Identity
    rom_size: int
    base_sha256: str
    allowed: tuple
    protected: tuple
    startup_changes: tuple
    stage_address: int
    state_address: int
    available: Region
    ram_protected: tuple
    evidence: str

    @classmethod
    def parse(cls, value):
        fields(value, ("format_version", "profile_id", "identity", "rom_size", "base_sha256",
                       "allowed", "protected", "startup_changes", "staging", "evidence"))
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            raise LocalizationError("unsupported profile format_version")
        if not isinstance(value["profile_id"], str) or not value["profile_id"].strip():
            raise LocalizationError("profile_id is required")
        if not isinstance(value["evidence"], str) or not value["evidence"].strip():
            raise LocalizationError("profile evidence is required")
        size = integer(value["rom_size"], "rom_size", PAGE_SIZE, 0x1000000)
        if size % PAGE_SIZE or size & (size - 1):
            raise LocalizationError("ROM size must be a page-aligned power of two, at most 16 MiB")
        identity = Identity.parse(value["identity"])
        digest(value["base_sha256"], "base_sha256")
        allowed = tuple(Region.parse(x) for x in array(value["allowed"], "allowed"))
        protected = tuple(Region.parse(x) for x in array(value["protected"], "protected"))
        for group in (allowed, protected):
            for i, region in enumerate(group):
                if region.end > size or any(r.overlaps(region.start, region.end) for r in group[:i]):
                    raise LocalizationError("ROM regions overlap or exceed the image")
        if not allowed:
            raise LocalizationError("no evidenced ROM changes are allowed")
        changes = []
        for entry in array(value["startup_changes"], "startup_changes"):
            fields(entry, ("offset", "before", "after"))
            before, after = unhex(entry["before"], "startup before"), unhex(entry["after"], "startup after")
            offset = integer(entry["offset"], "startup offset", 0, size - 1)
            if not before or len(before) != len(after) or offset + len(before) > size:
                raise LocalizationError("startup change must fit the ROM without resizing")
            if any(c.offset < offset + len(before) and offset < c.offset + len(c.before)
                   for c in changes):
                raise LocalizationError("overlapping startup changes")
            changes.append(StartupChange(offset, before, after))
        staging = value["staging"]
        fields(staging, ("stage_address", "state_address", "available", "protected"))
        available = Region.parse(staging["available"])
        ram_protected = tuple(Region.parse(x) for x in array(staging["protected"], "staging protected"))
        stage, state = (integer(staging[k], k) for k in ("stage_address", "state_address"))
        if not 0x02000000 <= available.start < available.end <= 0x02040000:
            raise LocalizationError("staging must have an evidenced EWRAM allocation")
        spans = ((stage, stage + PAGE_SIZE), (state, state + 64))
        for start, end in spans:
            if start % 4 or not available.contains(start, end):
                raise LocalizationError("staging/state must fit the allocation and be word aligned")
            # The client copies the whole 1 KiB, even for shorter scripts.
            if start < 0x0201C400 and 0x0201C000 < end or any(
                    r.overlaps(start, end) for r in ram_protected):
                raise LocalizationError("staging/state overlaps payload or protected RAM")
        if stage < state + 64 and state < stage + PAGE_SIZE:
            raise LocalizationError("staging data overlaps its state")
        return cls(value["profile_id"], identity, size, value["base_sha256"], allowed,
                   protected, tuple(changes), stage, state, available, ram_protected, value["evidence"])

    def as_dict(self):
        def region(r):
            return {"start": r.start, "end": r.end, "evidence": r.evidence}
        return {"format_version": 1, "profile_id": self.profile_id,
                "identity": dict(vars(self.identity)), "rom_size": self.rom_size,
                "base_sha256": self.base_sha256, "allowed": [region(r) for r in self.allowed],
                "protected": [region(r) for r in self.protected],
                "startup_changes": [{"offset": c.offset, "before": c.before.hex(),
                                     "after": c.after.hex()} for c in self.startup_changes],
                "staging": {"stage_address": self.stage_address, "state_address": self.state_address,
                            "available": region(self.available),
                            "protected": [region(r) for r in self.ram_protected]},
                "evidence": self.evidence}

    @property
    def fingerprint(self):
        return sha256(canonical(self.as_dict()))

    def validate_base(self, rom):
        if len(rom) != self.rom_size or sha256(rom) != self.base_sha256:
            raise LocalizationError("base ROM size/SHA-256 does not match profile")
        if rom[0xAC:0xB0] != self.identity.game_code.encode("ascii") or rom[0xBC] != self.identity.revision:
            raise LocalizationError("base ROM game code/revision does not match profile")
        for change in self.startup_changes:
            if rom[change.offset:change.offset + len(change.before)] != change.before:
                raise LocalizationError("unexplained wrapper startup change")

    def runtime_base(self, rom):
        self.validate_base(rom)
        result = bytearray(rom)
        for change in self.startup_changes:
            result[change.offset:change.offset + len(change.after)] = change.after
        return bytes(result)

    def check_edit(self, offset, length):
        end = offset + length
        if not any(r.contains(offset, end) for r in self.allowed):
            raise LocalizationError("edit is outside allowed ROM ranges")
        if offset < 0xC0 or any(r.overlaps(offset, end) for r in self.protected):
            raise LocalizationError("edit touches a protected ROM range")
        for change in self.startup_changes:
            if offset < change.offset + len(change.before) and change.offset < end:
                raise LocalizationError("edit touches a wrapper startup patch")

    def check_page(self, offset):
        integer(offset, "page offset", 0, self.rom_size - PAGE_SIZE)
        if offset % PAGE_SIZE:
            raise LocalizationError("page offset must be 4 KiB aligned")
        if not any(r.overlaps(offset, offset + PAGE_SIZE) for r in self.allowed):
            raise LocalizationError("page is outside allowed ROM ranges")
        # A full-page write can interrupt communications even when its hunks are elsewhere.
        build = builds.for_game_code(self.identity.game_code)
        client_page = (build.client_run_buffer_script - 0x08000000) & ~(PAGE_SIZE - 1)
        if offset in (0, client_page) or any(r.overlaps(offset, offset + PAGE_SIZE) for r in self.protected):
            raise LocalizationError("page overlaps communication/protected code")
