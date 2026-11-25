"""Regenerate tests/fixtures/report_50110_word_label.txt (synthetic Word layout report).

The .docx is minimal: a document.xml with two content controls bound to the report XML part
and one unbound control. Run: python tests/make_word_fixture.py
"""
import base64
import io
import textwrap
import zipfile
from pathlib import Path

DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:sdt><w:sdtPr><w:dataBinding w:prefixMappings="xmlns:ns0='urn:microsoft-dynamics-nav/reports/Etiquette_colis/50110/'" w:xpath="/ns0:NavWordReportXmlPart[1]/ns0:Colis[1]/ns0:Colis_No[1]" w:storeItemID="{00000000-0000-0000-0000-000000000001}"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Colis_No</w:t></w:r></w:p></w:sdtContent></w:sdt>
<w:sdt><w:sdtPr><w:dataBinding w:prefixMappings="xmlns:ns0='urn:microsoft-dynamics-nav/reports/Etiquette_colis/50110/'" w:xpath="/ns0:NavWordReportXmlPart[1]/ns0:Colis[1]/ns0:Colis_Destinataire[1]" w:storeItemID="{00000000-0000-0000-0000-000000000001}"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Colis_Destinataire</w:t></w:r></w:p></w:sdtContent></w:sdt>
<w:sdt><w:sdtPr><w:alias w:val="Libre"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>texte libre</w:t></w:r></w:p></w:sdtContent></w:sdt>
</w:body></w:document>"""

HEADER = """OBJECT Report 50110 Etiquette colis
{
  OBJECT-PROPERTIES
  {
    Date=03.03.20;
    Time=08:00:00;
    Modified=Yes;
    Version List=MED1.00;
  }
  PROPERTIES
  {
    DefaultLayout=Word;
  }
  DATASET
  {
    { 1   ;    ;DataItem;Colis               ;
               DataItemTable=Table50012 }

    { 2   ;1   ;Column  ;Colis_No            ;
               SourceExpr="No." }

  }
  REQUESTPAGE
  {
    PROPERTIES
    {
    }
    CONTROLS
    {
    }
  }
  LABELS
  {
  }
  CODE
  {

    BEGIN
    END.
  }
  WORDLAYOUT
  {
"""


def main() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", DOCUMENT)
    b64 = base64.b64encode(buf.getvalue()).decode()
    body = "\n".join("    " + line for line in textwrap.wrap(b64, 76))
    text = HEADER + body + "\n    END_OF_WORDLAYOUT\n  }\n}\n"
    out = Path(__file__).parent / "fixtures" / "report_50110_word_label.txt"
    out.write_text(text, encoding="utf-8")
    print(f"written {out}")


if __name__ == "__main__":
    main()
