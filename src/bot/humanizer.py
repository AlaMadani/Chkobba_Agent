import time
import random

class Humanizer:
    @staticmethod
    def random_sleep(base_delay: float = 0.3, variance: float = 0.1):
        """ Met le script en pause avec un délai aléatoire pour simuler l'hésitation humaine. """
        sleep_time = random.uniform(base_delay - variance, base_delay + variance)
        # On s'assure de ne jamais avoir de délai négatif
        time.sleep(max(0.05, sleep_time))

    @staticmethod
    def randomize_coordinates(x: int, y: int, radius: int = 15) -> tuple:
        """
        Applique un bruit gaussien (Gaussian Noise) aux coordonnées.
        La plupart des clics seront proches du centre (x,y), mais avec une légère variation naturelle.
        Le 'radius' est la taille de la zone cliquable (ex: taille du bouton ou de la carte).
        """
        # random.gauss(mu, sigma) : mu est le centre, sigma est l'écart-type
        new_x = int(random.gauss(x, radius / 3.0))
        new_y = int(random.gauss(y, radius / 3.0))
        return new_x, new_y