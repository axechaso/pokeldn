@ Stage one bounded chunk in the otherwise unclaimed top 1 KiB of EWRAM.
@ Both stages verify the source build and entire before/after RAM image.
@ Only the final stage installs the VBlank pointer, after all bytes validate.
@ An occupied slot or another resident hook is refused, never overwritten.
.syntax unified
.arm
.text
.global _start
_start:
    b code
before_hash: .word 0
after_hash: .word 0
full_hash: .word 0
chunk_offset: .word 0
chunk_length: .word 0
chunk_data: .word 0
guard_data: .word 0
guard_count: .word 0
entry_point: .word 0
activate: .word 0
code:
    push {r0, r4-r11, lr}
    adr r4, _start
    mov r5, r0
    ldr r0, header
    ldr r1, [r0]
    ldr r2, bpre
    cmp r1, r2
    bne bad_header
    ldrb r1, [r0, #16]
    cmp r1, #10
    bne bad_header
    ldr r6, [r4, #28]
    add r6, r4, r6
    ldr r7, [r4, #32]
guard_loop:
    ldmia r6!, {r0-r2}
    mov r8, r2
    bl hash
    cmp r0, r8
    bne bad_code
    subs r7, r7, #1
    bne guard_loop
    ldr r0, fonts
    ldr r0, [r0]
    ldr r1, rom_fonts
    cmp r0, r1
    bne check_already
    ldr r0, intr
    ldr r0, [r0]
    ldr r1, vblank
    cmp r0, r1
    beq check_before
check_already:
    ldr r0, intr
    ldr r0, [r0]
    ldr r1, [r4, #36]
    cmp r0, r1
    bne bad_slot
    ldr r0, resident
    mov r1, #1024
    bl hash
    ldr r1, [r4, #12]
    cmp r0, r1
    bne bad_slot
    ldr r0, already
    b finish
check_before:
    ldr r0, resident
    mov r1, #1024
    bl hash
    ldr r1, [r4, #4]
    cmp r0, r1
    bne bad_slot
    ldr r0, [r4, #16]
    ldr r1, [r4, #20]
    add r2, r0, r1
    cmp r1, #0
    beq bad_slot
    cmp r2, #1024
    bhi bad_slot
    orr r2, r0, r1
    tst r2, #3
    bne bad_slot
    ldr r2, resident
    add r2, r2, r0
    ldr r0, [r4, #24]
    add r0, r4, r0
copy_loop:
    ldr r3, [r0], #4
    str r3, [r2], #4
    subs r1, r1, #4
    bne copy_loop
    ldr r0, resident
    mov r1, #1024
    bl hash
    ldr r1, [r4, #8]
    cmp r0, r1
    bne bad_slot
    ldr r0, [r4, #40]
    cmp r0, #0
    ldreq r0, staged
    beq finish
    ldr r1, ime
    ldrh r2, [r1]
    mov r3, #0
    strh r3, [r1]
    ldr r0, [r4, #36]
    ldr r3, intr
    str r0, [r3]                @ activate last; no interrupt sees a partial image
    strh r2, [r1]
    ldr r0, installed
    b finish
bad_header:
    ldr r0, refused_header
    b finish
bad_code:
    ldr r0, refused_code
    b finish
bad_slot:
    ldr r0, refused_slot
finish:
    str r0, [r5]
    mov r0, #1
    add sp, sp, #4
    pop {r4-r11, lr}
    bx lr
hash:
    ldr r2, basis
    ldr r3, prime
hash_loop:
    ldrb ip, [r0], #1
    eor r2, r2, ip
    mul r2, r3, r2
    subs r1, r1, #1
    bne hash_loop
    mov r0, r2
    bx lr
.align 2
header: .word 0x080000ac
bpre: .word 0x45525042
fonts: .word 0x03003020
rom_fonts: .word 0x0841cf08
intr: .word 0x030027e0
vblank: .word 0x08000735
resident: .word 0x0203fc00
ime: .word 0x04000208
basis: .word 0x811c9dc5
prime: .word 0x01000193
staged: .word 0x5a485331
installed: .word 0x5a485332
already: .word 0x5a485341
refused_header: .word 0xbad10001
refused_code: .word 0xbad10002
refused_slot: .word 0xbad10003
