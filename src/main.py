import time
import cv2
from src.bot.adb_controller import ADBController
from src.vision.detector import VisionDetector
from src.logic.rules_engine import RulesEngine
from src.logic.decision_maker import DecisionMaker
from src.config import GameConfig
from src.logic.data_models import Card

class ChkobbaMemory:
    def __init__(self):
        self.cartes_sorties = []
        
    def ajouter_cartes(self, cartes):
        """ Ajoute une ou plusieurs cartes à la mémoire en évitant les doublons. """
        if not isinstance(cartes, (list, tuple)):
            cartes = [cartes]
            
        for c in cartes:
            # On vérifie si la carte n'est pas déjà dans la mémoire (par sécurité)
            deja_presente = any(exist_c.value == c.value and exist_c.suit == c.suit for exist_c in self.cartes_sorties)
            if not deja_presente:
                self.cartes_sorties.append(c)
                
        # Remise à zéro automatique à la fin de la manche (40 cartes)
        if len(self.cartes_sorties) >= 40:
            print("🔄 [MÉMOIRE] 40 cartes jouées ! Nouvelle manche détectée, remise à zéro.")
            self.cartes_sorties = []

def main():
    print("🚀 Démarrage du Bot Chkobba Autonome (Version YOLO)...")
    adb = ADBController(device_id="") # Laissez vide s'il y a un seul appareil connecté
    vision = VisionDetector()
    
    # Initialisation de la nouvelle mémoire intelligente
    memory = ChkobbaMemory()
    
    # Indicateur pour ne pas lire le popup adverse en double
    popup_deja_lu_ce_tour = False

    while True:
        # 1. Sense : Capturer l'écran
        frame = adb.get_screen_frame()
        
        if frame is None:
            print("Erreur : La capture d'écran a retourné None. Problème de décodage ADB.")
            time.sleep(1)
            continue
            
        # 2. State Check : Est-ce mon tour ?
        is_my_turn = vision.is_my_turn(frame)

        # ---------------------------------------------------------
        # VÉRIFICATION DU POPUP (Indépendant du tour !)
        # ---------------------------------------------------------
        if not popup_deja_lu_ce_tour:
            popup_data = vision.detect_cards(frame, GameConfig.POPUP_ROI, conf=0.7, is_popup=True)
            
            if popup_data:
                carte_adverse = [d['card'] for d in popup_data]
                print(f"👁️ POPUP DÉTECTÉ ! L'adversaire a joué/ramassé avec : {carte_adverse}")
                
                # Ajout des cartes adverses à la mémoire
                memory.ajouter_cartes(carte_adverse)
                popup_deja_lu_ce_tour = True

        # ---------------------------------------------------------
        # GESTION DES TOURS
        # ---------------------------------------------------------
        if not is_my_turn:
            time.sleep(0.1)
            continue

        # --- C'EST NOTRE TOUR ---
        print("🟢 C'est notre tour ! Analyse de la table en cours...")
        
        # 3. Vision YOLO : Extraire les cartes de la table et de la main
        hand_data = vision.detect_cards(frame, GameConfig.HAND_ROI)
        table_data = vision.detect_cards(frame, GameConfig.TABLE_ROI)
        
        my_hand = [d['card'] for d in hand_data]
        table_cards = [d['card'] for d in table_data]
        
        print(f"Cartes en main: {my_hand}")
        print(f"Cartes sur table: {table_cards}")

        if not my_hand:
            print("Main vide. Fin de la partie ou distribution en cours.")
            time.sleep(3)
            continue

        # 4. Think : Calculer le meilleur coup avec l'Heuristique + Mémoire
        best_move = DecisionMaker.get_best_move(
            my_hand, 
            table_cards, 
            RulesEngine.get_valid_combinations,
            memory.cartes_sorties  # Injection de la mémoire ici !
        )
        
        if not best_move:
            print("Erreur critique: Aucun coup calculé.")
            time.sleep(3)
            continue

        # 5. Act : Exécuter les clics via ADB
        action = best_move['action']
        played_card_obj = best_move['play_card']
        card_xy = next((d for d in hand_data if d['card'] == played_card_obj), None)

        if action == "7ot":
            adb.action_7ot(card_xy['x'], card_xy['y'], GameConfig.BTN_7OT_X, GameConfig.BTN_7OT_Y)
            # J'ajoute la carte jetée à ma mémoire
            memory.ajouter_cartes(played_card_obj)
            
        elif action == "koul":
            eaten_cards_obj = best_move['eat_cards']
            table_coords = []
            for ec in eaten_cards_obj:
                c_data = next((d for d in table_data if d['card'] == ec), None)
                if c_data:
                    table_coords.append((c_data['x'], c_data['y']))
                    
            adb.action_koul(card_xy['x'], card_xy['y'], table_coords, GameConfig.BTN_KOUL_X, GameConfig.BTN_KOUL_Y)
            # J'ajoute la carte jouée ET les cartes mangées à la mémoire
            memory.ajouter_cartes([played_card_obj] + list(eaten_cards_obj))

        print("✅ Coup joué. Attente de la transition du jeu...")
        
        # ---> RÉINITIALISATION DU POPUP <---
        popup_deja_lu_ce_tour = False
        time.sleep(1)

if __name__ == "__main__":
    main()