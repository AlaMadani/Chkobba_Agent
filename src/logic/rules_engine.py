import itertools
from typing import List, Tuple
from src.logic.data_models import Card

class RulesEngine:
    @staticmethod
    def get_valid_combinations(played_card: Card, table_cards: List[Card]) -> List[Tuple[Card, ...]]:
        """
        Détermine toutes les prises possibles sur la table pour une carte donnée.
        Applique la règle stricte : Identité > Somme.
        """
        valid_pickups = []
        n = len(table_cards)
        
        # 1. On cherche d'abord TOUTES les combinaisons possibles (de 1 à n cartes)
        for r in range(1, n + 1):
            for combo in itertools.combinations(table_cards, r):
                # Si la somme des valeurs correspond à la carte jouée
                if sum(card.value for card in combo) == played_card.value:
                    valid_pickups.append(combo)
                    
        # 2. Règle métier : Filtre d'Identité
        # Y a-t-il une combinaison composée d'une seule carte ? (La carte identique)
        identity_matches = [combo for combo in valid_pickups if len(combo) == 1]
        
        if identity_matches:
            # Si oui, la règle oblige à prendre la carte identique. 
            # On ignore toutes les additions possibles.
            return identity_matches
            
        # 3. S'il n'y a pas d'identité, on retourne les additions (s'il y en a)
        return valid_pickups