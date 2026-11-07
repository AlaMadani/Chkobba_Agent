class GameConfig:
    # ---------------------------------------------------------
    # PARAMÈTRES YOLO (NOUVEAU)
    # ---------------------------------------------------------
    YOLO_MODEL_PATH = "assets/best.pt"
    YOLO_CONFIDENCE = 0.5  # Seuil de détection (50%)

    # ---------------------------------------------------------
    # RÉSOLUTION DE L'ÉCRAN
    # ---------------------------------------------------------
    # Résolution de référence de votre smartphone
    SCREEN_WIDTH = 720
    SCREEN_HEIGHT = 1612

    # ---------------------------------------------------------
    # RÉGIONS D'INTÉRÊT (ROI) - Format : (Y_min, Y_max, X_min, X_max)
    # ---------------------------------------------------------
    # 1. Zone du Popup de l'adversaire (Haut de l'écran)
    POPUP_ROI = (int(SCREEN_HEIGHT * 0.15), int(SCREEN_HEIGHT * 0.35), 0, SCREEN_WIDTH)
    
    # 2. La table (Centre de l'écran)
    TABLE_ROI = (int(SCREEN_HEIGHT * 0.40), int(SCREEN_HEIGHT * 0.65), 0, SCREEN_WIDTH)
    
    # 3. La main du joueur (Bas de l'écran)
    HAND_ROI = (int(SCREEN_HEIGHT * 0.75), SCREEN_HEIGHT, 0, SCREEN_WIDTH)

    # ---------------------------------------------------------
    # COORDONNÉES DES BOUTONS ADB
    # ---------------------------------------------------------
    # Bouton "7ot" (Centré, juste au-dessus des infos du joueur)
    BTN_7OT_X = int(SCREEN_WIDTH * 0.5)
    BTN_7OT_Y = int(SCREEN_HEIGHT * 0.72)

    # Bouton "Koul" (Décalé sur la droite)
    BTN_KOUL_X = int(SCREEN_WIDTH * 0.7)
    BTN_KOUL_Y = int(SCREEN_HEIGHT * 0.72)
    
    # ---------------------------------------------------------
    # DÉTECTION DU TOUR (POINT VERT)
    # ---------------------------------------------------------
    # Coordonnées validées grâce à debug_roi.jpg
    TURN_DOT_ROI = [1250, 1400, 350, 600] 
    
    # Seuil pour valider le tour (présence du point vert)
    TURN_DOT_THRESHOLD = 0.01