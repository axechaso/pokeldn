@ BPRE rev10 Mystery Gift: show a short Chinese bitmap in the existing message window.
@ No ROM patch, flash access, save command, resident task or extra RAM allocation.
@ Verify the header, CopyWindowToVram's 64-byte fingerprint and window 1's complete
@ template before writing its allocated pixel buffer. Return an explicit refusal code.
@ Position independent in gDecompressionBuffer; callable once per frame through
@ CLI_RUN_BUFFER_SCRIPT. The client owns the code and counter until the reply.
.syntax unified
.arm
.text
.global _start, fingerprint, hold_frames, bitmap
_start:
    b       code
counter:       .word 0
fingerprint:   .word 0
hold_frames:   .word 600

code:
    push    {r0, r4-r11, lr}
    adr     r4, _start
    mov     r5, r0
    ldr     r0, [r4, #4]
    cmp     r0, #0
    bne     tick

    ldr     r0, rom_header
    ldr     r1, [r0]
    ldr     r2, game_code
    cmp     r1, r2
    bne     refuse_header
    ldrb    r1, [r0, #16]
    cmp     r1, #10
    bne     refuse_header

    ldr     r0, copy_window
    bic     r0, r0, #1
    ldr     r1, fnv_basis
    ldr     r2, fnv_prime
    mov     r3, #64
hash_loop:
    ldrb    ip, [r0], #1
    eor     r1, r1, ip
    mul     r1, r2, r1
    subs    r3, r3, #1
    bne     hash_loop
    ldr     r0, [r4, #8]
    cmp     r1, r0
    bne     refuse_code

    ldr     r0, window_one
    ldr     r1, [r0]
    ldr     r2, template_one
    cmp     r1, r2
    bne     refuse_window
    ldr     r1, [r0, #4]
    ldr     r2, template_two
    cmp     r1, r2
    bne     refuse_window
    ldr     r8, [r0, #8]
    tst     r8, #3
    bne     refuse_window
    ldr     r1, heap_low
    cmp     r8, r1
    blo     refuse_window
    ldr     r1, last_window_start
    cmp     r8, r1
    bhi     refuse_window

    @ White background, matching AddTextPrinterToWindow1's 0x11 fill.
    ldr     r0, white_pixels
    mov     r1, r8
    mov     r2, #0xe00
fill_loop:
    str     r0, [r1], #4
    subs    r2, r2, #4
    bne     fill_loop

    adr     r6, bitmap
    add     r7, r8, #0x3a0       @ tile (1,1), 28 tiles per window row
    mov     r9, #2
row_loop:
    mov     r10, #48            @ six tiles = 192 bytes per bitmap row
tile_loop:
    ldr     r0, [r6], #4
    str     r0, [r7], #4
    subs    r10, r10, #1
    bne     tile_loop
    add     r7, r7, #0x2c0      @ remaining 22 tiles in this window row
    subs    r9, r9, #1
    bne     row_loop

    mov     r0, #1
    mov     r1, #2              @ COPYWIN_GFX, original tilemap and border retained
    ldr     ip, copy_window
    mov     lr, pc
    bx      ip

tick:
    ldr     r0, [r4, #4]
    add     r0, r0, #1
    str     r0, [r4, #4]
    ldr     r1, [r4, #12]
    cmp     r0, r1
    blo     yield_frame
    ldr     r0, completed
    b       finish
refuse_header:
    ldr     r0, bad_header
    b       finish
refuse_code:
    ldr     r0, bad_code
    b       finish
refuse_window:
    ldr     r0, bad_window
finish:
    str     r0, [r5]
    mov     r0, #1
    b       restore
yield_frame:
    mov     r0, #0
restore:
    add     sp, sp, #4          @ discard saved incoming r0; keep our return value
    pop     {r4-r11, lr}
    bx      lr

.align 2
rom_header:        .word 0x080000ac
game_code:         .word 0x45525042
copy_window:       .word 0x08003ea1
fnv_basis:         .word 0x811c9dc5
fnv_prime:         .word 0x01000193
window_one:        .word 0x020204c0   @ gWindows + sizeof(struct Window)
template_one:      .word 0x1c0f0100
template_two:      .word 0x004f0f04
heap_low:          .word 0x02000000
last_window_start: .word 0x0201b200   @ entire 28x4 tile buffer must fit gHeap
white_pixels:      .word 0x11111111
completed:         .word 0x5a48444d
bad_header:        .word 0xbad00001
bad_code:          .word 0xbad00002
bad_window:        .word 0xbad00003
bitmap:
    .space 384                 @ authored 48x16, 4bpp tile data; patched by ns_display.py
