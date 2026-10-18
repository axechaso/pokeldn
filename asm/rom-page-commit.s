@ Dedicated ROM-page commit. Only swi 0x48; never 0x56.
@ Full session/patch/page tags, bitmap, sticky conflict and source/destination FNV checked.
@ Host SHA-256 readback is still mandatory: swi 0x48 returns no status.
    .arm
    .text
    .global _start
_start:
    b .Lcode
    .word 0                       @ +4 stage address
    .word 0                       @ +8 state address
    .word 0                       @ +12 exact ROM size (<=16 MiB)
    .word 0                       @ +16 ROM destination
    .word 0                       @ +20 expected destination FNV-1a
.Lheader:                         @ +24 (64 bytes)
    .space 64
.Lcode:
    push {r4-r11, lr}
    mov r11, r0
    adr r10, _start
    ldr r4, [r10, #4]
    ldr r5, [r10, #8]
    ldr r6, [r10, #16]
    ldr r1, .Lewram
    cmp r4, r1
    blo .Lfail
    cmp r5, r1
    blo .Lfail
    add r2, r1, #0x40000
    add r3, r4, #0x1000
    cmp r3, r2
    bhi .Lfail
    add r3, r5, #64
    cmp r3, r2
    bhi .Lfail
    orr r1, r4, r5
    tst r1, #3
    bne .Lfail
    ldr r1, .Lrom
    cmp r6, r1
    blo .Lfail
    sub r2, r6, r1
    mov r3, r2, lsl #20
    cmp r3, #0
    bne .Lfail
    ldr r3, [r10, #12]
    cmp r3, #0x1000000
    bhi .Lfail
    add r7, r2, #0x1000
    cmp r7, r3
    bhi .Lfail
    adr r7, .Lheader
    ldr r3, [r7, #48]
    cmp r2, r3
    bne .Lfail
    mov r2, #0
.Ltag:
    ldr r8, [r7, r2]
    ldr r9, [r5, r2]
    cmp r8, r9
    bne .Lfail
    add r2, r2, #4
    cmp r2, #52
    blo .Ltag
    ldr r2, [r5, #52]
    cmp r2, #255
    bne .Lfail
    ldr r2, [r5, #56]
    cmp r2, #0
    bne .Lfail
    ldr r8, [r5, #60]
    ldr r9, [r7, #60]
    cmp r8, r9
    bne .Lfail
    mov r0, r4
    bl .Lhash
    cmp r0, r8
    bne .Lfail
    mov r0, r6
    bl .Lhash
    ldr r1, [r10, #20]
    cmp r0, r1
    bne .Lfail
    ldr r3, .Lflash
    sub r0, r6, r3
    mov r0, r0, lsr #12         @ dest = 0x0e000000 + (sector << 12), modulo 2^32
    mov r1, r4
    adr r3, .Lswi
    add r3, r3, #1
    mov lr, pc
    bx r3
    ldr r1, .Lokvalue
    b .Lreturn
.Lfail:
    ldr r1, .Lerror
.Lreturn:
    str r1, [r11]
    mov r0, #1
    pop {r4-r11, lr}
    bx lr
.Lhash:
    mov r1, r0
    ldr r0, .Lbasis
    ldr r3, .Lprime
    add r2, r1, #0x1000
.Lhashbyte:
    ldrb r12, [r1], #1
    eor r0, r0, r12
    mul r12, r0, r3
    mov r0, r12
    cmp r1, r2
    blo .Lhashbyte
    bx lr
.Lewram: .word 0x02000000
.Lrom: .word 0x08000000
.Lflash: .word 0x0e000000
.Lbasis: .word 0x811c9dc5
.Lprime: .word 0x01000193
.Lokvalue: .word 0x5a480002
.Lerror: .word 0x5a48ffff
    .thumb
.Lswi:
    swi 0x48
    bx lr
    .align 2
