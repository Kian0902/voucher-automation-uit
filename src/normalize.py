
from anyascii import anyascii


def normalize_name(raw_name: str) -> str:
    """
    Normalizes the persons name to be consistent with db key name.

    Transliterate to ASCII (for æ, ø, å, osv), converts tolowercase, drop dots and commas,
    sort the words. Two spellings of the same name give the same key. Anything less than
    key equality is not a match (so no "fuzzy" searches and stuff).

    Args:
        raw_name (str): A persom name as written in the reports or an invoice.

    Returns:
        str: The matching key or "" (aka, nothing)

    Some examples:
            "Bada, Ada"  -> "ada bada"
            "ada BADA"   -> "ada bada"
            "Ada Bada"   -> "ada bada"
            etc
    """

    #print(raw_name)
    ascii_name = anyascii(raw_name)
    lowered_name = ascii_name.lower()
    #print(lowered_name)

    # Every non-letters and digits (comma, period, hyphen, ...etc) becomes a space
    cleaned_characters = []
    for character in lowered_name:
        if character.isalnum():
            cleaned_characters.append(character)
        else:
            cleaned_characters.append(" ")
    cleaned_name = "".join(cleaned_characters)

    # Sorting the words makes "Etternavn, Fornavn" and "Fornavn Etternavn" equal
    words = cleaned_name.split()
    words.sort()
    result_key = " ".join(words)
    return result_key


if __name__ == "__main__":

    # print(normalize_name("Ola Nordmann"))

    # testing...
    assert normalize_name("Nordmann, Ola") == "nordmann ola"
    assert normalize_name("ola NORDMANN") == "nordmann ola"
    assert normalize_name("Nordmann, Ola") == normalize_name("Ola Nordmann")
    assert normalize_name("Bjørn Vik") == normalize_name("Bjorn Vik")
    assert normalize_name("Sæter, Åse") == normalize_name("Saeter Ase")
    assert normalize_name("Anne-Lise Vik") == normalize_name("anne lise vik")
    assert normalize_name("...") == ""
    assert normalize_name("   ") == ""

    print("normalize_name test passed")
