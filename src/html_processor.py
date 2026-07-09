
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def _clean(raw_text):
    """
    Normalize whitespace in a text fragment.

    The HTML is pretty-printed: values contain newlines, tabs and
    non-breaking spaces. Collapse every whitespace run to one space.

    Args:
        raw_text (str | None): Text as found in the document.

    Returns:
        str | None: Cleaned text, or None if empty/absent.

    Example:
        "9037\\n\\t Tromsø"  -> "9037 Tromsø"
        "   "                -> None
    """

    if raw_text is None:
        return None

    # \xa0 is a non-breaking space, treated like any whitespace
    collapsed_text = re.sub(r"\s+", " ", raw_text.replace("\xa0", " "))
    stripped_text = collapsed_text.strip()
    if stripped_text == "":
        return None

    return stripped_text


def _element_text(element):
    """
    All text inside an element (including children), whitespace-normalized.

    Args:
        element (Element | None): The element to read.

    Returns:
        str | None: Cleaned text content, or None.
    """

    if element is None:
        return None

    joined_text = "".join(element.itertext())
    return _clean(joined_text)


def _load_tree(path):
    """
    Parse the XHTML file and strip namespaces from all tags.

    The files are well-formed XML with the XHTML namespace. Stripping the
    namespace once lets every later lookup use plain tag names ("td", "h2").

    Args:
        path (str | Path): Path to the HTML invoice file.

    Returns:
        Element: The root <html> element.
    """

    root = ET.parse(path).getroot()

    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}")[-1]

    return root


def _parent_map(root):
    """
    Map every element to its parent (ElementTree has no parent pointers).

    Args:
        root (Element): The root element.

    Returns:
        dict: {child Element: parent Element}
    """

    parents = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    return parents


def _find_label(root, label):
    """
    Find the h2/h3/b element whose own text equals `label`.

    Args:
        root (Element): Element to search within.
        label (str): The template label, e.g. "Fakturanummer".

    Returns:
        Element | None: The label element, or None if this invoice
                        does not have that section.
    """

    for element in root.iter():
        if element.tag not in ("h2", "h3", "b"):
            continue
        own_text = _clean(element.text)
        if own_text == label:
            return element

    return None


def _text_after_label(container, label_element):
    """
    All text inside `container` that appears AFTER `label_element`.

    This is how the template places scalar values:
        <td><h2>Fakturatype</h2>380</td>
        <td><h3><b>Fakturanummer</b></h3><br />41481</td>

    Collection stops at the next h2/h3 heading: some sections share one
    <td> (e.g. "Kontraktsidentifikator" and "Tilleggsdokumentasjon"), and
    text after the next heading belongs to that next section.

    Args:
        container (Element): The element holding label and value (a <td>).
        label_element (Element): The label whose text must be excluded.

    Returns:
        str | None: The value text, or None.
    """

    pieces = []
    state = {"passed_label": False, "stopped": False}

    def walk(element):
        if state["stopped"]:
            return

        if element is label_element:
            # Skip the label's own text, but everything after it counts
            state["passed_label"] = True
        elif state["passed_label"] and element.tag in ("h2", "h3"):
            # A new section starts here - its text is not our value
            state["stopped"] = True
            return
        else:
            if state["passed_label"] and element.text:
                pieces.append(element.text)
            for child in element:
                walk(child)

        # tail = text between this element's closing tag and the next tag
        if state["passed_label"] and not state["stopped"] and element.tail:
            pieces.append(element.tail)

    for child in container:
        walk(child)

    joined_text = " ".join(pieces)
    return _clean(joined_text)


def _labeled_value(root, label, parents):
    """
    Value of a template field: text following `label` inside the same <td>.

    Args:
        root (Element): Element to search within.
        label (str): Template label, e.g. "Deres referanse".
        parents (dict): Parent map from _parent_map().

    Returns:
        str | None: The field value, or None if the section is absent.
    """

    label_element = _find_label(root, label)
    if label_element is None:
        return None

    # Climb to the enclosing <td> (label may sit inside h3/small wrappers)
    container = label_element
    while container in parents and container.tag != "td":
        container = parents[container]

    return _text_after_label(container, label_element)


def _section_td(root, label, parents):
    """
    The <td> element of a whole template section (e.g. "Leverandør").

    Args:
        root (Element): The root element.
        label (str): The section's h2 label.
        parents (dict): Parent map from _parent_map().

    Returns:
        Element | None: The section's <td>, or None if absent.
    """

    label_element = _find_label(root, label)
    if label_element is None:
        return None

    container = label_element
    while container in parents and container.tag != "td":
        container = parents[container]

    if container.tag != "td":
        return None

    return container


def _strip_prefix_and_scheme(text, prefix):
    """
    Remove a label prefix and a trailing scheme bracket from a value.

    Args:
        text (str | None): Raw value, e.g. "Organisasjonsnummer: 976308344 [0192]".
        prefix (str): Label prefix to remove, e.g. "Organisasjonsnummer:".

    Returns:
        str | None: The bare value, e.g. "976308344".
    """

    if text is None:
        return None

    without_prefix = text
    if without_prefix.startswith(prefix):
        without_prefix = without_prefix[len(prefix):]

    # Trailing "[0192]" style scheme id is rendering noise
    without_scheme = re.sub(r"\s*\[\w+\]\s*$", "", without_prefix)

    return _clean(without_scheme)


def _prefixed_value(container, prefix):
    """
    Find the element whose text starts with `prefix` and return the rest.

    Used for the small labeled rows in party blocks:
        <small>Organisasjonsnummer: 976308344 [0192]</small>
        <span class="UBLElectronicMail">Epost: leif@uit.no</span>

    Args:
        container (Element | None): Element to search within.
        prefix (str): The label prefix, e.g. "Epost:".

    Returns:
        str | None: The value after the prefix, or None.
    """

    if container is None:
        return None

    for element in container.iter():
        element_text = _element_text(element)
        if element_text is not None and element_text.startswith(prefix):
            return _strip_prefix_and_scheme(element_text, prefix)

    return None


def _class_text(container, class_name):
    """
    Whitespace-normalized text of the first element with a CSS class.

    Args:
        container (Element | None): Element to search within.
        class_name (str): The class attribute value, e.g. "UBLStreetName".

    Returns:
        str | None: The element's text, or None.
    """

    if container is None:
        return None

    found = container.find(f".//*[@class='{class_name}']")
    return _element_text(found)


def _amount(text):
    """
    Normalize a displayed amount to a bare amount string.

    Args:
        text (str | None): Displayed amount, e.g. "255.00 NOK".

    Returns:
        str | None: The bare amount string, e.g. "255.00" (never parsed
                    to float). If the text does not match the strict
                    "<number> <CURRENCY>" pattern it is returned verbatim
                    rather than mangled.
    """

    if text is None:
        return None

    match = re.fullmatch(r"(-?[\d.,]+)\s*[A-Z]{3}", text)
    if match is None:
        return text

    return match.group(1)


def _split_postal_city(text):
    """
    Split the rendered "postcode city" string into its two parts.

    Args:
        text (str | None): e.g. "9037 Tromsø".

    Returns:
        tuple[str | None, str | None]: (postal_zone, city). Norwegian
        postcodes are exactly 4 digits; anything else goes fully to city.

    Example:
        "9037 Tromsø" -> ("9037", "Tromsø")
        "Tromsø"      -> (None, "Tromsø")
    """

    if text is None:
        return None, None

    match = re.fullmatch(r"(\d{4})\s+(.+)", text)
    if match is None:
        return None, text

    postal_zone = match.group(1)
    city = match.group(2)
    return postal_zone, city


def _header(root, parents):
    """
    Extract header level fields from the rendered invoice.

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        dict: Header fields with corresponding values. due_date is not
              here — the template renders it in the payment table
              ("Forfallsdato"), so parse_invoice_html fills it from there.
    """

    period_start = None
    period_end = None
    period_text = _labeled_value(root, "Periode", parents)
    if period_text is not None:
        # Rendered as "Fra: 2026-07-03 Til: 2026-08-03"
        match = re.search(r"Fra:\s*(\S+)\s*Til:\s*(\S+)", period_text)
        if match is not None:
            period_start = match.group(1)
            period_end = match.group(2)

    # The contract renders as a document reference: "- ID: 385955"
    contract_reference = _labeled_value(root, "Kontraktsidentifikator", parents)
    if contract_reference is not None:
        contract_match = re.match(r"-\s*ID:\s*(.+)", contract_reference)
        if contract_match is not None:
            contract_reference = contract_match.group(1)

    result_headers = {
        "invoice_id": _labeled_value(root, "Fakturanummer", parents),
        "issue_date": _labeled_value(root, "Utstedelsesdato", parents),
        "due_date": None,
        "invoice_type_code": _labeled_value(root, "Fakturatype", parents),
        "currency": _labeled_value(root, "Dokumentets valutakode", parents),
        "buyer_reference": _labeled_value(root, "Deres referanse", parents),
        "accounting_cost": _labeled_value(root, "Konteringsstreng", parents),
        "order_reference": _labeled_value(root, "Ordre-/bestillingsnummer", parents),
        "sales_order_id": None,          # not rendered by this template
        "contract_reference": contract_reference,
        "originator_reference": None,    # not rendered by this template
        "period_start": period_start,
        "period_end": period_end
    }
    return result_headers


def _party(root, section_label, contact_label, parents):
    """
    Extract party details for the supplier or customer.

    Args:
        root (Element): The root <html> element.
        section_label (str): "Leverandør" or "Kunde".
        contact_label (str): "Leverandørs kontaktinformasjon" or
                             "Kundens kontaktinformasjon".
        parents (dict): Parent map from _parent_map().

    Returns:
        dict | None: Party fields (name, org_nr, endpoint_id, party_id,
                     mva_nr, contact_name, contact_email, contact_phone),
                     or None if the section is absent.
    """

    section = _section_td(root, section_label, parents)
    if section is None:
        return None

    # "MVA nr." sometimes has the register name glued on by the template
    # ("NO976308344MVAForetaksregisteret") - deterministic rendering noise
    mva_nr = _prefixed_value(section, "MVA nr.:")
    if mva_nr is not None and mva_nr.endswith("Foretaksregisteret"):
        mva_nr = mva_nr[: -len("Foretaksregisteret")]

    contact_section = _section_td(root, contact_label, parents)
    contact_name = _class_text(contact_section, "UBLName")
    contact_phone = _prefixed_value(contact_section, "Telefon:")
    contact_email = _prefixed_value(contact_section, "Epost:")

    result_party = {
        "name": _prefixed_value(section, "Juridisk navn:"),
        "org_nr": _prefixed_value(section, "Organisasjonsnummer:"),
        "endpoint_id": _prefixed_value(section, "Endepunkt id:"),
        "party_id": _prefixed_value(section, "PartyID:"),
        "mva_nr": mva_nr,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone
    }

    return result_party


def _delivery(root, parents):
    """
    Extract delivery info from the "Leveringsdato"/"Leveringssted" sections.

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        dict | None: Delivery fields (date, party_name, location_id,
                     street, additional_street, city, postal_zone,
                     country), or None if the invoice has neither
                     delivery section.
    """

    date = _labeled_value(root, "Leveringsdato", parents)
    location = _section_td(root, "Leveringssted", parents)

    if date is None and location is None:
        return None

    postal_zone, city = _split_postal_city(_class_text(location, "UBLCityName"))

    # Country renders as a bare nested <div>NO</div> inside the section
    country = None
    if location is not None:
        for div in location.findall(".//div/div"):
            div_text = _clean(div.text)
            if div_text is not None and len(list(div)) == 0 and re.fullmatch(r"[A-Z]{2}", div_text):
                country = div_text
                break

    result_delivery = {
        "date": date,
        "party_name": None,    # not rendered by this template
        "location_id": _prefixed_value(location, "Lokasjonsidentifikator:"),
        "street": _class_text(location, "UBLStreetName"),
        "additional_street": _class_text(location, "UBLAdditionalStreetName"),
        "city": city,
        "postal_zone": postal_zone,
        "country": country
    }

    return result_delivery


def _label_value_rows(root, first_label, parents):
    """
    Read a two-row template table: one <tr> of <b> labels, then one <tr>
    of values, matched column by column. Used for "Totaler" and
    "Betalingsinformasjon".

    Args:
        root (Element): The root <html> element.
        first_label (str): A label known to sit in the label row,
                           e.g. "Sum linjebeløp".
        parents (dict): Parent map from _parent_map().

    Returns:
        dict: {label text: value text} for every non-empty column pair.
    """

    label_element = _find_label(root, first_label)
    if label_element is None:
        return {}

    # Climb to the label row (<tr>), then take the next row as values
    label_row = label_element
    while label_row in parents and label_row.tag != "tr":
        label_row = parents[label_row]

    table = parents[label_row]
    rows = list(table)
    label_row_index = rows.index(label_row)
    if label_row_index + 1 >= len(rows):
        return {}
    value_row = rows[label_row_index + 1]

    label_cells = label_row.findall("td")
    value_cells = value_row.findall("td")

    result_rows = {}
    for cell_index in range(min(len(label_cells), len(value_cells))):
        label_text = _element_text(label_cells[cell_index])
        value_text = _element_text(value_cells[cell_index])
        if label_text is not None:
            result_rows[label_text] = value_text

    return result_rows


def _payment(root, parents):
    """
    Extract payment information from the "Betalingsinformasjon" table.

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        dict: Payment fields (means_code, kid, accounts, terms), plus
              "due_date" - the template renders the due date here as
              "Forfallsdato"; parse_invoice_html moves it to the top level.
    """

    fields = _label_value_rows(root, "Betalingstype", parents)

    # Account renders as "47506227050 (BBAN)" - number plus scheme
    accounts = []
    account_text = fields.get("Bankkontonummer")
    if account_text is not None:
        match = re.fullmatch(r"(\S+)\s*\((\w+)\)", account_text)
        if match is not None:
            accounts.append({"number": match.group(1), "scheme": match.group(2)})
        else:
            accounts.append({"number": account_text, "scheme": None})

    result_payment = {
        "means_code": fields.get("Betalingstype"),
        "kid": fields.get("Betalingsref"),
        "accounts": accounts,
        "terms": _labeled_value(root, "Betalingsbetingelser:", parents),
        "due_date": fields.get("Forfallsdato")
    }

    return result_payment


def _totals(root, parents):
    """
    Extract the monetary totals from the "Totaler" and "Avgiftstotaler"
    sections.

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        dict: Total amounts as bare strings ("255.00 NOK" is normalized
              to "255.00", never parsed to float), plus a tax_subtotals
              list with the per-category breakdown.
    """

    fields = _label_value_rows(root, "Sum linjebeløp", parents)

    # Per tax category breakdown from the "Avgiftstotaler" table rows.
    # Cell layout: [category + percent, exemption text, taxable, -, tax]
    tax_subtotals = []
    for row in root.findall(".//tr[@class='TAXInformation']"):
        cells = row.findall("td")
        if len(cells) < 5:
            continue

        category_text = _element_text(cells[0])
        category = None
        percent = None
        if category_text is not None:
            # e.g. "E (= Fritatt for mva ), 0.00%"
            category = category_text.split(" ")[0].rstrip(",")
            percent_match = re.search(r"([\d.]+)\s*%", category_text)
            if percent_match is not None:
                percent = percent_match.group(1)

        tax_subtotals.append({
            "taxable_amount": _amount(_element_text(cells[2])),
            "tax_amount": _amount(_element_text(cells[4])),
            "category": category,
            "percent": percent,
            "exemption_reason": _element_text(cells[1])
        })

    result_totals = {
        "line_extension_amount": _amount(fields.get("Sum linjebeløp")),
        "tax_exclusive_amount": _amount(fields.get("Totalbeløp eksklusiv MVA")),
        "tax_inclusive_amount": _amount(fields.get("Totalbeløp inklusive MVA")),
        "payable_amount": _amount(fields.get("Betalingsbeløp")),
        "tax_amount": _amount(fields.get("Total avgift")),
        "prepaid_amount": _amount(fields.get("Forhåndsbetalt")),
        "payable_rounding_amount": _amount(_labeled_value(root, "Øreavrundingsbeløp", parents)),
        "allowance_total_amount": _amount(fields.get("Sum rabatter")),
        "charge_total_amount": _amount(fields.get("Sum gebyrer")),
        "tax_subtotals": tax_subtotals
    }

    return result_totals


def _lines(root, parents):
    """
    Extract the invoice lines from the line-items table.

    The header row (class UBLInvoiceLineHeader) fixes the column order:
    Linje | Selgers varenr | Navn | Kvantitet | Enhet | Enhetspris |
    Avgift | (allowance) | Linjebeløp.
    Each data row is followed by a detail row that may carry the item
    description ("Beskrivelse").

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        list[dict]: One dict per line (line_id, item_name, description,
                    quantity, unit_code, unit_price, amount, tax_category,
                    tax_percent, allowances).
    """

    header_row = root.find(".//tr[@class='UBLInvoiceLineHeader']")
    if header_row is None:
        return []

    table = parents[header_row]
    rows = list(table)
    start_index = rows.index(header_row) + 1

    result_lines = []
    for row in rows[start_index:]:
        cells = row.findall("td")

        # Data row: full column set and a line number in the first cell
        is_data_row = len(cells) >= 9 and _element_text(cells[0]) is not None
        if is_data_row:
            # Unit cell has decoration ("EA <small>..."), first token is the code
            unit_code = None
            unit_text = _element_text(cells[4])
            if unit_text is not None:
                unit_code = unit_text.split(" ")[0]

            # Tax cell renders as "S, 25.00%"
            tax_category = None
            tax_percent = None
            tax_text = _element_text(cells[6])
            if tax_text is not None:
                tax_category = tax_text.split(",")[0].strip()
                percent_match = re.search(r"([\d.]+)\s*%", tax_text)
                if percent_match is not None:
                    tax_percent = percent_match.group(1)

            # Allowance cell renders as "Rabatt: 1378.60 NOK" + reason div.
            # The template shows reason and amount only (no percent/base).
            allowances = []
            allowance_text = _element_text(cells[7])
            if allowance_text is not None:
                allowance_match = re.match(r"(.+?):\s*(.+)", allowance_text)
                if allowance_match is not None:
                    reason = allowance_match.group(1)
                    amount_part = allowance_match.group(2)
                    # Reason repeats after the amount ("... NOK Rabatt") - drop it
                    if amount_part.endswith(reason):
                        amount_part = amount_part[: -len(reason)].strip()
                    allowances.append({
                        "is_charge": None,
                        "reason": reason,
                        "percent": None,
                        "amount": _amount(amount_part),
                        "base_amount": None
                    })

            # Unit price renders as "255.00 NOK" possibly followed by "per 1"
            unit_price = None
            price_text = _element_text(cells[5])
            if price_text is not None:
                price_match = re.match(r"(-?[\d.,]+)\s*[A-Z]{3}", price_text)
                if price_match is not None:
                    unit_price = price_match.group(1)
                else:
                    unit_price = price_text

            result_line = {
                "line_id": _element_text(cells[0]),
                "item_name": _element_text(cells[2]),
                "description": None,
                "quantity": _element_text(cells[3]),
                "unit_code": unit_code,
                "unit_price": unit_price,
                "amount": _amount(_element_text(cells[8])),
                "tax_category": tax_category,
                "tax_percent": tax_percent,
                "allowances": allowances
            }
            result_lines.append(result_line)
            continue

        # Detail row: may carry the description for the previous line
        if len(result_lines) > 0:
            description_label = _find_label(row, "Beskrivelse")
            if description_label is not None:
                description = _clean(description_label.tail)
                result_lines[-1]["description"] = description

    return result_lines


def _attachments(root, parents):
    """
    Extract attachment metadata from the "Tilleggsdokumentasjon" section.

    The template renders each document as " - ID: <id>" followed by a
    <small> block naming the mime type and filename. The binary content
    itself is never present in the HTML.

    Args:
        root (Element): The root <html> element.
        parents (dict): Parent map from _parent_map().

    Returns:
        list[dict]: One dict per document (doc_id, description, filename,
                    mime_code).
    """

    section = _section_td(root, "Tilleggsdokumentasjon", parents)
    if section is None:
        return []

    heading = _find_label(section, "Tilleggsdokumentasjon")

    result = []
    inside_section = False
    for element in section:
        # The section's own <td> can also hold other headings (e.g.
        # "Kontraktsidentifikator") - only text after OUR heading counts
        if element is heading:
            inside_section = True
        elif inside_section and element.tag in ("h2", "h3"):
            break
        if not inside_section:
            continue

        # A <small> block holds the details (description, mime type,
        # filename) of the document opened by the PREVIOUS element's tail,
        # so it must be applied before this element's own tail is read.
        if element.tag == "small" and len(result) > 0:
            small_text = _element_text(element)
            if small_text is not None:
                desc_match = re.search(r"\(\s*Dokumenttype\s+(.+?),\s*Dokumenttype, kodet:", small_text)
                if desc_match is not None:
                    result[-1]["description"] = desc_match.group(1)
                mime_match = re.search(r"Mimecode til dette objektet er:\s*(\S+)", small_text)
                if mime_match is not None:
                    result[-1]["mime_code"] = mime_match.group(1)
                file_match = re.search(r"Filnavn:\s*(.+)$", small_text)
                if file_match is not None:
                    result[-1]["filename"] = file_match.group(1)

        # Each document starts as tail text " - ID: <id>" after an element
        tail_text = _clean(element.tail)
        if tail_text is not None:
            id_match = re.match(r"-\s*ID:\s*(.+)", tail_text)
            if id_match is not None:
                result.append({
                    "doc_id": id_match.group(1),
                    "description": None,
                    "filename": None,
                    "mime_code": None
                })

    return result


def parse_invoice_html(path):
    """
    Parse one HTML invoice rendering into a JSON-serializable dict.

    Args:
        path (str | Path): Path to the HTML invoice file.

    Returns:
        dict: Extracted invoice data. Missing fields are None.

    Raises:
        ValueError: If the file is not the known invoice template.
        xml.etree.ElementTree.ParseError: If the file is not well-formed.
    """

    root = _load_tree(path)
    parents = _parent_map(root)

    # Refuse to "extract" from an unknown layout
    has_invoice_number = _find_label(root, "Fakturanummer") is not None
    has_line_table = root.find(".//tr[@class='UBLInvoiceLineHeader']") is not None
    if not has_invoice_number or not has_line_table:
        raise ValueError(f"Unknown HTML invoice template: {path}")

    result_header = _header(root, parents)
    result_payment = _payment(root, parents)

    # The template renders the due date inside the payment table
    result_header["due_date"] = result_payment.pop("due_date")

    result_invoice = {
        "source_file": Path(path).name,
        **result_header,
        "supplier": _party(root, "Leverandør", "Leverandørs kontaktinformasjon", parents),
        "customer": _party(root, "Kunde", "Kundens kontaktinformasjon", parents),
        "delivery": _delivery(root, parents),
        "payment": result_payment,
        "totals": _totals(root, parents),
        "lines": _lines(root, parents),
        "attachments": _attachments(root, parents)
    }

    return result_invoice


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"

    invoices = {}
    for html_file in sorted(data_dir.glob("*.html")):
        invoice = parse_invoice_html(html_file)
        invoices[html_file.name] = invoice
        print(f"===== {html_file.name} =====")
        print(json.dumps(invoice, indent=2, ensure_ascii=False))

