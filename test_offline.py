import cv2
import os
from src.vision.detector import VisionDetector
from src.logic.rules_engine import RulesEngine
from src.logic.decision_maker import DecisionMaker
from src.config import GameConfig

def tester_image(chemin_image):
    print(f"\n🔍 --- TEST SUR L'IMAGE : {chemin_image} ---")
    
    # Vérifier si l'image existe
    if not os.path.exists(chemin_image):
        print(f"❌ Erreur : L'image {chemin_image} est introuvable.")
        return

    # 1. Charger l'image avec OpenCV (remplace ADB)
    frame = cv2.imread(chemin_image)
    
    # --- LA CORRECTION EST ICI ---
    # On force l'image à la résolution de référence de votre téléphone
    frame = cv2.resize(frame, (GameConfig.SCREEN_WIDTH, GameConfig.SCREEN_HEIGHT))
    # -----------------------------
    
    # 2. Initialiser la vision
    vision = VisionDetector()
    # 2. Initialiser la vision (charge le modèle YOLO)
    vision = VisionDetector()

    # 3. Vérifier le tour (Point vert)
    est_mon_tour = vision.is_my_turn(frame)
    if not est_mon_tour:
        print("⏳ Résultat : Le point vert n'est pas détecté. (C'est le tour de l'adversaire)")
        
        # Test bonus : lire le popup si on n'est pas à notre tour
        popup_data = vision.detect_cards(frame, GameConfig.POPUP_ROI)
        if popup_data:
            cartes_popup = [d['card'] for d in popup_data]
            print(f"👁️ Popup détecté : L'adversaire a joué/capturé : {cartes_popup}")
        else:
            print("👁️ Aucun popup adverse détecté sur cette image.")
        return # Fin du test pour cette image

    print("🟢 Résultat : C'est notre tour ! Analyse en cours...")

    # 4. Détecter les cartes (Table + Main)
    hand_data = vision.detect_cards(frame, GameConfig.HAND_ROI)
    table_data = vision.detect_cards(frame, GameConfig.TABLE_ROI)
    
    my_hand = [d['card'] for d in hand_data]
    table_cards = [d['card'] for d in table_data]
    
    print(f"🃏 Cartes détectées en main : {my_hand}")
    print(f"🃏 Cartes détectées sur table : {table_cards}")

    if not my_hand:
        print("⚠️ Aucune carte trouvée dans la main. Fin de l'analyse.")
        return

    # 5. La Logique (Cerveau) calcule le meilleur coup
    best_move = DecisionMaker.get_best_move(my_hand, table_cards, RulesEngine.get_valid_combinations)
    
    if not best_move:
        print("❌ Erreur : L'IA n'a trouvé aucun coup possible.")
        return

    # 6. Afficher la décision (Remplace les clics ADB)
    action = best_move['action']
    played_card = best_move['play_card']
    
    print("\n✅ --- DÉCISION DE L'IA ---")
    if action == "7ot":
        print(f"👉 Action : JETER (7ot)")
        print(f"👉 Carte à jeter : {played_card}")
    elif action == "koul":
        eaten_cards = best_move['eat_cards']
        print(f"👉 Action : MANGER (Koul)")
        print(f"👉 Avec la carte : {played_card}")
        print(f"👉 Cartes à ramasser sur la table : {eaten_cards}")
        
    print("--------------------------------------\n")

if __name__ == "__main__":
    # Remplacez "ma_capture.jpg" par le nom de votre ancienne capture d'écran
    # Vous pouvez même tester plusieurs images d'un coup en les mettant dans une liste
    images_a_tester = [
        "adv1.jpg",
        "mon_tour.jpg",
        "mon_tour2.jpg"
    ]
    
    for img_path in images_a_tester:
        tester_image(img_path)