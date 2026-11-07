import subprocess
import cv2
import numpy as np
import time
from src.bot.humanizer import Humanizer

class ADBController:
    def __init__(self, device_id: str = None):
        """
        Initialise le contrôleur ADB. Si device_id est None, prend le premier appareil connecté.
        """
        self.device_id = device_id
        self.base_cmd = ["adb"]
        if self.device_id:
            self.base_cmd.extend(["-s", self.device_id])

    def get_screen_frame(self) -> np.ndarray:
        """
        Capture l'écran via adb exec-out (rapide, directement en mémoire).
        Retourne une image OpenCV (numpy array, format BGR).
        """
        cmd = self.base_cmd + ["exec-out", "screencap", "-p"]
        try:
            # Capture stdout en raw bytes
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            img_bytes, _ = process.communicate()
            
            # Décodage des bytes en image OpenCV
            img_array = np.asarray(bytearray(img_bytes), dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            return frame
        except Exception as e:
            print(f"Erreur lors de la capture d'écran : {e}")
            return None

    def tap(self, x: int, y: int):
        """ Simule un clic tactile aux coordonnées (x, y). """
        cmd = self.base_cmd + ["shell", "input", "tap", str(x), str(y)]
        subprocess.run(cmd)
    def tap_humanized(self, x: int, y: int, radius: int = 15):
        """ Simule un clic avec du bruit gaussien pour éviter la détection. """
        hx, hy = Humanizer.randomize_coordinates(x, y, radius)
        cmd = self.base_cmd + ["shell", "input", "tap", str(hx), str(hy)]
        subprocess.run(cmd)

    def action_7ot(self, card_x: int, card_y: int, btn_7ot_x: int, btn_7ot_y: int):
        """ Exécute la séquence pour JETER une carte. """
        print(f"🤖 Action: 7ot (Jeter carte)")
        
        # 1. Clic sur la carte dans la main
        self.tap_humanized(card_x, card_y)
        Humanizer.random_sleep(0.3, 0.1) # Attente 300ms ± 100ms
        
        # 2. Clic sur le bouton "7ot"
        self.tap_humanized(btn_7ot_x, btn_7ot_y, radius=30) # Bouton généralement plus large
        
    def action_koul(self, card_main_x: int, card_main_y: int, table_coords: List[tuple], btn_koul_x: int, btn_koul_y: int):
        """ Exécute la séquence pour MANGER des cartes. """
        print(f"🤖 Action: Koul (Manger {len(table_coords)} cartes)")
        
        # 1. Clic sur la carte dans la main
        self.tap_humanized(card_main_x, card_main_y)
        Humanizer.random_sleep(0.3, 0.1)
        
        # 2. Clic sur CHAQUE carte de la table concernée
        for (tx, ty) in table_coords:
            self.tap_humanized(tx, ty)
            Humanizer.random_sleep(0.2, 0.05) # Petit délai entre chaque sélection sur la table
            
        # 3. Clic sur le bouton "Koul"
        Humanizer.random_sleep(0.3, 0.1)
        self.tap_humanized(btn_koul_x, btn_koul_y, radius=30)

# --- Test d'affichage (Peut être mis dans main.py) ---
if __name__ == "__main__":
    device = ADBController()
    print("Appuyez sur 'q' dans la fenêtre OpenCV pour quitter.")
    
    while True:
        start_time = time.time()
        frame = device.get_screen_frame()
        
        if frame is not None:
            # Redimensionner pour affichage sur PC (optionnel)
            display_frame = cv2.resize(frame, (0, 0), fx=0.3, fy=0.3)
            cv2.imshow("Chkobba Bot - Vision", display_frame)
            
        # Calcul des FPS pour évaluer la latence ADB
        fps = 1.0 / (time.time() - start_time)
        print(f"FPS: {fps:.2f}", end="\r")
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    cv2.destroyAllWindows()