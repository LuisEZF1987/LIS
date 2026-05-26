#!/usr/bin/env python3
"""
SRI Ecuador — XML Digital Signature (XAdES-BES).

Signs SRI electronic documents using a .p12 (PKCS#12) certificate.

Primary approach: signxml library (Python-native).
Fallback: xmlsec1 command-line tool via subprocess.
"""
import logging
import os
import subprocess
import tempfile
from typing import Optional

log = logging.getLogger("sri.signer")


def sign_xml(xml_str: str, cert_path: str, cert_password: str) -> str:
    """
    Sign an XML string using XAdES-BES with a .p12 certificate.

    Parameters:
        xml_str: The XML document to sign (UTF-8 string)
        cert_path: Path to the .p12 / .pfx certificate file
        cert_password: Password for the certificate

    Returns:
        Signed XML string

    Raises:
        RuntimeError: If signing fails with both methods
    """
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Certificate file not found: {cert_path}")

    # Try signxml library first
    try:
        return _sign_with_signxml(xml_str, cert_path, cert_password)
    except ImportError:
        log.info("signxml not available, falling back to xmlsec1 subprocess")
    except Exception as e:
        log.warning("signxml failed: %s, trying xmlsec1 fallback", e)

    # Fallback: xmlsec1 command-line
    try:
        return _sign_with_xmlsec1(xml_str, cert_path, cert_password)
    except FileNotFoundError:
        raise RuntimeError(
            "Neither signxml library nor xmlsec1 command-line tool available. "
            "Install signxml (pip install signxml) or xmlsec1 (apt install xmlsec1)."
        )
    except Exception as e:
        raise RuntimeError(f"XML signing failed: {e}")


def _sign_with_signxml(xml_str: str, cert_path: str, cert_password: str) -> str:
    """Sign using the signxml Python library."""
    from lxml import etree
    from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
    from cryptography.hazmat.backends import default_backend
    import signxml

    # Load .p12 certificate
    with open(cert_path, "rb") as f:
        p12_data = f.read()

    password_bytes = cert_password.encode("utf-8") if cert_password else None
    private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
        p12_data, password_bytes, default_backend()
    )

    if private_key is None or certificate is None:
        raise ValueError("Could not extract key and certificate from .p12 file")

    # Serialize key and cert to PEM
    key_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    cert_pem = certificate.public_bytes(Encoding.PEM)

    # Parse XML
    root = etree.fromstring(xml_str.encode("utf-8"))

    # Sign with enveloped signature (XAdES-BES)
    signer = signxml.XMLSigner(
        method=signxml.methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
    )

    signed_root = signer.sign(
        root,
        key=key_pem,
        cert=cert_pem,
    )

    signed_xml = etree.tostring(
        signed_root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    ).decode("utf-8")

    log.info("XML signed successfully using signxml library")
    return signed_xml


def _sign_with_xmlsec1(xml_str: str, cert_path: str, cert_password: str) -> str:
    """Sign using xmlsec1 command-line tool as fallback."""
    # Verify xmlsec1 is available
    try:
        subprocess.run(
            ["xmlsec1", "--version"],
            capture_output=True, check=True, timeout=10,
        )
    except FileNotFoundError:
        raise FileNotFoundError("xmlsec1 not found in PATH")

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = os.path.join(tmpdir, "document.xml")
        key_path = os.path.join(tmpdir, "key.pem")
        cert_pem_path = os.path.join(tmpdir, "cert.pem")
        signed_path = os.path.join(tmpdir, "signed.xml")

        # Extract key and cert from .p12 using openssl
        _extract_p12(cert_path, cert_password, key_path, cert_pem_path)

        # Add signature template to XML
        xml_with_template = _add_signature_template(xml_str)

        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_with_template)

        # Run xmlsec1
        cmd = [
            "xmlsec1", "--sign",
            "--privkey-pem", f"{key_path},{cert_pem_path}",
            "--output", signed_path,
            xml_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"xmlsec1 signing failed (rc={result.returncode}): {result.stderr}"
            )

        with open(signed_path, "r", encoding="utf-8") as f:
            signed_xml = f.read()

        log.info("XML signed successfully using xmlsec1 command-line")
        return signed_xml


def _extract_p12(
    p12_path: str, password: str,
    key_out: str, cert_out: str,
) -> None:
    """Extract PEM key and cert from a .p12 file using openssl."""
    pass_arg = f"pass:{password}" if password else "pass:"

    # Extract private key
    result = subprocess.run(
        [
            "openssl", "pkcs12", "-in", p12_path,
            "-nocerts", "-nodes",
            "-passin", pass_arg,
            "-out", key_out,
        ],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract private key: {result.stderr}")

    # Extract certificate
    result = subprocess.run(
        [
            "openssl", "pkcs12", "-in", p12_path,
            "-clcerts", "-nokeys",
            "-passin", pass_arg,
            "-out", cert_out,
        ],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract certificate: {result.stderr}")


def _add_signature_template(xml_str: str) -> str:
    """
    Add an XML Signature template for xmlsec1 to fill in.
    This inserts a <ds:Signature> placeholder in the root element.
    """
    from lxml import etree

    DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
    DS = "{%s}" % DSIG_NS
    nsmap = {"ds": DSIG_NS}

    root = etree.fromstring(xml_str.encode("utf-8"))

    # Build Signature template
    sig = etree.SubElement(root, f"{DS}Signature", nsmap=nsmap, Id="Signature")

    signed_info = etree.SubElement(sig, f"{DS}SignedInfo")
    c14n = etree.SubElement(signed_info, f"{DS}CanonicalizationMethod")
    c14n.set("Algorithm", "http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    sig_method = etree.SubElement(signed_info, f"{DS}SignatureMethod")
    sig_method.set("Algorithm", "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")

    ref = etree.SubElement(signed_info, f"{DS}Reference", URI="")
    transforms = etree.SubElement(ref, f"{DS}Transforms")
    t = etree.SubElement(transforms, f"{DS}Transform")
    t.set("Algorithm", "http://www.w3.org/2000/09/xmldsig#enveloped-signature")
    digest_method = etree.SubElement(ref, f"{DS}DigestMethod")
    digest_method.set("Algorithm", "http://www.w3.org/2001/04/xmlenc#sha256")
    etree.SubElement(ref, f"{DS}DigestValue")

    etree.SubElement(sig, f"{DS}SignatureValue")

    key_info = etree.SubElement(sig, f"{DS}KeyInfo")
    x509_data = etree.SubElement(key_info, f"{DS}X509Data")
    etree.SubElement(x509_data, f"{DS}X509Certificate")

    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    ).decode("utf-8")


def verify_p12(cert_path: str, cert_password: str) -> Optional[dict]:
    """
    Verify a .p12 certificate and return basic info.
    Returns None if invalid.
    """
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.backends import default_backend

        with open(cert_path, "rb") as f:
            p12_data = f.read()

        password_bytes = cert_password.encode("utf-8") if cert_password else None
        private_key, certificate, _ = pkcs12.load_key_and_certificates(
            p12_data, password_bytes, default_backend()
        )

        if certificate is None:
            return None

        return {
            "subject": str(certificate.subject),
            "issuer": str(certificate.issuer),
            "not_valid_before": certificate.not_valid_before_utc.isoformat(),
            "not_valid_after": certificate.not_valid_after_utc.isoformat(),
            "serial_number": str(certificate.serial_number),
        }
    except Exception as e:
        log.warning("Failed to verify .p12 certificate: %s", e)
        return None
