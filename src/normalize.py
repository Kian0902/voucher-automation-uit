
from anyascii import anyascii


def normalize_words(raw_text: str) -> list:
    """
    The words of a text the way normalize_name() sees them, in the text's own order.

    Transliterates to ASCII, lowercases and turns every character that is not a letter or a
    digit into a space. The order is kept so that neighbouring words can be read as a name.

    Args:
        raw_text (str): Any text, a name or a free text from an invoice.

    Returns:
        list[str]: The normalized words, empty for a text without letters or digits.
    """

    ascii_text = anyascii(raw_text)
    lowered_text = ascii_text.lower()
    cleaned_characters = []
    for character in lowered_text:
        if character.isalnum():
            cleaned_characters.append(character)
        else:
            cleaned_characters.append(" ")
    cleaned_text = "".join(cleaned_characters)
    return cleaned_text.split()


def compact_key(raw_text: str) -> str:
    """
    The letters and digits of a text, normalized, with spaces and punctuation removed.

    Two ways of writing a short code, such as "A-27" and "A27", give the same compact key.

    Args:
        raw_text (str): A name, a code or a reference.

    Returns:
        str: The compact key, "" for a text without letters or digits.
    """

    return "".join(normalize_words(raw_text))


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

    # Sorting the words makes "Etternavn, Fornavn" and "Fornavn Etternavn" equal
    words = normalize_words(raw_name)
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
    assert normalize_words("Nordmann, Ola") == ["nordmann", "ola"]
    assert normalize_words("Hytte for 2 ( Ola Nordmann - uke 40 )") == ["hytte", "for", "2", "ola", "nordmann", "uke", "40"]
    assert normalize_words(" - ") == []
    assert compact_key("A-27") == "a27" and compact_key("A27-H") == "a27h"
    assert compact_key("Tromsø undersøkelsen") == compact_key("Tromsøundersøkelsen")

    print("normalize_name test passed")
