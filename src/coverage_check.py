
import xml.etree.ElementTree as ET
from pathlib import Path


# Every element path that xml_processor.py reads.
EXTRACTED_PATHS = {
    # _header
    "ID",
    "IssueDate",
    "DueDate",
    "InvoiceTypeCode",
    "CreditNoteTypeCode",
    "DocumentCurrencyCode",
    "BuyerReference",
    "AccountingCost",
    "OrderReference/ID",
    "OrderReference/SalesOrderID",
    "ContractDocumentReference/ID",
    "OriginatorDocumentReference/ID",
    "ProjectReference/ID",
    "InvoicePeriod/StartDate",
    "InvoicePeriod/EndDate",
    "Note",
    # _delivery
    "Delivery/ActualDeliveryDate",
    "Delivery/DeliveryParty/PartyName/Name",
    "Delivery/DeliveryLocation/ID",
    "Delivery/DeliveryLocation/Address/StreetName",
    "Delivery/DeliveryLocation/Address/AdditionalStreetName",
    "Delivery/DeliveryLocation/Address/CityName",
    "Delivery/DeliveryLocation/Address/PostalZone",
    "Delivery/DeliveryLocation/Address/Country/IdentificationCode",
    # _payment
    "PaymentMeans/PaymentMeansCode",
    "PaymentMeans/PaymentID",
    "PaymentMeans/PayeeFinancialAccount/ID",
    "PaymentTerms/Note",
    # _totals
    "LegalMonetaryTotal/LineExtensionAmount",
    "LegalMonetaryTotal/TaxExclusiveAmount",
    "LegalMonetaryTotal/TaxInclusiveAmount",
    "LegalMonetaryTotal/PayableAmount",
    "LegalMonetaryTotal/PrepaidAmount",
    "LegalMonetaryTotal/PayableRoundingAmount",
    "LegalMonetaryTotal/AllowanceTotalAmount",
    "LegalMonetaryTotal/ChargeTotalAmount",
    "TaxTotal/TaxAmount",
    "TaxTotal/TaxSubtotal/TaxableAmount",
    "TaxTotal/TaxSubtotal/TaxAmount",
    "TaxTotal/TaxSubtotal/TaxCategory/ID",
    "TaxTotal/TaxSubtotal/TaxCategory/Percent",
    "TaxTotal/TaxSubtotal/TaxCategory/TaxExemptionReason",
    # _attachments
    "AdditionalDocumentReference/ID",
    "AdditionalDocumentReference/DocumentDescription",
}

# _party reads these under both supplier and customer
PARTY_FIELDS = [
    "Party/PartyLegalEntity/RegistrationName",
    "Party/PartyLegalEntity/CompanyID",
    "Party/EndpointID",
    "Party/PartyIdentification/ID",
    "Party/PartyTaxScheme/CompanyID",
    "Party/PartyTaxScheme/TaxScheme/ID",   # read to select the VAT block
    "Party/Contact/Name",
    "Party/Contact/ElectronicMail",
    "Party/Contact/Telephone",
]
for party_name in ["AccountingSupplierParty", "AccountingCustomerParty"]:
    for field in PARTY_FIELDS:
        EXTRACTED_PATHS.add(f"{party_name}/{field}")

# _line reads these; invoice lines and credit note lines differ in name
LINE_FIELDS = [
    "ID",
    "Note",
    "InvoicedQuantity",
    "CreditedQuantity",
    "LineExtensionAmount",
    "Item/Name",
    "Item/Description",
    "Item/ClassifiedTaxCategory/ID",
    "Item/ClassifiedTaxCategory/Percent",
    "Price/PriceAmount",
    "AllowanceCharge/ChargeIndicator",
    "AllowanceCharge/AllowanceChargeReason",
    "AllowanceCharge/MultiplierFactorNumeric",
    "AllowanceCharge/Amount",
    "AllowanceCharge/BaseAmount",
]
for line_name in ["InvoiceLine", "CreditNoteLine"]:
    for field in LINE_FIELDS:
        EXTRACTED_PATHS.add(f"{line_name}/{field}")


# Paths we have SEEN and DECIDED not to extract. Each needs a reason.
IGNORED_PATHS = {
    # Constants, identical on every EHF document
    "CustomizationID",
    "ProfileID",
    "UBLVersionID",
    "InvoiceLine/Item/ClassifiedTaxCategory/TaxScheme/ID",   # always "VAT"
    "CreditNoteLine/Item/ClassifiedTaxCategory/TaxScheme/ID",
    "TaxTotal/TaxSubtotal/TaxCategory/TaxScheme/ID",

    # Duplicates of extracted fields
    "AccountingSupplierParty/Party/PartyName/Name",   # duplicate of RegistrationName
    "AccountingCustomerParty/Party/PartyName/Name",

    # Consciously skipped (low value for gap resolution)
    "TaxPointDate",
    "PaymentMeans/PayeeFinancialAccount/FinancialInstitutionBranch/ID",  # BIC code
    "InvoiceLine/Item/SellersItemIdentification/ID",
    "InvoiceLine/Item/StandardItemIdentification/ID",
    "InvoiceLine/OrderLineReference/LineID",
    "InvoiceLine/InvoicePeriod/StartDate",   # duplicates the document period
    "InvoiceLine/InvoicePeriod/EndDate",
    "InvoiceLine/Price/BaseQuantity",
    "CreditNoteLine/Item/SellersItemIdentification/ID",

    # Amounts are out of scope for the agent (user decision)
    "AllowanceCharge/ChargeIndicator",       # document-level allowance/charge
    "AllowanceCharge/AllowanceChargeReason",
    "AllowanceCharge/Amount",
    "AllowanceCharge/TaxCategory/ID",
    "AllowanceCharge/TaxCategory/Percent",
    "AllowanceCharge/TaxCategory/TaxScheme/ID",
    "InvoiceLine/AccountingCost",            # line-level cost string
}

# Whole subtrees we deliberately skip
IGNORED_PREFIXES = [
    # Party postal addresses: customer side is always DFO's intake address
    "AccountingSupplierParty/Party/PostalAddress/",
    "AccountingCustomerParty/Party/PostalAddress/",
    # The base64 payload of embedded attachments is never decoded
    "AdditionalDocumentReference/Attachment/",
]


def _strip_namespace(tag):
    """
    Remove the "{namespace}" prefix from an element tag.

    Args:
        tag (str): e.g. "{urn:...:CommonBasicComponents-2}ID"

    Returns:
        str: e.g. "ID"
    """

    if "}" in tag:
        return tag.split("}")[-1]
    return tag


def collect_leaf_paths(root):
    """
    Collect every leaf path in the document and one example value.

    A leaf is an element with no children whose text is not empty.
    Paths are relative to the root element.

    Args:
        root (Element): The document root.

    Returns:
        dict: {path: example value}, e.g. {"OrderReference/ID": "46257101"}
    """

    leaves = {}

    def walk(element, prefix):
        for child in element:
            child_name = _strip_namespace(child.tag)
            if prefix == "":
                child_path = child_name
            else:
                child_path = f"{prefix}/{child_name}"

            has_children = len(child) > 0
            text = child.text
            if text is None:
                text = ""
            text = text.strip()

            if not has_children and text != "":
                if child_path not in leaves:
                    leaves[child_path] = text

            walk(child, child_path)

    walk(root, "")
    return leaves


def uncovered_paths(file_path):
    """
    Find the leaf paths in one file that the processor does not cover.

    Args:
        file_path (str | Path): Path to the XML invoice/credit note.

    Returns:
        dict: {path: example value} for every path that is neither
              extracted nor deliberately ignored.
    """

    root = ET.parse(file_path).getroot()
    leaves = collect_leaf_paths(root)

    result = {}
    for path, example in leaves.items():
        if path in EXTRACTED_PATHS:
            continue
        if path in IGNORED_PATHS:
            continue

        ignored_by_prefix = False
        for prefix in IGNORED_PREFIXES:
            if path.startswith(prefix):
                ignored_by_prefix = True
                break
        if ignored_by_prefix:
            continue

        result[path] = example

    return result


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"

    total_uncovered = 0
    for xml_file in sorted(data_dir.glob("*.xml")):
        uncovered = uncovered_paths(xml_file)
        total_uncovered = total_uncovered + len(uncovered)

        print(f"===== {xml_file.name} =====")
        if len(uncovered) == 0:
            print("  full coverage")
        for path, example in uncovered.items():
            shortened_example = example[:60]
            print(f"  {path}   ex: {shortened_example!r}")
        print()

    print(f"total: {total_uncovered} uncovered paths")
