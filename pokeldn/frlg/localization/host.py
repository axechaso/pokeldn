"""Use the normal PokeLDN radio/IP host runtime for a gated localization operation."""

from pokeldn.frlg.gift.host_mg_app import MysteryGiftHostApplication
from pokeldn.frlg.gift.stamp_rally import MysteryGiftDistribution
from pokeldn.frlg.link.host_app import HostApplication
from pokeldn.frlg.rom import buffer_script as bs
from .gift_transport import GiftConnectionClosed


class LocalizationHostApplication(MysteryGiftHostApplication):
    """A library entry point sharing the existing host config, radio, beacon and Pia stack.

    ROM modifications require the operation's independently measured observer. Bootstrap
    reads and the bounded RAM demos use their own preflight. No CLI flag fills wrapper
    identity from profile metadata to allow a ROM write.
    """

    ACTIVITY_NOUN = "localization"

    def __init__(self, config, operation, **kwargs):
        self.operation = operation
        # The base host needs a payload descriptor for its beacon/build-plan setup. The
        # custom server supplies every actual client script/payload; this descriptor is never sent.
        descriptor = MysteryGiftDistribution(card=None, ram_script=None,
                                             buffer_code=bs.payload(bs.TRAINER_ID_PROBE))
        super().__init__(config, distribution=descriptor, **kwargs)

    def _build_engine(self, link_player, timing):
        return self.operation.make_engine(link_player=link_player, timing=timing,
                                          trust_pia=self.config.trust_pia, log=self.log)

    def _screen_card(self):
        labels = {"probe": "Read-only ROM capture", "inspect": "Inspect ROM",
                  "load": "Load localization", "restore": "Restore ROM", "display": "NS Chinese display",
                  "story": "NS NPC Chinese test"}
        return "FRLG localization", labels[self.operation.operation], None

    def _log_identity(self, link_player):
        self.info(f"FRLG localization {self.operation.operation}: "
                  f"profile={self.operation.profile.profile_id}, "
                  f"game={self.operation.profile.identity.game_code}, "
                  f"revision={self.operation.profile.identity.revision:#x}")
        if self.operation.operation == "probe":
            self.info(f"Read-only ROM capture: {self.operation.rom_size} bytes in "
                      f"{self.operation.read_batch_size}-byte requests.")
        elif self.operation.operation == "display":
            self.info("NS Chinese screen demo: 808-byte payload, no ROM capture, no save or ROM writes. "
                      "Watch for Chinese text. Wait for the English result message, then press A; "
                      "communication may extend the wait.")
        elif self.operation.operation == "story":
            self.info("NS NPC dialogue demo: two short RAM uploads, no ROM capture, save or ROM writes. "
                      "After the result, load your game and talk to the south fat man in Pallet Town. "
                      "Only his first sentence changes. A soft reset or full restart removes the demo.")
        else:
            self.info("The connected console must match game data, wrapper observation and full ROM fingerprint.")

    def _hosting_instructions(self):
        return ("Hosting FRLG localization. On the Switch choose "
                "Mystery Gift -> Wonder Cards -> Friend.")

    def _log_activity_progress(self):
        # A failed worker cannot complete a normal gift close. Surface the error here
        # so HostApplication's finally block stops the network even when ACKs are stuck.
        if self.operation.result.done():
            error = self.operation.result.exception()
            if error is not None:
                raise error
        state = self.session.activity.state
        if state != self._last_state:
            self._last_state = state
            self.info(f"Localization link: {state}")

    def _activity_succeeded(self):
        return self.operation.result.done() and self.operation.result.exception() is None

    def run(self):
        try:
            joined = HostApplication.run(self)
        finally:
            self.operation.close()
        if not self.operation.result.done():
            raise GiftConnectionClosed("localization stopped before console identification")
        self.operation_result = self.operation.result.result()
        self.info(f"Localization: {self.operation_result['state']}")
        return joined
