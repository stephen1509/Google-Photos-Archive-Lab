from __future__ import annotations
import xml.etree.ElementTree as ET
from .writepolicy import MetadataPlan

NS={
    'x':'adobe:ns:meta/',
    'rdf':'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'photoshop':'http://ns.adobe.com/photoshop/1.0/',
    'dc':'http://purl.org/dc/elements/1.1/',
    'exif':'http://ns.adobe.com/exif/1.0/',
}
for p,u in NS.items():ET.register_namespace(p,u)


def render_xmp_sidecar(plan:MetadataPlan)->bytes|None:
    """Render only portable, standards-backed XMP fields.

    Google-specific/raw provenance deliberately stays in GPA archival JSON, not XMP.
    """
    date=plan.xmp.get('XMP-photoshop:DateCreated')
    desc=plan.xmp.get('dc:description')
    lat=plan.xmp.get('XMP-exif:GPSLatitude');lon=plan.xmp.get('XMP-exif:GPSLongitude')
    if not date and not desc and not lat and not lon:return None
    root=ET.Element(f"{{{NS['x']}}}xmpmeta")
    rdf=ET.SubElement(root,f"{{{NS['rdf']}}}RDF")
    d=ET.SubElement(rdf,f"{{{NS['rdf']}}}Description",{f"{{{NS['rdf']}}}about":''})
    if date:d.set(f"{{{NS['photoshop']}}}DateCreated",str(date))
    if lat:d.set(f"{{{NS['exif']}}}GPSLatitude",str(lat))
    if lon:d.set(f"{{{NS['exif']}}}GPSLongitude",str(lon))
    if desc:
        e=ET.SubElement(d,f"{{{NS['dc']}}}description")
        alt=ET.SubElement(e,f"{{{NS['rdf']}}}Alt")
        li=ET.SubElement(alt,f"{{{NS['rdf']}}}li",{'{http://www.w3.org/XML/1998/namespace}lang':'x-default'})
        li.text=str(desc)
    xml=ET.tostring(root,encoding='utf-8',xml_declaration=True,short_empty_elements=True)
    return xml+b'\n'
