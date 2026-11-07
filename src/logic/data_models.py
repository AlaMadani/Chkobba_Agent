from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Tuple
import itertools

class Suit(Enum):
    DINAR = auto()
    EPEE = auto()
    BATON = auto()
    COUPE = auto()

@dataclass(frozen=True)
class Card:
    value: int  # 1 à 10 (8=Valet, 9=Cavalier, 10=Roi)
    suit: Suit

    def __repr__(self):
        return f"{self.value}_{self.suit.name}"

class ChkobbaLogic:
    @staticmethod
    def get_valid_combinations(played_card: Card, table_cards: List[Card]) -> List[Tuple[Card, ...]]:
        """
        Calcule les combinaisons valides avec une règle unifiée pour TOUTES les cartes (1 à 10) :
        Priorité absolue à l'identité (1 carte de même valeur). S'il n'y en a pas, on cherche les sommes.
        """
        valid_pickups = []
        n = len(table_cards)
        
        # 1. On cherche d'abord toutes les combinaisons possibles (de taille 1 à n)
        for r in range(1, n + 1):
            for combo in itertools.combinations(table_cards, r):
                if sum(card.value for card in combo) == played_card.value:
                    valid_pickups.append(combo)
                    
        # 2. Règle métier : Identité prioritaire
        # On filtre pour voir s'il y a une combinaison de taille 1 (la carte identique)
        identity_matches = [combo for combo in valid_pickups if len(combo) == 1]
        
        if identity_matches:
            # S'il y a l'identité exacte sur la table, la règle oblige à la prendre.
            # On annule et on ignore toutes les autres combinaisons mathématiques.
            return identity_matches
            
        # 3. S'il n'y a pas d'identité, on retourne les combinaisons par addition (s'il y en a)
        return valid_pickups

# --- Test du moteur logique ---
if __name__ == "__main__":
    # État de la table
    table = [
        Card(3, Suit.EPEE),
        Card(4, Suit.DINAR),
        Card(7, Suit.COUPE),
        Card(2, Suit.BATON),
        Card(5, Suit.DINAR)
    ]
    
    # Carte que l'on souhaite jouer
    my_card = Card(7, Suit.DINAR) # Le fameux "Chibani"
    
    print(f"Table : {table}")
    print(f"Carte jouée : {my_card}")
    
    combos = ChkobbaLogic.get_valid_combinations(my_card, table)
    
    print("\nCombinaisons de ramassage possibles :")
    for combo in combos:
        print(f" -> {combo}")