import time
import cv2
from src.bot.adb_controller import ADBController
from src.vision.detector import VisionDetector
from src.logic.rules_engine import RulesEngine
from src.logic.decision_maker import DecisionMaker
from src.config import GameConfig
from src.logic.data_models import Card, Suit


def active_wait(duration, adb, vision, memory, popup_flag):
    """
    Remplace time.sleep(). Met le script en pause tout en surveillant
    activement l'écran pour ne rater aucun popup rapide de l'adversaire.
    """
    end_time = time.time() + duration
    while time.time() < end_time:
        if not popup_flag:
            frame = adb.get_screen_frame()
            if frame is not None:
                popup_data = vision.detect_cards(
                    frame, GameConfig.POPUP_ROI, conf=0.7, is_popup=True
                )
                if popup_data:
                    carte_adverse = [d["card"] for d in popup_data]
                    # S'il y a plus d'1 carte dans le popup, c'est qu'il a mangé (koul)
                    action_adverse = "koul" if len(carte_adverse) > 1 else "7ot"

                    print(
                        f"👁️ POPUP DÉTECTÉ PENDANT L'ATTENTE ! {carte_adverse} (Action: {action_adverse})"
                    )
                    memory.ajouter_cartes(
                        carte_adverse, action_type=action_adverse, player="opponent"
                    )
                    popup_flag = True
        time.sleep(0.1)  # Petite pause pour éviter de surcharger le CPU
    return popup_flag


class ChkobbaMemory:
    def __init__(self):
        self.cartes_sorties = []
        self.my_score_pile = []
        self.opponent_score_pile = []

    def ajouter_cartes(self, cartes, action_type="7ot", player=None):
        """Ajoute une ou plusieurs cartes à la mémoire avec gestion de l'appartenance."""
        if not isinstance(cartes, (list, tuple)):
            cartes = [cartes]

        for c in cartes:
            # 1. Ajout au tracker global (Card Counting)
            deja_presente = any(
                exist_c.value == c.value and exist_c.suit == c.suit
                for exist_c in self.cartes_sorties
            )
            if not deja_presente:
                self.cartes_sorties.append(c)

            # 2. Ajout au tracker de score (Dynamic Reward Shaping)
            if action_type == "koul" and player is not None:
                target_pile = (
                    self.my_score_pile if player == "me" else self.opponent_score_pile
                )

                # Vérifie qu'on n'ajoute pas la même carte physique 2 fois dans le score
                deja_score = any(
                    exist_c.value == c.value and exist_c.suit == c.suit
                    for exist_c in target_pile
                )
                if not deja_score:
                    target_pile.append(c)

        # Remise à zéro automatique à la fin de la manche (40 cartes)
        if len(self.cartes_sorties) >= 40:
            print(
                "🔄 [MÉMOIRE] 40 cartes jouées ! Nouvelle manche détectée, remise à zéro."
            )
            self.cartes_sorties = []
            self.my_score_pile = []
            self.opponent_score_pile = []

    def get_game_state(self):
        """Calcule l'état exact global ET l'état des possessions pour l'heuristique"""
        # État global du deck
        sab3a_7aya_out = any(
            c.value == 7 and c.suit == Suit.DINAR for c in self.cartes_sorties
        )
        dinars_sortis = sum(1 for c in self.cartes_sorties if c.suit == Suit.DINAR)
        sept_sortis = sum(1 for c in self.cartes_sorties if c.value == 7)
        six_sortis = sum(1 for c in self.cartes_sorties if c.value == 6)

        # État des possessions (Score sécurisé)
        my_sevens = sum(1 for c in self.my_score_pile if c.value == 7)
        opp_sevens = sum(1 for c in self.opponent_score_pile if c.value == 7)

        my_sixes = sum(1 for c in self.my_score_pile if c.value == 6)
        opp_sixes = sum(1 for c in self.opponent_score_pile if c.value == 6)

        my_dinars = sum(1 for c in self.my_score_pile if c.suit == Suit.DINAR)
        opp_dinars = sum(1 for c in self.opponent_score_pile if c.suit == Suit.DINAR)

        return {
            "sab3a_7aya_out": sab3a_7aya_out,
            "dinars_restants": 10 - dinars_sortis,
            "sevens_restants": 4 - sept_sortis,
            "sixes_restants": 4 - six_sortis,
            "cartes_restantes": 40 - len(self.cartes_sorties),
            "my_sevens": my_sevens,
            "opp_sevens": opp_sevens,
            "my_sixes": my_sixes,
            "opp_sixes": opp_sixes,
            "my_dinars": my_dinars,
            "opp_dinars": opp_dinars,
        }


def main():
    print("🚀 Démarrage du Bot Chkobba Autonome (Version Agentic AI)...")
    adb = ADBController(device_id="")  # Laissez vide s'il y a un seul appareil connecté
    vision = VisionDetector()

    # Initialisation de la mémoire structurée
    memory = ChkobbaMemory()

    # Indicateur pour ne pas lire le popup adverse en double
    popup_deja_lu_ce_tour = False

    while True:
        # 1. Sense : Capturer l'écran
        frame = adb.get_screen_frame()

        if frame is None:
            print(
                "Erreur : La capture d'écran a retourné None. Problème de décodage ADB."
            )
            popup_deja_lu_ce_tour = active_wait(
                1, adb, vision, memory, popup_deja_lu_ce_tour
            )
            continue

        # 2. State Check : Est-ce mon tour ?
        is_my_turn = vision.is_my_turn(frame)

        # ---------------------------------------------------------
        # VÉRIFICATION DU POPUP (Indépendant du tour !)
        # ---------------------------------------------------------
        if not popup_deja_lu_ce_tour:
            popup_data = vision.detect_cards(
                frame, GameConfig.POPUP_ROI, conf=0.7, is_popup=True
            )

            if popup_data:
                carte_adverse = [d["card"] for d in popup_data]
                action_adverse = "koul" if len(carte_adverse) > 1 else "7ot"
                print(
                    f"👁️ POPUP DÉTECTÉ ! L'adversaire a joué/ramassé avec : {carte_adverse} (Action: {action_adverse})"
                )

                # Ajout des cartes adverses à la mémoire et à son score si besoin
                memory.ajouter_cartes(
                    carte_adverse, action_type=action_adverse, player="opponent"
                )
                popup_deja_lu_ce_tour = True

        # ---------------------------------------------------------
        # GESTION DES TOURS
        # ---------------------------------------------------------
        if not is_my_turn:
            popup_deja_lu_ce_tour = active_wait(
                0.1, adb, vision, memory, popup_deja_lu_ce_tour
            )
            continue

        # --- C'EST NOTRE TOUR ---
        print("🟢 C'est notre tour ! Analyse de la table en cours...")

        # 3. Vision YOLO : Extraire les cartes de la table et de la main
        hand_data = vision.detect_cards(frame, GameConfig.HAND_ROI)
        table_data = vision.detect_cards(frame, GameConfig.TABLE_ROI)

        my_hand = [d["card"] for d in hand_data]
        table_cards = [d["card"] for d in table_data]

        print(f"Cartes en main: {my_hand}")
        print(f"Cartes sur table: {table_cards}")

        # Afficher l'état d'avancement pour le debug avec les nouveaux scores
        state = memory.get_game_state()
        print(
            f"📊 État Partie -> 7_Restants: {state['sevens_restants']} (Moi:{state['my_sevens']} | Adv:{state['opp_sevens']}) - "
            f"Dinars_Restants: {state['dinars_restants']} (Moi:{state['my_dinars']} | Adv:{state['opp_dinars']})"
        )

        if not my_hand:
            print("Main Vide")
            popup_deja_lu_ce_tour = False
            popup_deja_lu_ce_tour = active_wait(
                3, adb, vision, memory, popup_deja_lu_ce_tour
            )
            continue

        # 4. Think : Calculer le meilleur coup avec l'Heuristique + Mémoire
        best_move = DecisionMaker.get_best_move(
            my_hand,
            table_cards,
            RulesEngine.get_valid_combinations,
            memory.cartes_sorties,
            game_state=state,
        )

        if not best_move:
            print("Erreur critique: Aucun coup calculé.")
            popup_deja_lu_ce_tour = active_wait(
                3, adb, vision, memory, popup_deja_lu_ce_tour
            )
            continue

        # 5. Act : Exécuter les clics via ADB
        action = best_move["action"]
        played_card_obj = best_move["play_card"]
        card_xy = next((d for d in hand_data if d["card"] == played_card_obj), None)

        if action == "7ot":
            adb.action_7ot(
                card_xy["x"], card_xy["y"], GameConfig.BTN_7OT_X, GameConfig.BTN_7OT_Y
            )
            memory.ajouter_cartes(played_card_obj, action_type="7ot", player="me")

        elif action == "koul":
            eaten_cards_obj = best_move["eat_cards"]
            table_coords = []
            for ec in eaten_cards_obj:
                c_data = next((d for d in table_data if d["card"] == ec), None)
                if c_data:
                    table_coords.append((c_data["x"], c_data["y"]))

            adb.action_koul(
                card_xy["x"],
                card_xy["y"],
                table_coords,
                GameConfig.BTN_KOUL_X,
                GameConfig.BTN_KOUL_Y,
            )
            # On enregistre la carte jouée + les cartes mangées dans notre score
            memory.ajouter_cartes(
                [played_card_obj] + list(eaten_cards_obj),
                action_type="koul",
                player="me",
            )

        print("✅ Coup joué. Attente de la transition du jeu...")

        # ---> RÉINITIALISATION DU POPUP <---
        popup_deja_lu_ce_tour = False
        popup_deja_lu_ce_tour = active_wait(
            1, adb, vision, memory, popup_deja_lu_ce_tour
        )


if __name__ == "__main__":
    main()
