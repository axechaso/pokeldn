@ Dedicated localization page staging; no ROM or save writes.
@ Header: session[16], patch SHA-256[32], page offset, bitmap, conflict, FNV-1a.
@ Eight fixed 512-byte chunks. A conflicting duplicate poisons this page until begin.
@ Operands are validated against an evidenced Profile before this image is emitted.
    .arm
    .text
    .global _start
_start:
    b .Lcode
    .word 0                       @ +4 mode: 0 begin, 1 chunk
    .word 0                       @ +8 stage address
    .word 0                       @ +12 state address
    .word 0                       @ +16 chunk number
.Lheader:                         @ +20 (64 bytes)
    .space 64
.Ldata:                           @ +84 (512 bytes)
    .space 512
.Lcode:
    push {r4-r11, lr}
    mov r11, r0
    adr r10, _start
    ldr r4, [r10, #8]
    ldr r5, [r10, #12]
    ldr r3, .Lewram
    cmp r4, r3
    blo .Lfail
    cmp r5, r3
    blo .Lfail
    add r2, r3, #0x40000
    add r1, r4, #0x1000
    cmp r1, r2
    bhi .Lfail
    add r1, r5, #64
    cmp r1, r2
    bhi .Lfail
    orr r1, r4, r5
    tst r1, #3
    bne .Lfail
    ldr r3, [r10, #4]
    adr r6, .Lheader
    cmp r3, #0
    beq .Lbegin
    cmp r3, #1
    bne .Lfail
    mov r7, #0
.Ltag:
    ldr r8, [r6, r7]
    ldr r9, [r5, r7]
    cmp r8, r9
    bne .Lfail
    add r7, r7, #4
    cmp r7, #52
    blo .Ltag
    ldr r7, [r5, #56]
    cmp r7, #0
    bne .Lfail
    ldr r7, [r10, #16]
    cmp r7, #8
    bhs .Lfail
    mov r8, #1
    mov r8, r8, lsl r7
    add r4, r4, r7, lsl #9
    adr r6, .Ldata
    ldr r9, [r5, #52]
    tst r9, r8
    mov r7, #0
    bne .Lduplicate
.Lcopy:
    ldrb r1, [r6, r7]
    strb r1, [r4, r7]
    add r7, r7, #1
    cmp r7, #512
    blo .Lcopy
    orr r9, r9, r8
    str r9, [r5, #52]
    b .Lok
.Lduplicate:
    ldrb r1, [r6, r7]
    ldrb r2, [r4, r7]
    cmp r1, r2
    bne .Lconflict
    add r7, r7, #1
    cmp r7, #512
    blo .Lduplicate
    b .Lok
.Lconflict:
    mov r1, #1
    str r1, [r5, #56]
    b .Lfail
.Lbegin:
    mov r7, #0
.Linit:
    ldr r8, [r6, r7]
    str r8, [r5, r7]
    add r7, r7, #4
    cmp r7, #64
    blo .Linit
.Lok:
    ldr r1, .Lokvalue
    b .Lreturn
.Lfail:
    ldr r1, .Lerror
.Lreturn:
    str r1, [r11]
    mov r0, #1
    pop {r4-r11, lr}
    bx lr
.Lewram: .word 0x02000000
.Lokvalue: .word 0x5a480001
.Lerror: .word 0x5a48ffff
