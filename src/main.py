import time
import traceback
from types import SimpleNamespace
from typing import List, Optional, Tuple

from src.bot.adb_controller import ADBController
from src.vision.detector import VisionDetector
from src.logic.rules_engine import RulesEngine
from src.logic.decision_maker import DecisionMaker
from src.config import GameConfig
from src.logic.data_models import Suit

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #
POPUP_CONF = 0.7
POPUP_STABLE_FRAMES = 2  # popup seen identically on N NEW frames -> confirmed
POPUP_STABLE_SECONDS = (
    0.15  # ...or the same reading persisted this long on a static screen
)
POPUP_GONE_FRAMES = 3  # frames without popup before the next one may be counted
POPUP_DEBUG = True  # log candidates that are seen but not yet confirmed
POPUP_GRACE = 2.0  # my turn started, no popup yet: wait this long before playing anyway
CONFIRM_TIMEOUT = 4.0  # max wait for the game to acknowledge my move
HAND_CHECK_AFTER = 1.2  # start the secondary "hand changed" check after this delay
HAND_CHECK_EVERY = 0.5
POLL_TIMEOUT = 0.25  # max wait for a new frame (static screens emit none)
REEVAL_TURN_EVERY = 1.0  # re-check the turn indicator on a static screen
MAX_FAILS_BEFORE_PAUSE = 4

# Round structure (Tunisian Chkobba, 2 players):
# 4 cards on the table, then 6 deals of 3 cards each -> 4 + 6*6 = 40
DECK_SIZE = 40
PLAYS_PER_DEAL = 3
DEALS_PER_ROUND = 6
PLAYS_PER_ROUND = PLAYS_PER_DEAL * DEALS_PER_ROUND  # 18 plays per player
FALLBACK_ASSIGN_MIN_PLAYS = (
    15  # fallback end: only sweep the table if the round was well tracked
)


def key(card):
    return (card.value, card.suit)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class ChkobbaMemory:
    def __init__(self):
        self.cartes_sorties = []
        self.my_score_pile = []
        self.opponent_score_pile = []

    def reset(self, why: str = ""):
        print(f"🔄 [MÉMOIRE] Nouvelle manche ({why}), remise à zéro.")
        self.cartes_sorties = []
        self.my_score_pile = []
        self.opponent_score_pile = []

    @property
    def is_full(self) -> bool:
        return len(self.cartes_sorties) >= DECK_SIZE

    def out_keys(self) -> set:
        return {key(c) for c in self.cartes_sorties}

    def ajouter_cartes(self, cartes, action_type="7ot", player=None):
        """No automatic reset here: the bot decides when a round really ended."""
        if not isinstance(cartes, (list, tuple)):
            cartes = [cartes]

        for c in cartes:
            if not any(key(e) == key(c) for e in self.cartes_sorties):
                self.cartes_sorties.append(c)

            if action_type == "koul" and player is not None:
                pile = (
                    self.my_score_pile if player == "me" else self.opponent_score_pile
                )
                if not any(key(e) == key(c) for e in pile):
                    pile.append(c)

    def get_game_state(self):
        out = self.cartes_sorties
        dinars_sortis = sum(1 for c in out if c.suit == Suit.DINAR)
        sept_sortis = sum(1 for c in out if c.value == 7)
        six_sortis = sum(1 for c in out if c.value == 6)
        return {
            "sab3a_7aya_out": any(c.value == 7 and c.suit == Suit.DINAR for c in out),
            "dinars_restants": 10 - dinars_sortis,
            "sevens_restants": 4 - sept_sortis,
            "sixes_restants": 4 - six_sortis,
            "cartes_restantes": DECK_SIZE - len(out),
            "my_sevens": sum(1 for c in self.my_score_pile if c.value == 7),
            "opp_sevens": sum(1 for c in self.opponent_score_pile if c.value == 7),
            "my_sixes": sum(1 for c in self.my_score_pile if c.value == 6),
            "opp_sixes": sum(1 for c in self.opponent_score_pile if c.value == 6),
            "my_dinars": sum(1 for c in self.my_score_pile if c.suit == Suit.DINAR),
            "opp_dinars": sum(
                1 for c in self.opponent_score_pile if c.suit == Suit.DINAR
            ),
        }


# --------------------------------------------------------------------------- #
# Frame feed: every NEW frame exactly once, never the same one twice
# --------------------------------------------------------------------------- #
class FrameFeed:
    def __init__(self, adb: ADBController):
        self.adb = adb
        self.seq = 0

    def next(self, timeout: float = POLL_TIMEOUT) -> Tuple[Optional[object], bool]:
        """Returns (image, is_new). On a static screen no new frame arrives: we
        fall back to the latest one (is_new=False) so time-based logic keeps running."""
        f = self.adb.get_frame(after_seq=self.seq, timeout=timeout, settled=False)
        if f is not None:
            self.seq = f.seq
            return f.image.copy(), True
        f = self.adb.get_frame(settled=False)
        return (f.image.copy() if f is not None else None), False


# --------------------------------------------------------------------------- #
# Popup tracker: ONE event per popup, also on a static screen
# --------------------------------------------------------------------------- #
class PopupTracker:
    """
    screenrecord emits no frame while the picture does not change, so a popup
    that stays still produces a single frame. Confirmation therefore accepts
    EITHER N identical new frames OR the same reading persisting for a short time
    (tick() is called when no new frame arrived: unchanged frame = unchanged screen).
    """

    def __init__(self, vision: VisionDetector):
        self.vision = vision
        self._emitted: Optional[frozenset] = None
        self._candidate: Optional[frozenset] = None
        self._cand_cards: list = []
        self._cand_count = 0
        self._cand_since = 0.0
        self._gone = 0

    @property
    def pending(self) -> bool:
        return self._candidate is not None

    def _clear_candidate(self):
        self._candidate, self._cand_cards, self._cand_count = None, [], 0

    def _emit(self) -> list:
        cards = self._cand_cards
        self._emitted = self._candidate
        self._clear_candidate()
        return cards

    def update(self, frame) -> Optional[list]:
        """Call once per NEW frame."""
        data = self.vision.detect_cards(
            frame, GameConfig.POPUP_ROI, conf=POPUP_CONF, is_popup=True
        )
        cards = [d["card"] for d in data] if data else []

        if not cards:
            self._clear_candidate()
            self._gone += 1
            if self._gone >= POPUP_GONE_FRAMES:
                self._emitted = (
                    None  # popup left the screen: the next one is a new event
                )
            return None

        self._gone = 0
        sig = frozenset(key(c) for c in cards)

        if self._emitted is not None and sig <= self._emitted:
            self._clear_candidate()
            return None

        if sig == self._candidate:
            self._cand_count += 1
            self._cand_cards = cards
        else:
            self._candidate, self._cand_cards, self._cand_count = sig, cards, 1
            self._cand_since = time.monotonic()
            if POPUP_DEBUG:
                print(f"🔎 popup candidat : {cards} (en attente de confirmation)")

        if self._cand_count >= POPUP_STABLE_FRAMES:
            return self._emit()
        return None

    def tick(self) -> Optional[list]:
        """Call when NO new frame arrived: a persisting candidate is a real popup."""
        if (
            self._candidate is not None
            and time.monotonic() - self._cand_since >= POPUP_STABLE_SECONDS
        ):
            return self._emit()
        return None


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #
class ChkobbaBot:
    def __init__(self):
        self.adb = ADBController(device_id="")  # empty = single connected device
        self.vision = VisionDetector()
        self.memory = ChkobbaMemory()
        self.feed = FrameFeed(self.adb)
        self.tracker = PopupTracker(self.vision)

        self.expecting_popup = False  # True after I played: the opponent must answer
        self.popup_seen = False  # a popup was recorded since my last move
        self.fails = 0

        # Round tracking
        self.my_plays_round = 0  # my confirmed plays this round (max 18)
        self.opp_plays_round = 0  # opponent plays (popups) this round (max 18)
        self.last_capturer: Optional[str] = (
            None  # "me" | "opponent": last player who ate
        )
        self.deal_plays = 0
        self.deal_cards: set = set()
        self.pending_popups: List[Tuple[list, bool]] = (
            []
        )  # (cards, is_repeat) since my last move
        self.pending_move: Optional[dict] = None  # unconfirmed move to reconcile

    def poll_popup(self, frame, is_new: bool) -> Optional[list]:
        """Single entry point: new frame -> detect, static screen -> time-based tick."""
        return self.tracker.update(frame) if is_new else self.tracker.tick()

    # ---- opponent -------------------------------------------------------- #
    def record_opponent(self, cards: list, count_play: bool = True):
        action = "koul" if len(cards) > 1 else "7ot"
        repeat = len(cards) == 1 and key(cards[0]) in self.memory.out_keys()
        print(f"👁️ POPUP : l'adversaire a joué/ramassé avec {cards} (Action: {action})")
        self.memory.ajouter_cartes(cards, action_type=action, player="opponent")
        self.pending_popups.append((cards, repeat))
        if count_play:
            self.opp_plays_round += 1
        if action == "koul":
            self.last_capturer = "opponent"
        self.check_round_end()

    # ---- my moves bookkeeping ---------------------------------------------- #
    def commit_my_move(self, cards: list, action: str):
        self.memory.ajouter_cartes(cards, action_type=action, player="me")
        self.my_plays_round += 1
        self.deal_plays += 1
        if action == "koul":
            self.last_capturer = "me"
        self.check_round_end()

    def reconcile_pending(self, hand_keys: set):
        pm = self.pending_move
        if not pm:
            return
        self.pending_move = None
        if pm["played_key"] not in hand_keys:
            print(
                "🔁 Le coup précédent avait bien été joué (non confirmé à l'époque) : enregistré."
            )
            self.commit_my_move(pm["committed"], pm["action"])
        else:
            print("↩️ Le coup précédent n'avait pas été joué : rien à enregistrer.")

    # ---- round end (computed) --------------------------------------------- #
    def check_round_end(self):
        """4 table cards + 6 deals x (3 + 3) = 40. The round is over exactly when
        both players made their 18th play, whoever played last."""
        if (
            self.my_plays_round >= PLAYS_PER_ROUND
            and self.opp_plays_round >= PLAYS_PER_ROUND
        ):
            self.finish_round("18 coups de chaque joueur", assign_leftovers=True)

    def finish_round(self, why: str, assign_leftovers: bool):
        """Last capturer sweeps the table (not a chkobba), summary, then reset."""
        if assign_leftovers:
            out = self.memory.out_keys()
            leftovers = [
                SimpleNamespace(value=v, suit=s)
                for s in Suit
                for v in range(1, 11)
                if (v, s) not in out
            ]
            if leftovers and self.last_capturer:
                pile = (
                    self.memory.my_score_pile
                    if self.last_capturer == "me"
                    else self.memory.opponent_score_pile
                )
                pile.extend(leftovers)
                who = "moi" if self.last_capturer == "me" else "l'adversaire"
                print(
                    f"🧹 Fin de manche : {who} a mangé en dernier et ramasse les "
                    f"{len(leftovers)} cartes restantes de la table."
                )
            elif leftovers:
                print(
                    f"🧹 Fin de manche : {len(leftovers)} cartes restantes, "
                    "mais aucun ramassage détecté (non attribuées)."
                )

        mine, opp = self.memory.my_score_pile, self.memory.opponent_score_pile
        print(
            "🏁 Bilan manche -> "
            f"Cartes: moi {len(mine)} | adv {len(opp)} - "
            f"Dinars: moi {sum(1 for c in mine if c.suit == Suit.DINAR)} | "
            f"adv {sum(1 for c in opp if c.suit == Suit.DINAR)} - "
            f"7: moi {sum(1 for c in mine if c.value == 7)} | "
            f"adv {sum(1 for c in opp if c.value == 7)}"
        )

        self.memory.reset(why)
        self.my_plays_round = 0
        self.opp_plays_round = 0
        self.last_capturer = None
        self.deal_plays = 0
        self.deal_cards = set()
        self.pending_popups = []
        self.pending_move = None
        self.expecting_popup = False  # nothing to wait for at the start of a round
        self.popup_seen = False

    # ---- fallback: fresh hand detection (if popups were missed) ------------ #
    def check_new_round(self, hand_keys: set):
        same_deal = (
            bool(self.deal_cards & hand_keys) and self.deal_plays < PLAYS_PER_DEAL
        )
        if same_deal:
            self.deal_cards |= hand_keys
            return

        self.deal_cards = set(hand_keys)
        self.deal_plays = 0

        overlap = len(hand_keys & self.memory.out_keys())
        reasons = []
        if self.memory.is_full:
            reasons.append(f"{DECK_SIZE} cartes sorties")
        if self.my_plays_round >= PLAYS_PER_ROUND:
            reasons.append(f"{self.my_plays_round} coups joués (popups manqués ?)")
        if overlap >= 2:
            reasons.append(f"{overlap} cartes de ma nouvelle main déjà sorties")
        if reasons:
            self.start_new_round_fallback(", ".join(reasons))

    def start_new_round_fallback(self, why: str):
        # The opponent's FIRST play of the new round can arrive before my first turn
        carry = [c for c, repeat in self.pending_popups if repeat]
        if not carry and len(self.pending_popups) >= 2:
            carry = [self.pending_popups[-1][0]]

        self.finish_round(
            why, assign_leftovers=self.my_plays_round >= FALLBACK_ASSIGN_MIN_PLAYS
        )
        self.deal_cards = set()  # the hand just read belongs to the new round

        for cards in carry:
            action = "koul" if len(cards) > 1 else "7ot"
            self.memory.ajouter_cartes(cards, action_type=action, player="opponent")
            self.opp_plays_round += 1
            if action == "koul":
                self.last_capturer = "opponent"
            print(f"↪️ Popup {cards} appartient à la nouvelle manche : conservé.")

    # ---- move confirmation ------------------------------------------------ #
    def confirm_move(self, hand_before: frozenset) -> Tuple[bool, List[list]]:
        """Wait until the game acknowledged my move. Popups seen meanwhile are
        returned (not applied) so the caller can commit MY move first."""
        events: List[list] = []
        t0 = time.monotonic()
        off_count = 0
        last_hand_check = 0.0

        while time.monotonic() - t0 < CONFIRM_TIMEOUT:
            frame, is_new = self.feed.next(0.15)
            if frame is None:
                continue

            cards = self.poll_popup(frame, is_new)
            if cards:
                events.append(cards)

            if is_new:
                if not self.vision.is_my_turn(frame):
                    off_count += 1
                    if off_count >= 2:
                        return True, events
                else:
                    off_count = 0

            elapsed = time.monotonic() - t0
            if (
                elapsed >= HAND_CHECK_AFTER
                and elapsed - last_hand_check >= HAND_CHECK_EVERY
            ):
                last_hand_check = elapsed
                hand_now = frozenset(
                    key(d["card"])
                    for d in self.vision.detect_cards(frame, GameConfig.HAND_ROI)
                )
                if hand_now and hand_now != hand_before:
                    return True, events
        return False, events

    # ---- my move ---------------------------------------------------------- #
    def play_turn(self, frame) -> str:
        """Returns 'played' | 'empty' | 'error' | 'unconfirmed'."""
        hand_data = self.vision.detect_cards(frame, GameConfig.HAND_ROI)
        table_data = self.vision.detect_cards(frame, GameConfig.TABLE_ROI)
        my_hand = [d["card"] for d in hand_data]
        table_cards = [d["card"] for d in table_data]

        if not my_hand:
            return "empty"

        hand_keys = {key(c) for c in my_hand}

        self.reconcile_pending(hand_keys)
        self.check_new_round(hand_keys)

        print("🟢 C'est notre tour ! Analyse de la table en cours...")
        print(f"Cartes en main: {my_hand}")
        print(f"Cartes sur table: {table_cards}")

        state = self.memory.get_game_state()
        print(
            f"📊 État Partie -> 7_Restants: {state['sevens_restants']} "
            f"(Moi:{state['my_sevens']} | Adv:{state['opp_sevens']}) - "
            f"Dinars_Restants: {state['dinars_restants']} "
            f"(Moi:{state['my_dinars']} | Adv:{state['opp_dinars']})"
        )

        best = DecisionMaker.get_best_move(
            my_hand,
            table_cards,
            RulesEngine.get_valid_combinations,
            self.memory.cartes_sorties,
            game_state=state,
        )  # DecisionMaker already prints the chosen move
        if not best:
            print("Erreur critique: Aucun coup calculé.")
            return "error"

        action = best["action"]
        played = best["play_card"]
        card_xy = next((d for d in hand_data if d["card"] == played), None)
        if card_xy is None:
            print(
                f"⚠️ Carte {played} introuvable dans la main détectée, on recommence."
            )
            return "error"

        committed = [played]
        hand_before = frozenset(hand_keys)

        coords = []
        if action == "koul":
            eaten = list(best["eat_cards"])
            for ec in eaten:
                c_data = next((d for d in table_data if d["card"] == ec), None)
                if c_data is None:
                    print(f"⚠️ Carte de table {ec} introuvable, on annule ce coup.")
                    return "error"
                coords.append((c_data["x"], c_data["y"]))
            committed += eaten

        self.pending_popups.clear()

        if action == "7ot":
            ok = self.adb.action_7ot(
                card_xy["x"], card_xy["y"], GameConfig.BTN_7OT_X, GameConfig.BTN_7OT_Y
            )
        else:
            ok = self.adb.action_koul(
                card_xy["x"],
                card_xy["y"],
                coords,
                GameConfig.BTN_KOUL_X,
                GameConfig.BTN_KOUL_Y,
            )

        if not ok:
            print("⚠️ Les clics ADB ont échoué.")
            return "error"

        confirmed, events = self.confirm_move(hand_before)

        if confirmed:
            self.pending_move = None
            self.commit_my_move(committed, action)  # may end the round
            print("✅ Coup confirmé par le jeu.")
            self.popup_seen = False
            # if the round just ended (counters reset to 0) no popup is expected
            self.expecting_popup = self.my_plays_round > 0
            for cards in events:
                self.record_opponent(cards)
                self.popup_seen = True
            return "played"

        print("⚠️ Coup non confirmé : vérification à la prochaine lecture de la main.")
        self.pending_move = {
            "played_key": key(played),
            "committed": committed,
            "action": action,
        }
        for cards in events:
            self.record_opponent(cards)
            self.popup_seen = True
        return "unconfirmed"

    # ---- main loop -------------------------------------------------------- #
    def run(self):
        my_turn = False
        last_eval = 0.0
        turn_since: Optional[float] = None
        empty_logged = False

        while True:
            frame, is_new = self.feed.next()
            if frame is None:
                continue

            now = time.monotonic()
            cards = self.poll_popup(frame, is_new)
            if cards:
                self.record_opponent(cards)
                self.popup_seen = True

            if is_new or now - last_eval >= REEVAL_TURN_EVERY:
                my_turn = self.vision.is_my_turn(frame)
                last_eval = now

            if not my_turn:
                turn_since = None
                empty_logged = False
                continue

            if turn_since is None:
                turn_since = now

            # Never play while a popup is still being confirmed (keeps the order)
            if self.tracker.pending:
                continue

            if (
                self.expecting_popup
                and not self.popup_seen
                and self.my_plays_round < PLAYS_PER_ROUND
            ):
                if now - turn_since < POPUP_GRACE:
                    continue
                print(
                    "⏱️ Aucun popup adverse détecté après le délai, on joue quand même."
                )
                self.expecting_popup = False

            result = self.play_turn(frame)

            if result == "played":
                self.fails = 0
                my_turn = False
                last_eval = time.monotonic()
                turn_since = None
                empty_logged = False
            elif result == "empty":
                if not empty_logged:
                    print("Main Vide (distribution en cours ?)")
                    empty_logged = True
                time.sleep(0.3)
            else:
                self.fails += 1
                my_turn = False
                last_eval = time.monotonic()
                turn_since = None
                if self.fails >= MAX_FAILS_BEFORE_PAUSE:
                    print(
                        f"⚠️ {self.fails} échecs consécutifs, pause 2s (vérifiez ADB / ROI)."
                    )
                    time.sleep(2.0)
                else:
                    time.sleep(0.3)

    def stop(self):
        self.adb.stop()


def main():
    print("🚀 Démarrage du Bot Chkobba Autonome (Version Agentic AI)...")
    bot = ChkobbaBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt demandé.")
    except Exception:
        traceback.print_exc()
    finally:
        bot.stop()


if __name__ == "__main__":
    main()
