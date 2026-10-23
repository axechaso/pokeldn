# Prototype bitmap glyphs

`demo-font.json` is a modified bitmap subset named **FRLG Prototype Glyphs**.
The glyph data is under the SIL Open Font License 1.1 in [OFL.txt](OFL.txt),
separately from the surrounding AGPL project code.

Original font copyright: © 2014-2021 Adobe (http://www.adobe.com/).

Source: [Noto Sans CJK SC Regular](https://github.com/notofonts/noto-cjk/blob/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf),
commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c`.
Source SHA-256: `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`.

Regeneration uses `scripts/gen_localization_demo_font.py SOURCE.otf` with Pillow 12.3.0.
The generator verifies the source hash and preserves copyright metadata. The NPC story
asset is regenerated with `--text scratchpad/ns-story-text.txt --size 14 --stroke-width 0`.
It uses 16x16 cells with baseline 13, quantizes four pixel values, and packs four pixels
per byte, lowest two bits first, in row order. The resident story hook keeps source
levels 2 and 3 as ink (`GLYPH_INK_THRESHOLD = 2`) when it converts the nine Chinese
glyphs to 1bpp; dropping the lightest antialias level keeps neighboring strokes separate
at the GBA's native scale. The game still uses its fixed 14-pixel character advance.
No complete source font is bundled.

This is an offline asset prototype. Glyph indices have no assigned Chinese game encoding,
and the asset does not establish a compatible ROM text/render hook.
