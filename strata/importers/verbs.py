"""Stratigraphic relation words, in the languages excavation records use.

Every matrix format names its relations differently: "above:" in an ArchEd
list, "Copre" in pyArchInit, ``isAbove`` in Field Desktop, an edge label in
a drawing. :func:`verb` maps any of them to one of Strata's verbs
(see ``strata.importers.load.VERBS``), read as "<a> is <verb> <b>".
"""

from __future__ import annotations

import re
import unicodedata

# Strata verb -> the phrases that mean it. Phrases are compared after
# :func:`_norm` (lower case, no accents, no punctuation, single spaces).
PHRASES: dict[str, list[str]] = {
    "above": [
        # pyArchInit (EN, DE)
        "liegt uber", "liegt ueber",
        # English
        "above", "over", "covers", "overlies", "overlays", "on top of", "later than", "younger than",
        "after", "is above", "is after", "is later than", "isabove", "isafter", "posterior",
        # Italian
        "copre", "sopra", "posteriore a", "posteriore", "si sovrappone a", "sovrapposto a",
        # German
        "uber", "liegt uber", "bedeckt", "junger als", "spater als",
        # French
        "recouvre", "sur", "posterieur a", "au dessus de",
        # Spanish / Portuguese
        "cubre", "sobre", "posterior a", "encima de", "cobre",
    ],
    "below": [
        "liegt unter",
        "below", "under", "covered by", "is covered by", "underlies", "earlier than", "older than",
        "before", "is below", "is before", "is earlier than", "isbelow", "isbefore", "anterior",
        "coperto da", "coperta da", "sotto", "anteriore a", "anteriore", "sottoposto a",
        "unter", "liegt unter", "bedeckt von", "alter als", "fruher als",
        "recouvert par", "sous", "anterieur a", "au dessous de",
        "cubierto por", "cubierta por", "bajo", "debajo de", "coberto por",
    ],
    "cuts": [
        "schneidet",
        "cuts", "cuts through", "truncates", "iscut", "cuts into",
        "taglia", "schneidet", "stort", "coupe", "recoupe", "corta", "corta a",
    ],
    "cut_by": [
        "wird geschnitten", "wird geschnitten von",
        "cut by", "is cut by", "iscutby", "truncated by", "cut_by",
        "tagliato da", "tagliata da", "geschnitten von", "gestort von", "coupe par", "recoupe par",
        "cortado por", "cortada por",
    ],
    "fills": [
        "verfullt",
        "fills", "fill of", "is fill of", "isfillof",
        "riempie", "verfullt", "fullt", "remplit", "comble", "rellena", "rellena a", "preenche",
    ],
    "filled_by": [
        "wird verfullt durch", "wird verfullt von",
        "filled by", "is filled by", "isfilledby", "filled_by",
        "riempito da", "riempita da", "verfullt von", "gefullt von", "rempli par", "comble par",
        "rellenado por", "rellenada por", "preenchido por",
    ],
    "seals": ["seals", "sigilla", "versiegelt", "scelle", "sella"],
    "sealed_by": ["sealed by", "is sealed by", "sigillato da", "versiegelt von", "scelle par", "sellado por"],
    "equals": [
        "equals", "equal", "equal to", "is equal to", "same as", "is same as", "same", "identical to",
        "is identical to", "equivalent to", "is equivalent to", "isequivalentto", "isidenticalto",
        "correlates with", "=", "==",
        "uguale a", "uguale", "corrisponde a", "identico a", "gleich", "identisch mit", "entspricht",
        "egal a", "identique a", "igual a", "igual", "equivale a",
    ],
    "contemporary": [
        "connected to", "bindet an",
        "contemporary", "contemporary with", "is contemporary with", "iscontemporarywith",
        "bonds with", "bonded with", "bonded to", "bonds", "joins", "tied to", "keyed into",
        "si lega a", "legato a", "si lega", "coevo a", "contemporaneo a", "contemporaneo",
        "bindet ein", "verzahnt mit", "gleichzeitig", "zeitgleich", "gleichzeitig mit", "zeitgleich mit",
        "synchrone", "synchrone de", "lie a", "contemporain de", "contemporaneo con", "se une a",
        "se traba con",
    ],
    "abuts": [
        "supports", "stutzt sich auf", "wird gestutzt von", "wird gestuzt von",
        "abuts", "abutted by", "is abutted by", "isabuttedby", "butts", "butted by", "leans against",
        "si appoggia a", "si appoggia", "gli si appoggia", "appoggiato a", "stosst an", "angesetzt an",
        "s appuie contre", "s appuie sur", "appuye contre", "se apoya en", "se le apoya", "adosado a",
        "encosta", "adossé a",
    ],
    "within": ["within", "inside", "part of", "lies within", "lieswithin", "dentro", "innerhalb", "dans"],
    "contains": ["contains", "includes", "contiene", "enthalt", "contient"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.strip().lower().rstrip(":").strip()
    if re.fullmatch(r"[<>=-]+", text):
        return text
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_LOOKUP: dict[str, str] = {}
for _verb, _phrases in PHRASES.items():
    for _p in _phrases + [_verb]:
        _LOOKUP.setdefault(_norm(_p), _verb)


def verb(phrase: str | None) -> str | None:
    """The Strata verb for a recorded relation word, or None if it is not one."""
    if phrase is None:
        return None
    key = _norm(str(phrase))
    if key in _LOOKUP:
        return _LOOKUP[key]
    # camelCase field names (Field Desktop: isCutBy) and "relations.isAbove"
    key = _norm(re.sub(r"(?<!^)(?=[A-Z])", " ", str(phrase).split(".")[-1]))
    return _LOOKUP.get(key) or _LOOKUP.get(key.removeprefix("is "))
