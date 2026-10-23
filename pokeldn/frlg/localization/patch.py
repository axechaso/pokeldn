"""Deterministic, ROM-free differential packages. Only edited bytes are distributed."""

from dataclasses import dataclass

from . import (LocalizationError, PAGE_SIZE, array, canonical, digest, fields, integer,
               sha256, unhex)

KINDS = ("resource", "code", "activation")


@dataclass(frozen=True)
class Edit:
    offset: int
    before: bytes
    after: bytes
    kind: str = "resource"

    @classmethod
    def parse(cls, value):
        fields(value, ("offset", "before", "after", "kind"))
        offset = integer(value["offset"], "edit offset")
        before, after = unhex(value["before"], "before"), unhex(value["after"], "after")
        if not before or len(before) != len(after) or before == after:
            raise LocalizationError("an edit needs different, nonempty, equally sized bytes")
        if value["kind"] not in KINDS:
            raise LocalizationError("unknown page kind")
        return cls(offset, before, after, value["kind"])

    def as_dict(self):
        return {"offset": self.offset, "before": self.before.hex(),
                "after": self.after.hex(), "kind": self.kind}


@dataclass(frozen=True)
class Page:
    offset: int
    kind: str
    before_sha256: str
    after_sha256: str
    edits: tuple

    def as_dict(self):
        return {"offset": self.offset, "kind": self.kind, "before_sha256": self.before_sha256,
                "after_sha256": self.after_sha256, "edits": [e.as_dict() for e in self.edits]}


@dataclass(frozen=True)
class Patch:
    profile_id: str
    profile_sha256: str
    base_sha256: str
    pages: tuple
    resources: dict
    compatibility: dict

    def body(self):
        return {"format_version": 1, "profile_id": self.profile_id,
                "profile_sha256": self.profile_sha256, "base_sha256": self.base_sha256,
                "pages": [p.as_dict() for p in self.pages], "resources": self.resources,
                "compatibility": self.compatibility,
                "activation": [p.offset for p in self.pages if p.kind == "activation"]}

    @property
    def patch_id(self):
        return sha256(canonical(self.body()))

    def as_dict(self):
        return {**self.body(), "patch_id": self.patch_id}

    @classmethod
    def parse(cls, value):
        fields(value, ("format_version", "profile_id", "profile_sha256", "base_sha256", "pages",
                       "resources", "compatibility", "activation", "patch_id"))
        if type(value["format_version"]) is not int or value["format_version"] != 1:
            raise LocalizationError("unsupported patch format_version")
        for name in ("profile_sha256", "base_sha256", "patch_id"):
            digest(value[name], name)
        if not isinstance(value["resources"], dict) or not isinstance(value["compatibility"], dict):
            raise LocalizationError("resources and compatibility must be objects")
        pages = []
        for page in array(value["pages"], "pages"):
            fields(page, ("offset", "kind", "before_sha256", "after_sha256", "edits"))
            digest(page["before_sha256"], "before_sha256")
            digest(page["after_sha256"], "after_sha256")
            offset = integer(page["offset"], "page offset")
            if offset % PAGE_SIZE or page["kind"] not in KINDS:
                raise LocalizationError("invalid page offset/kind")
            edits = tuple(Edit.parse(x) for x in array(page["edits"], "edits"))
            if not edits:
                raise LocalizationError("empty patch page")
            previous_end = offset
            for edit in edits:
                if edit.kind != page["kind"] or not offset <= edit.offset < edit.offset + len(edit.before) <= offset + PAGE_SIZE:
                    raise LocalizationError("an edit crosses its page or changes its kind")
                if edit.offset < previous_end:
                    raise LocalizationError("overlapping or unsorted edits")
                previous_end = edit.offset + len(edit.before)
            pages.append(Page(offset, page["kind"], page["before_sha256"], page["after_sha256"], edits))
        if not pages or len({p.offset for p in pages}) != len(pages):
            raise LocalizationError("empty package or duplicate page offsets")
        if pages != sorted(pages, key=lambda p: (KINDS.index(p.kind), p.offset)):
            raise LocalizationError("resources and code must precede activation pages")
        if value["activation"] != [p.offset for p in pages if p.kind == "activation"]:
            raise LocalizationError("activation order does not match pages")
        patch = cls(value["profile_id"], value["profile_sha256"], value["base_sha256"],
                    tuple(pages), value["resources"], value["compatibility"])
        if patch.patch_id != value["patch_id"]:
            raise LocalizationError("patch content identifier mismatch")
        return patch

    def validate(self, profile, base):
        # Revalidate even programmatically constructed packages.
        Patch.parse(self.as_dict())
        profile.validate_base(base)
        if (self.profile_id != profile.profile_id or self.profile_sha256 != profile.fingerprint
                or self.base_sha256 != profile.base_sha256):
            raise LocalizationError("patch targets another profile/base ROM")
        runtime = profile.runtime_base(base)
        result = {}
        for page in self.pages:
            profile.check_page(page.offset)
            original = runtime[page.offset:page.offset + PAGE_SIZE]
            target = bytearray(original)
            for edit in page.edits:
                profile.check_edit(edit.offset, len(edit.before))
                at = edit.offset - page.offset
                if original[at:at + len(edit.before)] != edit.before:
                    raise LocalizationError("edit preimage mismatch")
                target[at:at + len(edit.after)] = edit.after
            if sha256(original) != page.before_sha256 or sha256(target) != page.after_sha256:
                raise LocalizationError("page SHA-256 mismatch")
            result[page.offset] = (original, bytes(target))
        return result


def build_patch(profile, base, edits, *, resources=None, compatibility=None):
    """Split edits at page boundaries, merge against runtime originals, then hash every page."""
    profile.validate_base(base)
    runtime = profile.runtime_base(base)
    groups, last_end = {}, -1
    for edit in sorted(edits, key=lambda e: e.offset):
        edit = Edit.parse(edit.as_dict())
        profile.check_edit(edit.offset, len(edit.before))
        if edit.offset < last_end:
            raise LocalizationError("overlapping edits")
        last_end = edit.offset + len(edit.before)
        if runtime[edit.offset:last_end] != edit.before:
            raise LocalizationError("edit preimage mismatch")
        pos = 0
        while pos < len(edit.before):
            offset = edit.offset + pos
            page_at = offset & ~(PAGE_SIZE - 1)
            count = min(len(edit.before) - pos, PAGE_SIZE - offset % PAGE_SIZE)
            part = Edit(offset, edit.before[pos:pos + count], edit.after[pos:pos + count], edit.kind)
            if part.before != part.after:
                groups.setdefault(page_at, []).append(part)
            pos += count
    pages = []
    for offset, parts in groups.items():
        if len({e.kind for e in parts}) != 1:
            raise LocalizationError("activation, code and resources need separate pages")
        original = runtime[offset:offset + PAGE_SIZE]
        target = bytearray(original)
        for edit in parts:
            at = edit.offset - offset
            target[at:at + len(edit.after)] = edit.after
        pages.append(Page(offset, parts[0].kind, sha256(original), sha256(target), tuple(parts)))
    patch = Patch(profile.profile_id, profile.fingerprint, profile.base_sha256,
                  tuple(sorted(pages, key=lambda p: (KINDS.index(p.kind), p.offset))),
                  resources or {}, compatibility or {})
    patch.validate(profile, base)
    return patch
