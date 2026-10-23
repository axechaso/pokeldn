"""Lossless international message parsing before a Chinese encoding is selected.

Unknown selectors have unknown arity: preserve their entire remaining tail and refuse a
rewrite. Unlike the display decoder this parser never substitutes or drops bytes.
"""

from dataclasses import dataclass

from pokeldn.frlg.text import charmap
from . import LocalizationError

# GetExtCtrlCodeLength [pret/pokefirered src/string_util.c]. The display decoder's
# legacy table has no parameters for 0x10/0x11; preserving PLAY_SE/CLEAR requires 2/1.
EXT_ARGUMENTS = {**charmap._EXT_CTRL_ARGS, 0x10: 2, 0x11: 1}


@dataclass(frozen=True)
class Token:
    kind: str
    raw: bytes
    text: str = ""


@dataclass(frozen=True)
class Message:
    tokens: tuple

    def to_bytes(self):
        return b"".join(t.raw for t in self.tokens)

    @property
    def rewrite_safe(self):
        return (any(t.kind == "end" for t in self.tokens)
                and all(t.kind not in ("unknown", "opaque", "truncated") for t in self.tokens))

    def require_rewrite_safe(self):
        if not self.rewrite_safe:
            raise LocalizationError("unknown/truncated text or missing terminator requires review")
        return self


def parse_message(data):
    data, tokens, pos = bytes(data), [], 0
    controls = {0xFA: "scroll", 0xFB: "clear", 0xFE: "newline", 0xFF: "end"}
    while pos < len(data):
        start, byte = pos, data[pos]
        pos += 1
        if byte == 0xFC:
            if pos == len(data):
                tokens.append(Token("truncated", data[start:]))
                break
            selector = data[pos]
            if selector not in EXT_ARGUMENTS:
                tokens.append(Token("opaque", data[start:]))
                break
            pos += 1 + EXT_ARGUMENTS[selector]
            tokens.append(Token("extended" if pos <= len(data) else "truncated", data[start:pos]))
        elif byte in (0xF8, 0xF9):
            # Keypad/extra-symbol selectors are multibyte, even when the selector is EOS.
            pos += 1
            tokens.append(Token("unknown" if pos <= len(data) else "truncated", data[start:pos]))
        elif byte == 0xFD:
            if pos == len(data):
                tokens.append(Token("truncated", data[start:]))
                break
            selector = data[pos]
            pos += 1
            tokens.append(Token("variable" if selector in charmap.PLACEHOLDERS else "unknown",
                                data[start:pos], charmap.PLACEHOLDERS.get(selector, "")))
        elif byte in controls:
            tokens.append(Token(controls[byte], data[start:pos]))
            if byte == 0xFF:
                if pos < len(data):
                    tokens.append(Token("tail", data[pos:]))
                break
        elif byte in charmap._DEC:
            tokens.append(Token("glyph", data[start:pos], charmap._DEC[byte]))
        else:
            tokens.append(Token("unknown", data[start:pos]))
    return Message(tuple(tokens))
