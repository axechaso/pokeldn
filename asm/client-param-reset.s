@ Reset the Mystery Gift client's param between multi-block dump transactions.
@ memory-dump-multi carries its cursor there; the next transaction must begin at zero.
    .arm
    .text
    .global _start
_start:
    mov r1, #0
    str r1, [r0]
    mov r0, #1
    bx lr
