"""Buffer-script adapter and an explicitly synthetic Unicorn research backend.

The adapter takes observations from its host integration, not from patch metadata. No
retail host is exposed by the CLI until wrapper identification and RAM lifetime have evidence.
"""

from dataclasses import dataclass

from pokeldn.frlg.rom import buffer_script as bs
from . import LocalizationError, PAGE_SIZE, ROM_BASE, integer
from .payloads import COMMIT_OK, STAGE_OK, build_commit, build_stage
from .profiles import Identity


@dataclass(frozen=True)
class Observation:
    identity: Identity
    executing: tuple  # ROM file-offset ranges that a page write may not intersect

    def __post_init__(self):
        if not isinstance(self.identity, Identity):
            raise LocalizationError("observation requires a measured Identity")
        Identity.parse(dict(vars(self.identity)))
        for start, end in self.executing:
            integer(start, "execution range start", 0, 0xFFFFFF)
            integer(end, "execution range end", start + 1, 0x1000000)


class BufferTransport:
    """Bridge to a persistent Mystery Gift exchange: observe(), read(addr,size), run(code).

    `run` must use CLIENT_SCRIPT_RUN_BUFFER and return its u32 response. `read` must use
    CLIENT_SCRIPT_DUMP_MEMORY (InitSend before RUN), and chunk into <=1024-byte responses.
    These callbacks must remain in the same connected game process during an operation.
    """

    def __init__(self, observe, read, run):
        self._observe, self._read, self._run = observe, read, run

    def observe(self):
        observed = self._observe()
        if not isinstance(observed, Observation):
            raise LocalizationError("host did not provide an observed build identity")
        return observed

    def read_memory(self, address, size):
        result = bytearray()
        for pos in range(0, size, 1024):
            count = min(1024, size - pos)
            part = self._read(address + pos, count)
            if not isinstance(part, bytes) or len(part) != count:
                raise LocalizationError("short/invalid memory readback")
            result.extend(part)
        return bytes(result)

    def read_rom(self, offset, size):
        return self.read_memory(ROM_BASE + offset, size)

    def begin(self, profile, session, patch, offset, target):
        self._expect(build_stage(profile, session, patch, offset, target), STAGE_OK)

    def stage(self, profile, session, patch, offset, target, chunk):
        self._expect(build_stage(profile, session, patch, offset, target, chunk=chunk), STAGE_OK)

    def commit(self, profile, session, patch, offset, before, target):
        self._expect(build_commit(profile, session, patch, offset, before, target), COMMIT_OK)

    def _expect(self, code, expected):
        status = self._run(code)
        if type(status) is not int or status != expected:
            raise LocalizationError(f"guest refused payload: status={status!r}")


class ModelTransport(BufferTransport):
    """ARM execution plus a model of the *documented* wrapper's ROM-copy syscall.

    This deliberately implements the unverified hardware behavior and therefore cannot
    establish P1 (Ryubing) or P2 (retail) success. It is for software regression only.
    """

    def __init__(self, profile, base):
        from pokeldn.frlg.rom import builds
        runtime = profile.runtime_base(base)

        class RomMachine(bs._Machine):
            def _on_swi(machine, uc, intno, user_data=None):
                arm = machine._arm
                pc = uc.reg_read(arm.UC_ARM_REG_PC)
                thumb = uc.reg_read(arm.UC_ARM_REG_CPSR) & (1 << 5)
                number = int.from_bytes(uc.mem_read(pc - (2 if thumb else 4), 2 if thumb else 4),
                                        "little") & (0xFF if thumb else 0xFFFFFF)
                if intno == 2 and number == 0x48:
                    sector = uc.reg_read(arm.UC_ARM_REG_R0)
                    dest = (0x0E000000 + sector * PAGE_SIZE) & 0xFFFFFFFF
                    source = uc.reg_read(arm.UC_ARM_REG_R1)
                    if (ROM_BASE <= dest <= ROM_BASE + profile.rom_size - PAGE_SIZE
                            and 0x02000000 <= source <= 0x02040000 - PAGE_SIZE):
                        uc.mem_write(dest, bytes(uc.mem_read(source, PAGE_SIZE)))
                        uc.ctl_remove_cache(dest, dest + PAGE_SIZE)
                        machine.rom_writes.append(dest - ROM_BASE)
                    return
                return super(RomMachine, machine)._on_swi(uc, intno, user_data)

        self.profile, self.base = profile, bytes(base)
        self.machine = RomMachine(bs.payload(bs.TRAINER_ID_PROBE), rom=runtime,
                                  build=profile.identity.game_code)
        self.machine.rom_writes = []
        executing = builds.for_game_code(profile.identity.game_code).client_run_buffer_script - ROM_BASE
        self.observation = Observation(profile.identity, ((executing, executing + 0x40),))
        super().__init__(lambda: self.observation, self._memory, self.run)

    def _memory(self, address, size):
        return bytes(self.machine.uc.mem_read(address, size))

    def run(self, code):
        bs.validate(code)
        self.machine.uc.mem_write(bs.GDECOMPRESSION_BUFFER, code.ljust(1024, b"\x00"))
        self.machine.uc.ctl_remove_cache(bs.GDECOMPRESSION_BUFFER, bs.GDECOMPRESSION_BUFFER + 1024)
        return self.machine.call().param

    def soft_reset(self):
        runtime = self._memory(ROM_BASE, self.profile.rom_size)
        self.__init__(self.profile, self.base)
        self.machine.uc.mem_write(ROM_BASE, runtime)

    def full_restart(self):
        self.__init__(self.profile, self.base)
