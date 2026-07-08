

import json
import xml.etree.ElementTree as ET
from pathlib import Path
import snoop

Name_Spaces = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

# @snoop
def _text(node, element_path):
    """
    Extract text of the first element at 'path', or None if absent/empty.

    Args:
        node (Element | None):  The XML element to search within.
        path (str):  ET-style path to the target child element or tag.
                     Example: "cbc:IssueDate"

    Returns:
        str | None:  Stripped text content or None if empty
    
    Example:
        <cbc:ID> abc123 </cbc:ID>   -> "abc123"
        <cbc:ID></cbc:ID>           -> None
        element not present         -> None
    """

    
    if node is None:
        return None

    # Find 1st element matching path belong to accepted name spaces
    ele = node.find(element_path, Name_Spaces)
    if ele is None:
        return None


    # Extract element text as is
    raw_text = ele.text
    if raw_text is None:
        raw_text = ""


    # Edge case: raw_text is missing info but not empty. E.g., only whitespaces...
    stripped_text = raw_text.strip()
    if stripped_text == "":
        return None

    return stripped_text


def _header(root):
    """
    Extract header level fields from file.

    Args:
        root (Element): The root element "Invoice" of xml tree.

    Returns:
        dict: Header fields with corresponding values
    """

    result_headers = {
        "invoice_id": _text(root, "cbc:ID"),
        "issue_date": _text(root, "cbc:IssueDate"),
        "due_date": _text(root, "cbc:DueDate"),
        "invoice_type_code": _text(root, "cbc:InvoiceTypeCode"),
        "currency": _text(root, "cbc:DocumentCurrencyCode"),
        "buyer_reference": _text(root, "cbc:BuyerReference"),
        "accounting_cost": _text(root, "cbc:AccountingCost"),
        "order_reference": _text(root, "cac:OrderReference/cbc:ID"),
        "sales_order_id": _text(root, "cac:OrderReference/cbc:SalesOrderID"),
        "contract_reference": _text(root, "cac:ContractDocumentReference/cbc:ID"),
        "originator_reference": _text(root, "cac:OriginatorDocumentReference/cbc:ID"),
        "period_start": _text(root, "cac:InvoicePeriod/cbc:StartDate"),
        "period_end": _text(root, "cac:InvoicePeriod/cbc:EndDate")
    }
    return result_headers


def _party(root, acc_party: str):
    """
    Extract party details for the supplier or customer.

    Args:
        root (Element):  The root element "Invoice" of xml tree.
        acc_party (str): Which party to extract
                         - "AccountingSupplierParty"
                         - "AccountingCustomerParty".

    Returns:
        dict | None: Party fields with correspnoding values

    Example:
        <cbc:RegistrationName>Company AS</cbc:RegistrationName>,
        <cbc:CompanyID>123456789</cbc:CompanyID>
        -> {"name": "Company AS", "org_nr": "123456789", "endpoint_id": None}
    """

    # Find first element matching path belong to accepted name spaces
    party = root.find(f"cac:{acc_party}/cac:Party", Name_Spaces)
    if party is None:
        return None
    
    result_party = {
        "name": _text(party, "cac:PartyLegalEntity/cbc:RegistrationName"),
        "org_nr": _text(party, "cac:PartyLegalEntity/cbc:CompanyID"),
        "endpoint_id": _text(party, "cbc:EndpointID"),
        "party_id": _text(party, "cac:PartyIdentification/cbc:ID"),
        "mva_nr": _text(party, "cac:PartyTaxScheme/cbc:CompanyID"),
        "contact_name": _text(party, "cac:Contact/cbc:Name"),
        "contact_email": _text(party, "cac:Contact/cbc:ElectronicMail"),
        "contact_phone": _text(party, "cac:Contact/cbc:Telephone")
    }

    return result_party


def _payment(root):
    """
    Extract payment information from the invoice.

    Args:
        root (Element): The root element "Invoice" of xml tree.

    Returns:
        dict: Payment information 
    """

    # Find all <cac:PaymentMeans> blocks in the invoice (can 0, 1 or multiple)
    payment_blocks = root.findall("cac:PaymentMeans", Name_Spaces)

    # Get all account number per block with perserved order.
    # The schemeID attribute tells which form the number has (BBAN or IBAN).
    accounts = []
    seen_numbers = []
    for block in payment_blocks:
        account_el = block.find("cac:PayeeFinancialAccount/cbc:ID", Name_Spaces)
        number = _text(block, "cac:PayeeFinancialAccount/cbc:ID")

        # Only append unique accounts
        if number is not None and number not in seen_numbers:
            seen_numbers.append(number)
            accounts.append({
                "number": number,
                "scheme": account_el.get("schemeID") if account_el is not None else None
            })

    # Means code and KID come from the first block (repeats are identical)
    if len(payment_blocks) > 0:
        first_block = payment_blocks[0]
    else:
        first_block = None


    result_payment = {
        "means_code": _text(first_block, "cbc:PaymentMeansCode"),
        "kid": _text(first_block, "cbc:PaymentID"),
        "accounts": accounts,
        # Free text payment terms, e.g. "Netto pr. 30 dager. Forsinkelsesrente 12.00%"
        "terms": _text(root, "cac:PaymentTerms/cbc:Note")
    }

    return result_payment


def _totals(root):
    """
    Extract the monetary totals of the invoice.

    Args:
        root (Element): The root element "Invoice" of xml tree.

    Returns:
        dict: Total amounts as strings.
    """

    # Find first element matching payh belong to accepted name spaces
    total = root.find("cac:LegalMonetaryTotal", Name_Spaces)

    # Per tax category breakdown
    tax_subtotals = []
    for sub in root.findall("cac:TaxTotal/cac:TaxSubtotal", Name_Spaces):
        tax_subtotals.append({
            "taxable_amount": _text(sub, "cbc:TaxableAmount"),
            "tax_amount": _text(sub, "cbc:TaxAmount"),
            "category": _text(sub, "cac:TaxCategory/cbc:ID"),
            "percent": _text(sub, "cac:TaxCategory/cbc:Percent"),
            "exemption_reason": _text(sub, "cac:TaxCategory/cbc:TaxExemptionReason")
        })

    result_totals = {
        "line_extension_amount": _text(total, "cbc:LineExtensionAmount"),
        "tax_exclusive_amount": _text(total, "cbc:TaxExclusiveAmount"),
        "tax_inclusive_amount": _text(total, "cbc:TaxInclusiveAmount"),
        "payable_amount": _text(total, "cbc:PayableAmount"),
        "tax_amount": _text(root, "cac:TaxTotal/cbc:TaxAmount"),
        "prepaid_amount": _text(total, "cbc:PrepaidAmount"),
        "payable_rounding_amount": _text(total, "cbc:PayableRoundingAmount"),
        "allowance_total_amount": _text(total, "cbc:AllowanceTotalAmount"),
        "charge_total_amount": _text(total, "cbc:ChargeTotalAmount"),
        "tax_subtotals": tax_subtotals
    }

    return result_totals


def _line(line_el):
    """
    Extract one invoice line (one purchased item/service).

    Args:
        line_el (Element): A single "cac:InvoiceLine" element.

    Returns:
        dict: Line fields with corresponding values

    Example:
        <cbc:InvoicedQuantity unitCode="EA">1.00</cbc:InvoicedQuantity>
        -> "quantity": "1.00", "unit_code": "EA"
    """

    # Quantity element is needed directly since unitCode is an attribute, not text
    qty = line_el.find("cbc:InvoicedQuantity", Name_Spaces)

    allowances = []
    for ac in line_el.findall("cac:AllowanceCharge", Name_Spaces):
        allowances.append({
            # "true" = extra charge, "false" = discount
            "is_charge": _text(ac, "cbc:ChargeIndicator"),
            "reason": _text(ac, "cbc:AllowanceChargeReason"),
            "percent": _text(ac, "cbc:MultiplierFactorNumeric"),
            "amount": _text(ac, "cbc:Amount"),
            "base_amount": _text(ac, "cbc:BaseAmount")
        })

    result_line = {
        "line_id": _text(line_el, "cbc:ID"),
        "item_name": _text(line_el, "cac:Item/cbc:Name"),
        "description": _text(line_el, "cac:Item/cbc:Description"),
        "quantity": _text(line_el, "cbc:InvoicedQuantity"),
        "unit_code": qty.get("unitCode") if qty is not None else None,
        "unit_price": _text(line_el, "cac:Price/cbc:PriceAmount"),
        "amount": _text(line_el, "cbc:LineExtensionAmount"),
        "tax_category": _text(line_el, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID"),
        "tax_percent": _text(line_el, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"),
        "allowances": allowances
    }
    return result_line


def _delivery(root):
    """
    Extract delivery info: when and where the goods/services were delivered.

    The delivery address and party name often identify the receiving UiT
    unit (e.g. "UIT-Norges arktiske universitet trykkeriet"), which is a
    strong signal for inferring the department.

    Args:
        root (Element): The root element "Invoice" of xml tree.

    Returns:
        dict | None: Delivery fields, or None if the invoice has no
                     "cac:Delivery" block.
    """

    delivery = root.find("cac:Delivery", Name_Spaces)
    if delivery is None:
        return None

    address = "cac:DeliveryLocation/cac:Address/"
    result_delivery = {
        "date": _text(delivery, "cbc:ActualDeliveryDate"),
        "party_name": _text(delivery, "cac:DeliveryParty/cac:PartyName/cbc:Name"),
        "location_id": _text(delivery, "cac:DeliveryLocation/cbc:ID"),
        "street": _text(delivery, address + "cbc:StreetName"),
        "additional_street": _text(delivery, address + "cbc:AdditionalStreetName"),
        "city": _text(delivery, address + "cbc:CityName"),
        "postal_zone": _text(delivery, address + "cbc:PostalZone"),
        "country": _text(delivery, address + "cac:Country/cbc:IdentificationCode")
    }

    return result_delivery


def _attachments(root):
    """
    Extract metadata for documents attached to the invoice. Usually these
    are binary strings (base64)
    
    Args:
        root (Element): The root element "Invoice" of xml tree.

    Returns:
        list[dict]: One dict per "cac:AdditionalDocumentReference" block.

    Example:
        <cbc:EmbeddedDocumentBinaryObject filename="faktura.pdf" mimeCode="application/pdf">
        -> {"doc_id": "6156704", "filename": "faktura.pdf", "mime_code": "application/pdf"}
    """

    result = []
    for ref in root.findall("cac:AdditionalDocumentReference", Name_Spaces):
        blob = ref.find("cac:Attachment/cbc:EmbeddedDocumentBinaryObject", Name_Spaces)
        info_block = {
            "doc_id": _text(ref, "cbc:ID"),
            "description": _text(ref, "cbc:DocumentDescription"),
            "filename": blob.get("filename") if blob is not None else None,
            "mime_code": blob.get("mimeCode") if blob is not None else None
        }
        result.append(info_block)

    return result


def parse_invoice_xml(path):
    """Parse one UBL invoice XML file into a JSON-serializable dict."""
    root = ET.parse(path).getroot()
    return {
        "source_file": Path(path).name,
        **_header(root),
        "supplier": _party(root, "AccountingSupplierParty"),
        "customer": _party(root, "AccountingCustomerParty"),
        "delivery": _delivery(root),
        "payment": _payment(root),
        "totals": _totals(root),
        "lines": [_line(el) for el in root.findall("cac:InvoiceLine", Name_Spaces)],
        "attachments": _attachments(root)
    }


# if __name__ == "__main__":
#     data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices" / "Faktura 41481.xml"
#     print("\n--------\n")
#     print(f"data_dir = {data_dir}")
#     print("\n--------\n")

#     X = parse_invoice_xml(data_dir)

#     for x in X.items():
#         print(x)


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    
    print(data_dir)

    invoices = {f.name: parse_invoice_xml(f) for f in sorted(data_dir.glob("*.xml"))}
    for name, inv in invoices.items():
        print(f"===== {name} =====")
        print(json.dumps(inv, indent=2, ensure_ascii=False))
        break
