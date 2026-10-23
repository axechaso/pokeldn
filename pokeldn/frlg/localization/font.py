"""Small, licensed 16x16 bitmap subsets. Asset format is explicit, independent of game encoding."""

from dataclasses import dataclass

from . import LocalizationError, canonical, digest, fields, integer, sha256, unhex


def pack_pixels(pixels):
    pixels = bytes(pixels)
    if len(pixels) != 256 or any(x > 3 for x in pixels):
        raise LocalizationError("a glyph is 256 pixels with values 0..3")
    return bytes(sum(pixels[i + j] << (j * 2) for j in range(4)) for i in range(0, 256, 4))


def unpack_pixels(packed):
    if len(packed) != 64:
        raise LocalizationError("a 16x16 2bpp glyph needs 64 bytes")
    return bytes((value >> (j * 2)) & 3 for value in packed for j in range(4))


@dataclass(frozen=True)
class Glyph:
    advance: int
    packed: bytes


@dataclass(frozen=True)
class BitmapFont:
    name: str
    source: dict
    glyphs: dict

    @classmethod
    def parse(cls, value):
        fields(value, ("format_version", "name", "source", "pixel_format", "glyphs", "font_id"))
        if (type(value["format_version"]) is not int or value["format_version"] != 1
                or value["pixel_format"] != "16x16-2bpp-row-major-lsb"):
            raise LocalizationError("unsupported bitmap font format")
        if not isinstance(value["name"], str) or not value["name"].strip():
            raise LocalizationError("font name is required")
        source = value["source"]
        fields(source, ("url", "commit", "sha256", "license", "copyright", "rasterizer"))
        digest(source["sha256"], "source font SHA-256")
        if (source["license"] != "OFL-1.1" or not isinstance(source["copyright"], list)
                or not source["copyright"] or any(not isinstance(x, str) or not x.strip()
                                                for x in source["copyright"])):
            raise LocalizationError("bitmap source must carry its font license and copyright")
        if (not isinstance(source["url"], str) or not source["url"].startswith("https://")
                or not isinstance(source["commit"], str) or len(source["commit"]) != 40
                or any(c not in "0123456789abcdef" for c in source["commit"])
                or not isinstance(source["rasterizer"], str) or not source["rasterizer"].strip()):
            raise LocalizationError("font source needs a pinned commit, URL and rasterizer")
        if not isinstance(value["glyphs"], dict) or not value["glyphs"]:
            raise LocalizationError("font needs a glyph map")
        glyphs = {}
        for char, entry in value["glyphs"].items():
            if len(char) != 1 or ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
                raise LocalizationError("invalid Unicode glyph key")
            fields(entry, ("advance", "pixels"))
            advance = integer(entry["advance"], "glyph advance", 1, 16)
            packed = unhex(entry["pixels"], "glyph pixels")
            unpack_pixels(packed)
            glyphs[char] = Glyph(advance, packed)
        font = cls(value["name"], source, glyphs)
        if value["font_id"] != font.font_id:
            raise LocalizationError("bitmap font content identifier mismatch")
        return font

    def body(self):
        return {"format_version": 1, "name": self.name, "source": self.source,
                "pixel_format": "16x16-2bpp-row-major-lsb",
                "glyphs": {char: {"advance": glyph.advance, "pixels": glyph.packed.hex()}
                           for char, glyph in sorted(self.glyphs.items())}}

    @property
    def font_id(self):
        return sha256(canonical(self.body()))

    def as_dict(self):
        return {**self.body(), "font_id": self.font_id}

    def subset(self, chars):
        chars = set(chars)
        missing = chars - self.glyphs.keys()
        if missing:
            raise LocalizationError("missing glyphs: " + "".join(sorted(missing)))
        return BitmapFont(self.name, self.source, {c: self.glyphs[c] for c in sorted(chars)})

    def binary(self):
        return b"".join(self.glyphs[c].packed for c in sorted(self.glyphs))
