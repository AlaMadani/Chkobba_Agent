from typing import List, Tuple, Dict, Optional
from src.logic.data_models import Card, Suit


class DecisionMaker:
    @staticmethod
    def evaluate_move(
        action_type: str,
        play_card: Card,
        eaten_cards: Tuple[Card, ...],
        table_size: int,
        my_hand: List[Card],
        memory_cartes_sorties: Optional[List[Card]] = None,
        game_state: Optional[Dict] = None,
    ) -> int:
        """Calcule l'intérêt mathématique d'un coup avec Dynamic Reward Shaping."""
        score = 0

        # Initialisation par défaut si game_state n'est pas fourni
        sab3a_7aya_out = False
        dinars_restants = 10
        sevens_restants = 4
        sixes_restants = 4
        cartes_restantes = 40

        # Nouvelles variables de possession (Dynamic Reward Shaping)
        my_sevens = 0
        opp_sevens = 0
        my_sixes = 0
        opp_sixes = 0
        my_dinars = 0
        opp_dinars = 0

        if game_state:
            sab3a_7aya_out = game_state.get("sab3a_7aya_out", False)
            dinars_restants = game_state.get("dinars_restants", 10)
            sevens_restants = game_state.get("sevens_restants", 4)
            sixes_restants = game_state.get("sixes_restants", 4)
            cartes_restantes = game_state.get("cartes_restantes", 40)

            my_sevens = game_state.get("my_sevens", 0)
            opp_sevens = game_state.get("opp_sevens", 0)
            my_sixes = game_state.get("my_sixes", 0)
            opp_sixes = game_state.get("opp_sixes", 0)
            my_dinars = game_state.get("my_dinars", 0)
            opp_dinars = game_state.get("opp_dinars", 0)

        if action_type == "koul":
            all_involved_cards = list(eaten_cards) + [play_card]

            # 1. Évaluation des points de capture
            for card in all_involved_cards:
                # Sab3a 7aya (7 de Dinars) : Priorité absolue
                if card.value == 7 and card.suit == Suit.DINAR:
                    score += 500

                # Bermila : Les 7
                elif card.value == 7:
                    # Si la Bermila est DÉJÀ GAGNÉE ou DÉJÀ PERDUE, on lâche l'affaire (vaut juste 10 pts de Karta)
                    if my_sevens >= 3 or opp_sevens >= 3:
                        score += 0  # L'attachement tombe à 0 (recevra juste les +10 de Karta en bas)
                    else:
                        # Toujours disputée : forte valeur
                        score += 100 + (sevens_restants * 10)

                # Bermila (Tie-breakers) : Les 6
                elif card.value == 6:
                    # Si les 7 sont tous sortis et qu'il y a égalité (2 partout)
                    bermila_tied = (
                        sevens_restants == 0 and my_sevens == 2 and opp_sevens == 2
                    )
                    if bermila_tied:
                        if my_sixes >= 3 or opp_sixes >= 3:
                            score += 0  # Déjà gagné ou perdu par les 6
                        else:
                            score += 80  # Crucial pour le tie-break
                    else:
                        score += 30  # Valeur d'anticipation au cas où

                # Dinari : Chaque carte de Dinar
                if card.suit == Suit.DINAR and card.value != 7:
                    # Si le point du Dinari est DÉJÀ GAGNÉ (6+) ou PERDU (6+ chez l'adversaire)
                    if my_dinars >= 6 or opp_dinars >= 6:
                        score += 0  # L'attachement tombe à 0
                    else:
                        if dinars_restants > 2:
                            score += 40
                        else:
                            score += 30

                # Karta : Volume de cartes (10 points de base par carte mangée/jouée)
                score += 10

            # 2. Vérification de la Chkobba
            if len(eaten_cards) == table_size:
                if cartes_restantes > 0:
                    score += 1000

        elif action_type == "7ot":
            # Pénalités si on jette des cartes précieuses, ajustées selon ce qui est DÉJÀ SÉCURISÉ

            if play_card.value == 7 and play_card.suit == Suit.DINAR:
                score -= 500

            elif play_card.value == 7:
                # Jeter un 7 est grave... SAUF si la Bermila est déjà pliée (gagnée ou perdue)
                if my_sevens >= 3 or opp_sevens >= 3:
                    score -= 10  # Pas grave
                else:
                    score -= 100  # Très grave

            if play_card.suit == Suit.DINAR and play_card.value != 7:
                # Jeter un dinar est mauvais... SAUF si le point Dinari est déjà plié
                if my_dinars >= 6 or opp_dinars >= 6:
                    score -= 10  # Pas grave
                else:
                    score -= 30  # Grave

            # Bonus pour se débarrasser des "déchets" (1, 2, 3 hors dinar)
            if play_card.value <= 3 and play_card.suit != Suit.DINAR:
                score += 5

            # 3. L'INTÉGRATION DU CARD COUNTING (Sécurité contre la Chkobba)
            if memory_cartes_sorties is not None:
                valeur_jetee = play_card.value
                sorties = sum(
                    1 for c in memory_cartes_sorties if c.value == valeur_jetee
                )
                en_main = sum(1 for c in my_hand if c.value == valeur_jetee)
                total_visibles = sorties + en_main

                # 4 cartes visibles = l'adversaire n'en a plus. Risque 0.
                if total_visibles == 4:
                    score += 200
                # 3 cartes visibles = Risque modéré (1 seule carte dangereuse restante)
                elif total_visibles == 3:
                    score -= 50
                # Moins de 3 = Haut risque de Chkobba
                else:
                    score -= 150

        return score

    @classmethod
    def get_best_move(
        cls,
        hand: List[Card],
        table: List[Card],
        get_combinations_func,
        memory_cartes_sorties: Optional[List[Card]] = None,
        game_state: Optional[Dict] = None,
    ) -> Dict:
        """
        Teste toutes les cartes en main et retourne la meilleure action à faire.
        Format : {"action": "koul" ou "7ot", "play_card": Card, "eat_cards": Tuple}
        """
        best_score = -99999
        best_move = None

        for my_card in hand:
            # 1. Vérifier si on peut manger
            combos = get_combinations_func(my_card, table)

            if combos:
                for combo in combos:
                    score = cls.evaluate_move(
                        "koul",
                        my_card,
                        combo,
                        len(table),
                        hand,
                        memory_cartes_sorties,
                        game_state,
                    )
                    if score > best_score:
                        best_score = score
                        best_move = {
                            "action": "koul",
                            "play_card": my_card,
                            "eat_cards": combo,
                        }
            else:
                # 2. Si on ne peut rien manger, il faut la jeter
                score = cls.evaluate_move(
                    "7ot",
                    my_card,
                    (),
                    len(table),
                    hand,
                    memory_cartes_sorties,
                    game_state,
                )
                if score > best_score:
                    best_score = score
                    best_move = {"action": "7ot", "play_card": my_card, "eat_cards": ()}

        if best_move:
            print(
                f"🧠 L'IA a choisi {best_move['action']} avec {best_move['play_card']} (Score IA: {best_score})"
            )

        return best_move
