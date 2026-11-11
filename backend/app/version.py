"""Numéro de version de Monteur IA : la seule source.

Lu par l'application (API, mises à jour), par desktop/build.py (installateur, nom du fichier, ressource de
version de l'exe) et par desktop/release.py (tag et titre de la release). Ne mettre ici qu'une ligne
`__version__ = "X.Y.Z"` : les outils de construction la lisent par expression régulière, sans importer l'application.
"""
__version__ = "1.1.0"
