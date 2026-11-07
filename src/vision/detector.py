import cv2
import numpy as np
from typing import List, Dict
from ultralytics import YOLO
from src.config import GameConfig
from src.logic.data_models import Card, Suit

class VisionDetector:
    def __init__(self):
        print(f"Chargement du modèle YOLO depuis {GameConfig.YOLO_MODEL_PATH}...")
        self.model = YOLO(GameConfig.YOLO_MODEL_PATH)

    def is_my_turn(self, frame: np.ndarray) -> bool:
        try:
            y1, y2, x1, x2 = GameConfig.TURN_DOT_ROI
        except AttributeError:
             y1, y2, x1, x2 = 1250, 1400, 350, 600 
             
        dot_crop = frame[y1:y2, x1:x2]
        
        # cv2.imwrite("debug_roi.jpg", dot_crop) # Optionnel: vous pouvez le décommenter pour debug
        hsv_crop = cv2.cvtColor(dot_crop, cv2.COLOR_BGR2HSV)
        
        lower_green = np.array([45, 80, 100])
        upper_green = np.array([85, 255, 255])
        mask = cv2.inRange(hsv_crop, lower_green, upper_green)
        
        green_ratio = cv2.countNonZero(mask) / (mask.shape[0] * mask.shape[1])
        threshold = getattr(GameConfig, 'TURN_DOT_THRESHOLD', 0.05)
        
        return green_ratio > threshold

    # Nouveaux paramètres: conf (pour ajuster la confiance à la volée) et is_popup (pour le debug)
    def detect_cards(self, frame: np.ndarray, roi: tuple, conf: float = None, is_popup: bool = False) -> List[dict]:
        y1, y2, x1, x2 = roi
        crop = frame[y1:y2, x1:x2]
        
        # Si aucune confiance n'est spécifiée, on utilise celle par défaut
        if conf is None:
            conf = GameConfig.YOLO_CONFIDENCE
            
        detected_cards = []
        
        results = self.model(crop, conf=conf, verbose=False)
        
        for r in results:
            for box in r.boxes:
                class_id = int(box.cls[0])
                class_name = self.model.names[class_id]
                
                if class_name == "dos":
                    continue
                    
                try:
                    val_str, suit_str = class_name.split('_')
                    val = int(val_str)
                    suit = Suit[suit_str.upper()]
                except Exception as e:
                    print(f"Erreur de parsing pour la classe {class_name}: {e}")
                    continue

                x_min, y_min, x_max, y_max = box.xyxy[0].cpu().numpy()
                center_x_roi = (x_min + x_max) / 2.0
                center_y_roi = (y_min + y_max) / 2.0
                
                abs_x = int(center_x_roi + x1)
                abs_y = int(center_y_roi + y1)
                
                detected_cards.append({
                    "card": Card(val, suit),
                    "x": abs_x,
                    "y": abs_y,
                    "class_name": class_name 
                })
                
        # Sauvegarde d'images ciblées pour déboguer les "faux" popups
        if is_popup:
            if len(detected_cards) > 0:
                cv2.imwrite("debug_popup_detected.jpg", crop) # L'image d'une détection réussie/fausse
            else:
                cv2.imwrite("debug_popup_live.jpg", crop)     # L'image d'un scan à vide
                
        return detected_cards