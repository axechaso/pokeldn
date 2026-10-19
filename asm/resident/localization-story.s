@ English FireRed rev10: one NPC sentence through the game's ordinary text printer.
@ Only FONT_NORMAL / FONT_MALE are routed here. Other strings and all native
@ control states use the original function. Chinese glyphs ask it to consume the
@ no-op RESET_FONT control first, retaining its delay and A/B acceleration.
@ No field script, story flag, save block or ROM is changed.
.syntax unified
.thumb
.align 2
.global story_hook, story_font, p_fonts, p_rom_fonts, p_ram_fonts, p_vblank
.global p_original_font, p_copy_glyph, p_string, p_tail, p_text_hash
.global p_original_normal
.global p_prefix, p_prefix_end, p_glyphs, p_glyph_info, font_table, glyphs
.global prefix, prefix_end, pace
story_hook:
    ldr r0, p_fonts
    ldr r1, [r0]
    ldr r2, p_rom_fonts
    cmp r1, r2
    bne 1f
    ldr r1, p_ram_fonts
    str r1, [r0]
1:  ldr r0, p_vblank
    bx r0

story_font:
    push {r4-r7, lr}
    sub sp, #12                 @ 32-byte frame, ABI aligned at native calls
    movs r4, r0
    ldr r5, [r4]
    ldr r0, p_string
    cmp r5, r0
    bne .Lprefix
    ldrb r0, [r4, #4]           @ field message window 0 only
    cmp r0, #0
    bne .Lnative
    ldrb r0, [r4, #0x1c]
    cmp r0, #0
    bne .Lnative
    movs r0, r5
    movs r1, #89                @ entire source-matched NPC message, including EOS
    bl .Lhash
    ldr r1, p_text_hash
    cmp r0, r1
    bne .Lnative
    ldr r5, p_prefix
    str r5, [r4]
.Lprefix:
    ldr r0, p_prefix_end
    cmp r5, r0
    bne .Linside
    ldr r5, p_tail              @ native second page, including native clear/wait
    str r5, [r4]
    b .Lnative
.Linside:
    ldr r0, p_prefix
    cmp r5, r0
    blo .Lnative
    ldr r0, p_prefix_end
    cmp r5, r0
    bhs .Lnative
    ldrb r0, [r4, #0x1c]
    cmp r0, #0
    bne .Lnative
    ldrb r0, [r5]
    cmp r0, #0xf9
    beq .Lchinese
.Lnative:
    bl .Lnative_font
.Lreturn:
    add sp, #12
    pop {r4-r7}
    pop {r3}
    bx r3
.Lcall:
    bx r3
.Lnative_font:
    ldrb r0, [r4, #5]
    ldr r3, p_original_font
    cmp r0, #2
    bne 5f
    ldr r3, p_original_normal
5:  movs r0, r4
    bx r3

.Lchinese:
    adr r6, pace
    str r6, [r4]
    bl .Lnative_font
    str r5, [r4]               @ restore the real stream even when pacing yields
    cmp r0, #2                 @ RENDER_REPEAT means RESET_FONT was consumed
    bne .Lreturn
    ldrb r0, [r5, #1]
    subs r0, #0x40
    cmp r0, #8
    bhi .Lfinish               @ never read outside the nine-glyph subset
    lsls r0, r0, #5
    ldr r1, p_glyphs
    adds r5, r1, r0
    ldr r0, [r4]
    adds r0, #2
    str r0, [r4]

    @ Four foreground/background pixel pairs, using this printer's own palette.
    ldrb r0, [r4, #12]
    lsrs r0, r0, #4
    ldrb r1, [r4, #13]
    movs r2, #15
    ands r1, r2
    mov r3, sp
    adds r3, #8
    lsls r2, r1, #4
    orrs r1, r2
    strb r1, [r3]
    orrs r2, r0
    strb r2, [r3, #1]
    lsls r2, r0, #4
    orrs r0, r2
    strb r0, [r3, #3]
    movs r0, #15
    ands r1, r0
    orrs r1, r2
    strb r1, [r3, #2]

    ldr r6, p_glyph_info
    movs r7, #16
.Lrow:
    ldrh r0, [r5]
    adds r5, #2
    movs r1, #8
.Lpair:
    movs r2, #3
    ands r2, r0
    mov r3, sp
    adds r3, #8
    ldrb r3, [r3, r2]
    strb r3, [r6]
    adds r6, #1
    lsrs r0, r0, #2
    subs r1, #1
    cmp r1, #4
    bne 2f
    adds r6, #28               @ right tile in the current tile row
2:  cmp r1, #0
    bne .Lpair
    subs r6, #32
    subs r7, #1
    cmp r7, #8
    bne 3f
    adds r6, #32               @ lower tile row
3:  cmp r7, #0
    bne .Lrow
    ldr r0, p_glyph_info
    adds r0, #128
    ldr r1, .Ldimensions
    strh r1, [r0]
    movs r0, r4
    ldr r3, p_copy_glyph
    bl .Lcall
    ldrb r0, [r4, #8]
    adds r0, #14
    strb r0, [r4, #8]
    movs r0, #0                @ RENDER_PRINT: native RunTextPrinters uploads it
    b .Lreturn
.Lfinish:
    movs r0, #1
    b .Lreturn

.Lhash:
    push {r4-r5, lr}
    movs r4, r0
    ldr r0, .Lbasis
    ldr r3, .Lprime
4:  ldrb r2, [r4]
    adds r4, #1
    eors r0, r2
    muls r0, r3
    subs r1, #1
    bne 4b
    pop {r4-r5}
    pop {r2}
    bx r2

.align 2
.Lbasis: .word 0x811c9dc5
.Lprime: .word 0x01000193
.Ldimensions: .word 0x100e
p_fonts: .word 0x03003020
p_rom_fonts: .word 0x0841cf08
p_ram_fonts: .word 0
p_vblank: .word 0x08000735
p_original_font: .word 0x08008df1
p_original_normal: .word 0x08008d81
p_copy_glyph: .word 0x08002f95
p_string: .word 0x02021d18
p_tail: .word 0
p_text_hash: .word 0
p_prefix: .word 0
p_prefix_end: .word 0
p_glyphs: .word 0
p_glyph_info: .word 0x03003030
pace: .byte 0xfc, 7
prefix:
    .byte 0xf9, 0x40, 0xf9, 0x41, 0xf9, 0x42, 0xf9, 0x43
    .byte 0xf9, 0x44, 0xf9, 0x45, 0xf9, 0x46, 0xf9, 0x47
    .byte 0xf9, 0x48, 0xfb
prefix_end:
.align 2
font_table: .space 96
glyphs: .space 288
